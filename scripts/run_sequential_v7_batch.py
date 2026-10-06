"""Run at most three qualified four-seat games, one reviewed launcher at a time.

This is a spend/cursor wrapper, not a game runner. Every game still goes through
run_two_luna_binding_game.py and Argentum's native FFA lifecycle. An interrupted
game is never replayed automatically, even when its launcher log looks complete.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from commander_gym.cache_probe_session import _durable_mkdir, _fsync_dir, _private_json
from commander_gym.openai_run_budget import OpenAIRunBudget
from commander_gym.qualified_v7_preflight import (
    BINDING_FINGERPRINTS, verify_qualified_v7_catalog,
)


class BatchError(RuntimeError):
    pass


BATCH_USD = 10.0
GAME_COUNT = 3
PROFILES = tuple(BINDING_FINGERPRINTS)
OLD_REQUEST_LIMIT = 2212
NEW_REQUEST_LIMIT = 2500


def _head(directory: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(directory), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


def _tracked_clean(directory: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(directory), "status", "--porcelain", "--untracked-files=no"],
        check=True, capture_output=True, text=True,
    )
    return not result.stdout.strip()


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_cursor(path: Path, value: Mapping[str, Any]) -> None:
    encoded = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    fd, temporary = tempfile.mkstemp(prefix=".cursor-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_dir(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _private_dir(path: Path) -> None:
    _durable_mkdir(path)
    if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise BatchError("batch directory must be private and cannot be a symlink")


def _snapshot(ledger: Path, cap: float, max_requests: int) -> dict[str, Any]:
    return OpenAIRunBudget(
        ledger, cap, authorized_max_usd=max(cap, BATCH_USD),
        max_requests=max_requests,
    ).snapshot()


def _current_ledger(ledger: Path, old_cap: float, new_cap: float,
                    old_requests: int, new_requests: int) -> dict[str, Any]:
    if not ledger.is_file():
        raise BatchError("existing cumulative ledger is missing")
    try:
        cap = json.loads(ledger.read_text())["capUsd"]
    except (ValueError, KeyError) as exc:
        raise BatchError("existing cumulative ledger is malformed") from exc
    if cap not in (old_cap, new_cap):
        raise BatchError("cumulative ledger cap changed outside this batch")
    try:
        limit = json.loads(ledger.read_text())["maxRequests"]
    except (ValueError, KeyError) as exc:
        raise BatchError("existing cumulative request limit is malformed") from exc
    if limit not in (old_requests, new_requests):
        raise BatchError("cumulative request limit changed outside this batch")
    return _snapshot(ledger, cap, limit)


def _policy_attempts(path: Path) -> tuple[int, int, str]:
    if not path.is_file() or path.is_symlink():
        raise BatchError("per-game policy provenance is missing")
    count = callbacks = 0
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for line in source:
            digest.update(line)
            if not line.endswith(b"\n"):
                raise BatchError("policy provenance has a partial final row")
            record = json.loads(line)
            if not isinstance(record, dict):
                raise BatchError("policy provenance contains a non-object row")
            callbacks += 1
            choice = record.get("choice")
            metadata = choice.get("metadata") if isinstance(choice, dict) else None
            if isinstance(metadata, dict) and metadata.get("provider") == "openai":
                retries = metadata.get("retryCount")
                if type(retries) is not int or retries < 0:
                    raise BatchError("provider provenance has an invalid retry count")
                model_io = metadata.get("modelIo")
                if (not isinstance(model_io, dict)
                    or not isinstance(model_io.get("attempts"), list)
                    or not model_io["attempts"]):
                    raise BatchError("provider attempt receipt count is inconsistent")
                attempts = model_io["attempts"]
                transport_errors = sum(
                    isinstance(attempt, dict)
                    and isinstance(attempt.get("response"), dict)
                    and isinstance(attempt["response"].get("transportError"), str)
                    for attempt in attempts
                )
                validation_errors = sum(
                    isinstance(attempt, dict)
                    and isinstance(attempt.get("response"), dict)
                    and "validationError" in attempt["response"]
                    for attempt in attempts
                )
                failed = choice.get("channel") == "error"
                final_response = attempts[-1].get("response") if isinstance(attempts[-1], dict) else None
                if (failed and (model_io.get("selectedAttempt") is not None
                    or validation_errors != retries
                    or len(attempts) != retries + transport_errors + sum(
                        isinstance(attempt, dict)
                        and isinstance(attempt.get("response"), dict)
                        and "budgetError" in attempt["response"]
                        for attempt in attempts
                    )
                    or not isinstance(final_response, dict)
                    or not ("transportError" in final_response
                            or "budgetError" in final_response
                            or "validationError" in final_response))
                    or not failed and (len(attempts) != retries + transport_errors + 1
                        or transport_errors and (
                            not isinstance(final_response, dict)
                            or "transportError" in final_response
                        ))):
                    raise BatchError("provider attempt receipt count is inconsistent")
                count += len(attempts)
    return count, callbacks, digest.hexdigest()


def _group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


def _stop_and_verify_group(child: subprocess.Popen[Any]) -> None:
    """Hold the caller's runtime lock until every game-group member is gone."""
    pgid = child.pid
    if _group_exists(pgid):
        os.killpg(pgid, signal.SIGTERM)
    try:
        child.wait(timeout=60)
    except subprocess.TimeoutExpired:
        while child.poll() is None:
            if _group_exists(pgid):
                os.killpg(pgid, signal.SIGKILL)
            try:
                child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                continue
    while _group_exists(pgid):
        os.killpg(pgid, signal.SIGKILL)
        time.sleep(0.1)


def _launcher_lines(path: Path) -> tuple[dict[str, Any] | None, dict[str, Any] | None, Path | None]:
    preflight = result = None
    artifact = None
    with path.open(encoding="utf-8", errors="replace") as source:
        for line in source:
            if line.startswith("TWO_LUNA_QUALIFICATION_PREFLIGHT "):
                if preflight is not None:
                    raise BatchError("launcher emitted duplicate qualification preflight")
                preflight = json.loads(line.split(" ", 1)[1])
            elif line.startswith("TWO_LUNA_DEBUG_RESULT="):
                if result is not None:
                    raise BatchError("launcher emitted duplicate game result")
                result = json.loads(line.split("=", 1)[1])
            elif line.startswith("RUN_ARTIFACTS="):
                if artifact is not None:
                    raise BatchError("launcher emitted duplicate artifact directory")
                artifact = Path(line.split("=", 1)[1].strip())
    return preflight, result, artifact


def _verify_game(
    *, index: int, launcher_code: int, log: Path, output_dir: Path,
    before: Mapping[str, Any], after: Mapping[str, Any], manifest: Mapping[str, Any],
) -> dict[str, Any]:
    reasons: list[str] = []
    preflight, result, artifact = _launcher_lines(log)
    if preflight != manifest["qualificationPreflight"]:
        reasons.append("qualification preflight missing or changed")
    if result is None:
        reasons.append("game result missing")
    if artifact is None or not artifact.is_dir() or not artifact.resolve().is_relative_to(output_dir.resolve()):
        reasons.append("private game artifact directory missing or outside this game")
        artifact = None
    request_delta = after["requests"] - before["requests"]
    if (after["capUsd"] != manifest["cumulativeCapUsd"]
        or after.get("maxRequests") != manifest["maxRequests"]
        or after["estimatedUsd"] > manifest["cumulativeCapUsd"] + 1e-7
        or after["requests"] > manifest["maxRequests"]):
        reasons.append("shared budget limit changed or exceeded")
    if (after["unsettledRequests"] != before["unsettledRequests"]
        or after["unsettledRequests"] != manifest["startLedger"]["unsettledRequests"]):
        reasons.append("new provider request remained unsettled")
    if request_delta < 0:
        reasons.append("shared ledger request count decreased")
    if result is not None:
        budget = result.get("budget") or {}
        if any(value != request_delta for value in (
            result.get("providerRequests"), result.get("recordedGameApiRequests"),
            budget.get("gameApiRequests"),
        )):
            reasons.append("launcher request counts disagree with shared ledger")
        for field, key in (("inputTokens", "gameInputTokens"), ("outputTokens", "gameOutputTokens")):
            if budget.get(key) != after[field] - before[field]:
                reasons.append(f"launcher {field} disagrees with shared ledger")
        if (budget.get("cumulativeApiRequests") != after["requests"]
            or budget.get("unsettledRequests") != after["unsettledRequests"]
            or budget.get("capUsd") != manifest["cumulativeCapUsd"]):
            reasons.append("launcher cumulative budget disagrees with shared ledger")
        if (result.get("provenanceFinalizationComplete") is not True
            or result.get("fourSeatProvenanceComplete") is not True):
            reasons.append("four-seat policy provenance was not finalized")
        if (result.get("expectedSeats") != 4 or result.get("singlePodGame") is not True
            or result.get("completed") is not True or result.get("stopReason") != "native_complete"
            or not isinstance(result.get("terminalEvidence"), dict)
            or result["terminalEvidence"].get("nativeGameOver") is not True):
            reasons.append("native four-seat game did not complete normally")
        terminal = result.get("terminalArtifact")
        if not isinstance(terminal, dict) or terminal.get("nativeGameOver") is not True:
            reasons.append("native terminal replay proof is missing")
        if artifact is not None:
            try:
                attempts, callbacks, policy_sha = _policy_attempts(artifact / "policy.jsonl")
                if attempts != request_delta:
                    reasons.append("policy attempt count disagrees with shared ledger")
            except (BatchError, ValueError) as exc:
                reasons.append(str(exc))
                attempts = callbacks = 0
                policy_sha = None
            for filename, key in (("terminal-replay.json", "replaySha256"),
                                  ("terminal-state.json", "terminalStateSha256")):
                if (not isinstance(terminal, dict) or not (artifact / filename).is_file()
                    or _sha(artifact / filename) != terminal.get(key)):
                    reasons.append(f"{filename} hash disagrees with native result")
        else:
            attempts = callbacks = 0
            policy_sha = None
        if result.get("validationRetries") != 0:
            reasons.append("recovered retry: natural completion is not strict zero-retry qualification")
        if result.get("providerTransportErrors", 0) != 0:
            reasons.append("provider transport error occurred during game")
        if result.get("technicalQualified") is not True or launcher_code != 0:
            reasons.append("launcher did not pass strict qualification")
    else:
        attempts = callbacks = 0
        policy_sha = None
    return {
        "index": index, "launcherExitCode": launcher_code,
        "launcherLogSha256": _sha(log),
        "artifactDir": str(artifact) if artifact else None,
        "policySha256": policy_sha, "policyCallbacks": callbacks,
        "providerAttempts": attempts,
        "beforeLedger": dict(before), "afterLedger": dict(after),
        "nativeCompleted": bool(result and result.get("completed") is True
                                and isinstance(result.get("terminalEvidence"), dict)
                                and result["terminalEvidence"].get("nativeGameOver") is True),
        "validationRetries": result.get("validationRetries") if result else None,
        "qualified": not reasons, "stopReasons": reasons,
        "result": result,
    }


def launch_one_game(args: argparse.Namespace, index: int, log: Path, output_dir: Path,
                    expected_requests: int, expected_unsettled: int,
                    cumulative_cap: float) -> int:
    gym_root = getattr(args, "gym_dir", Path(__file__).resolve().parent.parent)
    command = [
        str(args.python), "-m", "scripts.run_two_luna_binding_game",
        "--engine-dir", str(args.engine_dir),
        "--instance-root", str(args.instance_root),
        "--catalog", str(args.catalog),
        "--output-dir", str(output_dir),
        "--api-key-file", str(args.api_key_file),
        "--python", str(args.python),
        "--budget-ledger", str(args.budget_ledger),
        "--budget-cap", str(cumulative_cap),
        "--budget-authorized-max", str(cumulative_cap),
        "--budget-max-requests", str(args.max_requests),
        "--expected-ledger-requests", str(expected_requests),
        "--expected-unsettled-requests", str(expected_unsettled),
        "--expected-ledger-estimated-usd", str(args.before_estimated_usd),
        "--max-attempts", "2",
        "--qualified-v7-comparison",
        "--supervised-batch-process-group",
        "--timeout", str(args.timeout),
        "--stall-seconds", str(args.stall_seconds),
        *(item for profile in PROFILES for item in ("--profile", profile)),
    ]
    environment = dict(os.environ)
    # An inherited experiment flag must not silently change the canonical run.
    environment["COMMANDER_GYM_CACHE_FRIENDLY_HISTORY"] = "false"
    if hasattr(args, "gym_dir"):
        # The continuation controller lives in a different checkout. Resolve the
        # game module and imports only from the original qualified Gym checkout.
        environment["PYTHONPATH"] = str(gym_root)
    stop_requested = False
    def interrupted(_signum: int, _frame: Any) -> None:
        nonlocal stop_requested
        stop_requested = True

    with log.open("x", encoding="utf-8") as stream:
        os.chmod(log, 0o600)
        previous_term = signal.signal(signal.SIGTERM, interrupted)
        previous_int = signal.signal(signal.SIGINT, interrupted)
        child = None
        exit_code = None
        try:
            child = subprocess.Popen(
                command, stdout=stream, stderr=subprocess.STDOUT,
                env=environment, cwd=gym_root, start_new_session=True,
            )
            while not stop_requested:
                try:
                    exit_code = child.wait(timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    continue
        finally:
            try:
                if child is not None:
                    _stop_and_verify_group(child)
                stream.flush()
                os.fsync(stream.fileno())
                _fsync_dir(log.parent)
            finally:
                signal.signal(signal.SIGTERM, previous_term)
                signal.signal(signal.SIGINT, previous_int)
    if stop_requested:
        raise InterruptedError("batch launcher received a termination signal")
    if exit_code is None:
        raise BatchError("game launcher exited without a status")
    return exit_code


def _identity(args: argparse.Namespace) -> dict[str, Any]:
    gym_root = getattr(args, "gym_dir", Path(__file__).resolve().parent.parent)
    if _head(gym_root) != args.expected_gym_head or _head(args.engine_dir) != args.expected_engine_head:
        raise BatchError("Gym or engine source head differs from the reviewed pin")
    if not _tracked_clean(gym_root) or not _tracked_clean(args.engine_dir):
        raise BatchError("Gym or engine tracked source differs from its reviewed head")
    qualified = verify_qualified_v7_catalog(
        args.instance_root, args.catalog, PROFILES[0], PROFILES[1], 2,
    )
    if tuple(BINDING_FINGERPRINTS) != (
        "krenko-forge-declarative-continuation-v7-openai",
        "talrand-forge-declarative-continuation-v7-openai",
        "sythis-forge-declarative-continuation-v7-openai",
        "lathril-forge-declarative-continuation-v7-openai",
    ):
        raise BatchError("qualified seat order changed")
    return {
        "schemaVersion": 1,
        "gymHead": args.expected_gym_head, "engineHead": args.expected_engine_head,
        "gymRoot": str(gym_root), "engineDir": str(args.engine_dir),
        "instanceRoot": str(args.instance_root), "catalog": str(args.catalog),
        "catalogSha256": _sha(args.catalog), "python": str(args.python),
        "apiKeyFile": str(args.api_key_file), "ledger": str(args.budget_ledger),
        "runtimeLock": str(args.runtime_lock),
        "profiles": list(PROFILES), "model": "gpt-6-luna",
        "cacheFriendlyHistory": False, "maxAttempts": 2,
        "timeout": args.timeout, "stallSeconds": args.stall_seconds,
        "gameCount": GAME_COUNT, "incrementalCapUsd": BATCH_USD,
        "maxRequests": args.max_requests, "qualificationPreflight": qualified,
    }


def run_batch(
    args: argparse.Namespace,
    *, runner: Callable[[argparse.Namespace, int, Path, Path, int, int, float], int] = launch_one_game,
) -> dict[str, Any]:
    for path in (args.batch_dir, args.engine_dir, args.instance_root, args.catalog,
                 args.python, args.api_key_file, args.budget_ledger, args.runtime_lock):
        if not path.is_absolute():
            raise BatchError("all batch paths must be absolute")
    if (args.max_requests != NEW_REQUEST_LIMIT or args.expected_start_requests != 1732
        or args.expected_start_unsettled != 3
        or args.expected_start_usd != 14.870275991999986
        or args.existing_cap_usd != 18.0):
        raise BatchError("qualified starting ledger or existing request limit changed")
    if args.timeout <= 0 or args.stall_seconds <= 0:
        raise BatchError("game timeout and stall limit must be positive")
    if not args.python.is_file() or not os.access(args.python, os.X_OK):
        raise BatchError("selected Python venv is unavailable")
    if not args.api_key_file.is_file() or not args.budget_ledger.is_file():
        raise BatchError("authorized key file or existing ledger is unavailable")
    os.umask(0o077)
    _private_dir(args.batch_dir)
    if not args.runtime_lock.parent.is_dir():
        raise BatchError("runtime lock parent does not exist")
    if args.runtime_lock.is_symlink():
        raise BatchError("runtime lock cannot be a symlink")
    lock_fd = os.open(args.runtime_lock, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if stat.S_IMODE(os.fstat(lock_fd).st_mode) & 0o077:
            raise BatchError("runtime lock must be private")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BatchError("another process owns the shared game runtime") from exc
        identity = _identity(args)
        manifest_path = args.batch_dir / "manifest.json"
        cursor_path = args.batch_dir / "cursor.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text())
            if any(manifest.get(key) != value for key, value in identity.items()):
                raise BatchError("frozen batch identity changed on resume")
            if not cursor_path.is_file():
                raise BatchError("batch manifest lacks a durable cursor")
        else:
            if any(args.batch_dir.iterdir()):
                raise BatchError("batch evidence exists without a frozen manifest")
            start = _snapshot(args.budget_ledger, args.existing_cap_usd, OLD_REQUEST_LIMIT)
            if (start["requests"] != args.expected_start_requests
                or start["unsettledRequests"] != args.expected_start_unsettled
                or start["estimatedUsd"] != args.expected_start_usd
                or start["capUsd"] != args.existing_cap_usd
                or start.get("maxRequests") != OLD_REQUEST_LIMIT):
                raise BatchError("historic cumulative ledger differs from authorized start")
            manifest = {**identity, "startLedger": start,
                        "existingCapUsd": args.existing_cap_usd,
                        "cumulativeCapUsd": start["estimatedUsd"] + BATCH_USD}
            _private_json(manifest_path, manifest)
            _write_cursor(cursor_path, {"status": "ready", "nextGame": 1})
        cursor = json.loads(cursor_path.read_text())
        if cursor.get("status") != "ready" or type(cursor.get("nextGame")) is not int:
            raise BatchError("batch has a stopped or ambiguous in-flight game; no automatic replay")
        next_game = cursor["nextGame"]
        if not 1 <= next_game <= GAME_COUNT + 1:
            raise BatchError("batch cursor is out of range")
        for completed in range(1, next_game):
            receipt_path = args.batch_dir / f"game-{completed:02d}-receipt.json"
            if not receipt_path.is_file() or json.loads(receipt_path.read_text()).get("qualified") is not True:
                raise BatchError("prior game receipt is missing or unqualified")
        after_previous = (manifest["startLedger"] if next_game == 1 else json.loads(
            (args.batch_dir / f"game-{next_game - 1:02d}-receipt.json").read_text()
        )["afterLedger"])
        current = _current_ledger(args.budget_ledger, args.existing_cap_usd,
                                  manifest["cumulativeCapUsd"], OLD_REQUEST_LIMIT,
                                  args.max_requests)
        for key in ("requests", "inputTokens", "outputTokens", "unsettledRequests", "estimatedUsd"):
            if current[key] != after_previous[key]:
                raise BatchError("cumulative ledger changed outside completed batch games")
        if current["maxRequests"] == OLD_REQUEST_LIMIT:
            if next_game != 1:
                raise BatchError("batch request limit reverted after a game")
            budget = OpenAIRunBudget(
                args.budget_ledger, current["capUsd"],
                authorized_max_usd=manifest["cumulativeCapUsd"],
                max_requests=OLD_REQUEST_LIMIT,
            )
            current = budget.set_request_limit(args.max_requests)
        if current["capUsd"] == args.existing_cap_usd:
            if next_game != 1:
                raise BatchError("batch cap reverted after a game")
            budget = OpenAIRunBudget(
                args.budget_ledger, args.existing_cap_usd,
                authorized_max_usd=manifest["cumulativeCapUsd"],
                max_requests=args.max_requests,
            )
            budget.increase_cap(manifest["cumulativeCapUsd"])
            current = budget.snapshot()
        if current["capUsd"] != manifest["cumulativeCapUsd"]:
            raise BatchError("batch cumulative cap is not installed")
        while next_game <= GAME_COUNT:
            if _identity(args) != identity:
                raise BatchError("reviewed source or catalog identity changed before next game")
            before = _snapshot(args.budget_ledger, manifest["cumulativeCapUsd"], args.max_requests)
            if before != current:
                raise BatchError("cumulative ledger changed before next game")
            if (before["requests"] >= args.max_requests
                or before["estimatedUsd"] >= manifest["cumulativeCapUsd"]):
                _write_cursor(cursor_path, {"status": "stopped", "nextGame": next_game,
                                           "reason": "batch budget or request cap reached"})
                break
            output_dir = args.batch_dir / f"game-{next_game:02d}"
            log = args.batch_dir / f"game-{next_game:02d}-launcher.log"
            if output_dir.exists() or log.exists():
                raise BatchError("next game already has artifacts; refusing replay")
            _write_cursor(cursor_path, {"status": "inflight", "nextGame": next_game,
                                       "beforeLedger": before})
            args.before_estimated_usd = before["estimatedUsd"]
            code = runner(args, next_game, log, output_dir,
                          before["requests"], before["unsettledRequests"],
                          manifest["cumulativeCapUsd"])
            after = _snapshot(args.budget_ledger, manifest["cumulativeCapUsd"], args.max_requests)
            receipt = _verify_game(
                index=next_game, launcher_code=code, log=log, output_dir=output_dir,
                before=before, after=after, manifest=manifest,
            )
            _private_json(args.batch_dir / f"game-{next_game:02d}-receipt.json", receipt)
            if not receipt["qualified"]:
                _write_cursor(cursor_path, {"status": "stopped", "nextGame": next_game,
                                           "reason": receipt["stopReasons"]})
                break
            next_game += 1
            current = after
            _write_cursor(cursor_path, {"status": "ready", "nextGame": next_game})
        return json.loads(cursor_path.read_text())
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("batch-dir", "engine-dir", "instance-root", "catalog", "python",
                 "api-key-file", "budget-ledger", "runtime-lock"):
        p.add_argument("--" + name, required=True, type=Path)
    p.add_argument("--expected-gym-head", required=True)
    p.add_argument("--expected-engine-head", required=True)
    p.add_argument("--existing-cap-usd", type=float, default=18.0)
    p.add_argument("--max-requests", type=int, default=NEW_REQUEST_LIMIT)
    p.add_argument("--expected-start-requests", type=int, default=1732)
    p.add_argument("--expected-start-unsettled", type=int, default=3)
    p.add_argument("--expected-start-usd", type=float, default=14.870275991999986)
    p.add_argument("--timeout", type=float, default=3600)
    p.add_argument("--stall-seconds", type=float, default=600)
    p.add_argument("--execute", action="store_true", help="allow paid game dispatch after review")
    return p


def main() -> int:
    args = parser().parse_args()
    if not args.execute:
        raise SystemExit("--execute is required; no game was started")
    result = run_batch(args)
    print(json.dumps(result, sort_keys=True))
    return 0 if result == {"status": "ready", "nextGame": GAME_COUNT + 1} else 1


if __name__ == "__main__":
    raise SystemExit(main())
