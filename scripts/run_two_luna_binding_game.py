"""Start isolated Argentum/Binding services and run one capped headless match or pod."""
from __future__ import annotations
import argparse
from contextlib import nullcontext
import math
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import stat
import subprocess
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from commander_gym.openai_run_budget import OpenAIRunBudget
from commander_gym.experimental_prefix import exclusive_runtime_lock
from commander_gym.experimental_wait_preflight import (
    PROFILES as WAIT_PROFILES, stage_experimental_wait_catalog,
)
from commander_gym.two_luna_debug import _write_private_json, _finalize_provenance
from commander_gym.cache_probe_session import _durable_mkdir
from scripts.run_sequential_v7_batch import _stop_and_verify_group
from commander_gym.qualified_v7_preflight import (
    BINDING_FINGERPRINTS, SELECTED_PROFILES, stage_qualified_v7_catalog,
)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def read_key(path):
    for line in path.read_text().splitlines():
        line = line.strip().removeprefix("export ")
        if line.startswith("OPENAI_API_KEY="):
            key = line.split("=", 1)[1].strip().strip("\"'")
            if key:
                return key
    raise RuntimeError("API key file has no OPENAI_API_KEY entry")


def checked_existing_budget(ledger, cap, authorized_max, max_requests,
                            expected_requests, expected_unsettled,
                            expected_estimated_usd=None):
    if not ledger.is_file() or not ledger.read_bytes():
        raise RuntimeError("existing nonempty cumulative budget ledger required")
    snapshot = OpenAIRunBudget(
        ledger, cap, authorized_max_usd=authorized_max, max_requests=max_requests,
    ).snapshot()
    if expected_requests is not None and snapshot["requests"] != expected_requests:
        raise RuntimeError("cumulative ledger request count changed before game launch")
    if expected_unsettled is not None and snapshot["unsettledRequests"] != expected_unsettled:
        raise RuntimeError("cumulative ledger unsettled reservations changed before game launch")
    if (expected_estimated_usd is not None
        and snapshot["estimatedUsd"] != expected_estimated_usd):
        raise RuntimeError("cumulative ledger estimate changed before game launch")
    return snapshot


def selected_python(gym: Path, override: Path | None) -> Path:
    # A venv interpreter is usually a symlink. Resolving it selects the system
    # binary outside the venv and loses its installed OpenAI SDK.
    python = override.absolute() if override else gym / ".venv/bin/python"
    if not python.is_file() or not os.access(python, os.X_OK):
        raise RuntimeError("selected Python executable is unavailable")
    return python


def await_ready(url, process, seconds, token=None):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("local process exited during startup; inspect run log")
        try:
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            with urlopen(Request(url, headers=headers), timeout=0.5):
                return
        except HTTPError as error:
            if error.code == 404 and token is None:
                return
        except Exception:
            time.sleep(0.3)
    raise RuntimeError("local service did not become ready; inspect run log")


def _interrupted(_signum, _frame):
    raise InterruptedError("supervised game launcher received a termination signal")


def main():
    parser = argparse.ArgumentParser()
    for name in ("engine-dir", "instance-root", "catalog", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--api-key-file", type=Path)
    parser.add_argument("--python", type=Path,
                        help="Python executable with the existing OpenAI SDK; defaults to worktree .venv")
    parser.add_argument("--budget-ledger", type=Path,
                        help="existing absolute cumulative ledger; defaults to output-dir ledger")
    parser.add_argument("--budget-cap", type=float, default=5.0)
    parser.add_argument("--budget-authorized-max", type=float, default=5.0)
    parser.add_argument("--budget-max-requests", type=int)
    parser.add_argument("--expected-ledger-requests", type=int)
    parser.add_argument("--expected-unsettled-requests", type=int)
    parser.add_argument("--expected-ledger-estimated-usd", type=float)
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--profile-a")
    parser.add_argument("--profile-b")
    parser.add_argument("--profile", dest="profiles", action="append",
                        help="repeat Krenko, Talrand, Sythis, Lathril for one four-seat pod")
    parser.add_argument("--dry-run", action="store_true", help="reject model dispatch before spend")
    parser.add_argument("--qualified-v7-comparison", action="store_true",
                        help="pin and stage the reviewed v7 Binding/Pilot catalog closure")
    parser.add_argument("--experimental-wait-prefix", action="store_true",
                        help="opt-in bounded efficiency prefix; never v7/full-game qualification")
    parser.add_argument("--session-cap-usd", type=float)
    parser.add_argument("--session-max-requests", type=int)
    parser.add_argument("--runtime-lock", type=Path)
    parser.add_argument("--expected-gym-head")
    parser.add_argument("--expected-engine-head")
    parser.add_argument("--prefix-turn-limit", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=3600)
    parser.add_argument("--stall-seconds", type=float, default=600)
    parser.add_argument("--supervised-batch-process-group", action="store_true",
                        help="keep isolated services in the batch launcher's process group")
    args = parser.parse_args()
    if args.profiles:
        args.profile_a = args.profile_a or args.profiles[0]
        args.profile_b = args.profile_b or (args.profiles[1] if len(args.profiles) > 1 else None)
    elif not args.profile_a or not args.profile_b:
        parser.error("--profile-a and --profile-b are required for a two-seat run")
    if args.profiles and not args.experimental_wait_prefix and (
        tuple(args.profiles[:2]) != SELECTED_PROFILES
        or tuple(args.profiles) != tuple(BINDING_FINGERPRINTS)
        or (args.profile_a, args.profile_b) != SELECTED_PROFILES
    ):
        parser.error("four-seat run requires the exact qualified v7 Binding order")
    if args.profiles and not (args.qualified_v7_comparison or args.experimental_wait_prefix):
        parser.error("four-seat run requires --qualified-v7-comparison")
    if args.timeout <= 0 or args.stall_seconds <= 0 or args.max_attempts <= 0:
        parser.error("timeout and stall-seconds must be positive")
    if args.budget_ledger is not None and not args.budget_ledger.is_absolute():
        parser.error("existing cumulative budget ledger must be absolute")
    if args.budget_cap > 5 and args.budget_max_requests is None:
        parser.error("an elevated cumulative cap requires an absolute request limit")
    if args.supervised_batch_process_group and os.getpgrp() != os.getpid():
        parser.error("supervised batch launcher must be a new process-group leader")
    if not args.dry_run and args.api_key_file is None:
        parser.error("--api-key-file is required for a paid game")
    if not args.dry_run and not (args.qualified_v7_comparison or args.experimental_wait_prefix):
        parser.error("paid two-Luna qualification requires --qualified-v7-comparison")
    if bool(args.session_cap_usd is not None) != bool(args.session_max_requests is not None):
        parser.error("both absolute session budget bounds are required together")
    if args.session_cap_usd is not None and args.budget_ledger is None:
        parser.error("session bounds require an existing cumulative ledger")
    if args.experimental_wait_prefix:
        validate_prefix_args(args, parser)
    with (exclusive_runtime_lock(args.runtime_lock) if args.experimental_wait_prefix else nullcontext()):
        return launch(args, parser)


def validate_prefix_args(args, parser):
    if (args.qualified_v7_comparison or args.dry_run or args.supervised_batch_process_group
        or tuple(args.profiles or ()) != WAIT_PROFILES
        or (args.profile_a, args.profile_b) != WAIT_PROFILES[:2]
        or args.budget_ledger is None or args.runtime_lock is None
        or not args.expected_gym_head or not args.expected_engine_head
        or args.expected_ledger_requests is None or args.expected_unsettled_requests is None
        or args.expected_ledger_estimated_usd is None
        or args.session_cap_usd is None or args.session_max_requests is None):
        parser.error("experimental prefix requires isolated four-seat pins, ledger snapshot, lock and session bounds")
    if (not all(math.isfinite(v) for v in (args.timeout, args.budget_cap, args.budget_authorized_max,
                                           args.expected_ledger_estimated_usd, args.session_cap_usd))
        or not 0 < args.timeout <= 900 or not 1 <= args.prefix_turn_limit <= 8
        or args.max_attempts != 2 or args.budget_max_requests is None
        or args.budget_max_requests > 2500 or args.budget_cap > 24.870275991999986
        or args.budget_authorized_max > 24.870275991999986
        or not args.expected_ledger_requests < args.session_max_requests <= min(args.expected_ledger_requests + 60, args.budget_max_requests)
        or not args.expected_ledger_estimated_usd < args.session_cap_usd <= min(args.expected_ledger_estimated_usd + .75, args.budget_cap)):
        parser.error("experimental prefix exceeds reviewed turn/time/session/cumulative limits")


def launch(args, parser):
    started = time.monotonic()
    deadline_unix = time.time() + args.timeout if args.experimental_wait_prefix else None
    gym = Path(__file__).resolve().parent.parent
    python = selected_python(gym, args.python)
    engine = args.engine_dir.resolve()
    if args.experimental_wait_prefix:
        for root, expected in ((gym, args.expected_gym_head), (engine, args.expected_engine_head)):
            head = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
            dirty = subprocess.check_output(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"], text=True).strip()
            if head != expected or dirty:
                raise RuntimeError("experimental source pin or tracked source differs")
    out = args.output_dir.resolve()
    os.umask(0o077)
    if args.experimental_wait_prefix:
        if args.output_dir.is_symlink():
            raise RuntimeError("experimental output must not be a symlink")
        _durable_mkdir(out)
    else:
        out.mkdir(parents=True, exist_ok=True, mode=0o700)
    if stat.S_IMODE(out.stat().st_mode) & 0o077:
        raise RuntimeError("output directory must be private to the current user")
    ledger = args.budget_ledger.resolve() if args.budget_ledger else out / "openai-budget.json"
    start_ledger = None
    if args.budget_ledger is not None and not args.dry_run:
        start_ledger = checked_existing_budget(
            ledger, args.budget_cap, args.budget_authorized_max,
            args.budget_max_requests, args.expected_ledger_requests,
            args.expected_unsettled_requests, args.expected_ledger_estimated_usd,
        )
    if args.dry_run and not ledger.exists():
        OpenAIRunBudget(
            ledger, 0.000000001, authorized_max_usd=args.budget_authorized_max,
            max_requests=args.budget_max_requests, initialize_new_ledger=True,
        ).snapshot()
    if args.experimental_wait_prefix and any(out.iterdir()):
        raise RuntimeError("experimental prefix output already has evidence; refusing replay")
    if args.session_cap_usd is not None:
        OpenAIRunBudget(ledger, args.budget_cap, authorized_max_usd=args.budget_authorized_max,
                       max_requests=args.budget_max_requests, session_cap_usd=args.session_cap_usd,
                       session_max_requests=args.session_max_requests).snapshot()
    run_dir = out / f"game-{int(time.time())}-{secrets.token_hex(4)}"
    run_dir.mkdir(mode=0o700)
    if args.experimental_wait_prefix:
        _durable_mkdir(run_dir)
    catalog_root = args.instance_root.resolve()
    catalog_path = args.catalog.resolve()
    if args.qualified_v7_comparison:
        catalog_root, catalog_path, qualification = stage_qualified_v7_catalog(
            catalog_root, catalog_path, run_dir, args.profile_a, args.profile_b,
            args.max_attempts,
        )
        print("TWO_LUNA_QUALIFICATION_PREFLIGHT " + json.dumps(qualification, sort_keys=True))
    elif args.experimental_wait_prefix:
        catalog_root, catalog_path, qualification = stage_experimental_wait_catalog(
            catalog_root, catalog_path, run_dir, tuple(args.profiles), args.max_attempts)
        _write_private_json(out / "prefix-manifest.json", {
            "gymHead": args.expected_gym_head, "engineHead": args.expected_engine_head,
            "preflight": qualification, "startLedger": start_ledger,
            "sessionCapUsd": args.session_cap_usd, "sessionMaxRequests": args.session_max_requests,
            "prefixTurnLimit": args.prefix_turn_limit, "deadlineUnix": deadline_unix,
            "runtimeLock": str(args.runtime_lock), "model": "gpt-6-luna", "maxAttempts": 2,
            "cacheFriendlyHistory": False, "qualification": False,
        })
    sidecar_port, server_port = free_port(), free_port()
    env = dict(os.environ)
    env.update({
        "OPENAI_API_KEY": "sk-test-no-dispatch" if args.dry_run else read_key(args.api_key_file),
        "COMMANDER_GYM_SIDECAR_TOKEN": secrets.token_hex(32),
        "COMMANDER_GYM_SIDECAR_HOST": "127.0.0.1",
        "COMMANDER_GYM_SIDECAR_PORT": str(sidecar_port),
        "COMMANDER_GYM_SIDECAR_URL": f"http://127.0.0.1:{sidecar_port}",
        "COMMANDER_GYM_GUI_SERVER_PORT": str(server_port),
        "COMMANDER_GYM_JVM_SIDECAR_TIMEOUT_MS": "120000",
        "COMMANDER_GYM_OPENAI_MODEL": "gpt-6-luna",
        "COMMANDER_GYM_OPENAI_MAX_ATTEMPTS": str(args.max_attempts),
        "COMMANDER_GYM_OPENAI_TIMEOUT": "120",
        "COMMANDER_GYM_SIDECAR_PROVENANCE": str(run_dir / "policy.jsonl"),
        "COMMANDER_GYM_BINDING_CATALOG": str(catalog_path),
        "COMMANDER_GYM_INSTANCE_ROOT": str(catalog_root),
        "COMMANDER_GYM_OPENAI_BUDGET_LEDGER": str(ledger),
        "COMMANDER_GYM_OPENAI_BUDGET_CAP_USD": "0.000000001" if args.dry_run else str(args.budget_cap),
        "COMMANDER_GYM_OPENAI_BUDGET_AUTHORIZED_MAX_USD": str(args.budget_authorized_max),
        "ARGENTUM_ENGINE_DIR": str(engine),
    })
    if args.budget_max_requests is not None:
        env["COMMANDER_GYM_OPENAI_BUDGET_MAX_REQUESTS"] = str(args.budget_max_requests)
    else:
        env.pop("COMMANDER_GYM_OPENAI_BUDGET_MAX_REQUESTS", None)
    # Ambient experiment settings must not mutate a default v7 run.
    for key in ("COMMANDER_GYM_OPENAI_SESSION_CAP_USD", "COMMANDER_GYM_OPENAI_SESSION_MAX_REQUESTS",
                "COMMANDER_GYM_PREFIX_TURN_LIMIT", "COMMANDER_GYM_PREFIX_DEADLINE_UNIX",
                "COMMANDER_GYM_PREFIX_STOP_RECEIPT"):
        env.pop(key, None)
    if args.session_cap_usd is not None:
        env["COMMANDER_GYM_OPENAI_SESSION_CAP_USD"] = str(args.session_cap_usd)
        env["COMMANDER_GYM_OPENAI_SESSION_MAX_REQUESTS"] = str(args.session_max_requests)
    if args.experimental_wait_prefix:
        env["COMMANDER_GYM_CACHE_FRIENDLY_HISTORY"] = "false"
        env["COMMANDER_GYM_PREFIX_TURN_LIMIT"] = str(args.prefix_turn_limit)
        env["COMMANDER_GYM_PREFIX_DEADLINE_UNIX"] = str(deadline_unix)
        env["COMMANDER_GYM_PREFIX_STOP_RECEIPT"] = str(run_dir / "prefix-stop.json")
    processes, logs = [], []
    previous_term = (signal.signal(signal.SIGTERM, _interrupted)
                     if args.supervised_batch_process_group or args.experimental_wait_prefix else None)
    try:
        sidecar_log = (run_dir / "sidecar.log").open("x")
        logs.append(sidecar_log)
        sidecar = subprocess.Popen([str(python), "-m",
            "commander_gym.game_server_binding_openai_sidecar"], cwd=gym, env=env,
            stdout=sidecar_log, stderr=subprocess.STDOUT,
            start_new_session=not args.supervised_batch_process_group)
        processes.append(sidecar)
        await_ready(
            f"http://127.0.0.1:{sidecar_port}/v1/controller-profiles",
            sidecar, min(30, max(.001, args.timeout - (time.monotonic() - started))) if args.experimental_wait_prefix else 30, env["COMMANDER_GYM_SIDECAR_TOKEN"],
        )
        server_log = (run_dir / "server.log").open("x")
        logs.append(server_log)
        server = subprocess.Popen([str(engine / "scripts/gradle-locked"), "-p",
            str(gym / "jvm-adapter"), "runLocalGuiServer"], cwd=engine, env=env,
            stdout=server_log, stderr=subprocess.STDOUT,
            start_new_session=not args.supervised_batch_process_group)
        processes.append(server)
        await_ready(f"http://127.0.0.1:{server_port}/", server, min(240, max(.001, args.timeout - (time.monotonic() - started))) if args.experimental_wait_prefix else 240)
        runner_env = dict(env)
        runner_env.pop("OPENAI_API_KEY", None)
        result = subprocess.run([str(python), "-m",
            "commander_gym.two_luna_debug", "--server-url", f"http://127.0.0.1:{server_port}",
            "--sidecar-url", f"http://127.0.0.1:{sidecar_port}",
            "--profile-a", args.profile_a, "--profile-b", args.profile_b,
            *(item for profile in (args.profiles or ()) for item in ("--profile", profile)),
            "--budget-ledger", str(ledger),
            "--budget-cap", "0.000000001" if args.dry_run else str(args.budget_cap),
            "--budget-authorized-max", str(args.budget_authorized_max),
            *(["--budget-max-requests", str(args.budget_max_requests)] if args.budget_max_requests is not None else []),
            *(["--session-cap-usd", str(args.session_cap_usd), "--session-max-requests", str(args.session_max_requests)] if args.session_cap_usd is not None else []),
            *(["--prefix-turn-limit", str(args.prefix_turn_limit), "--prefix-stop-receipt", str(run_dir / "prefix-stop.json")] if args.experimental_wait_prefix else []),
            "--provenance", str(run_dir / "policy.jsonl"),
            "--server-log", str(run_dir / "server.log"),
            "--terminal-evidence-dir", str(run_dir),
            "--timeout", str(min(args.timeout, 30) if args.dry_run else max(.001, args.timeout - (time.monotonic() - started)) if args.experimental_wait_prefix else args.timeout),
            "--stall-seconds", str(min(args.stall_seconds, 30) if args.dry_run else args.stall_seconds)],
            cwd=gym, env=runner_env, check=False,
            timeout=max(.001, args.timeout - (time.monotonic() - started)) if args.experimental_wait_prefix else None)
        print(f"RUN_ARTIFACTS={run_dir}", flush=True)
        print(f"CUMULATIVE_BUDGET_LEDGER={ledger}", flush=True)
        return result.returncode
    except subprocess.TimeoutExpired:
        _write_private_json(run_dir / "prefix-launcher-stop.json", {"reason": "prefix_wall_limit"})
        return 1
    finally:
        if args.supervised_batch_process_group:
            # The parent batch wrapper owns this whole group and verifies that
            # no member survives before it releases its runtime lock.
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
        try:
            for process in reversed(processes):
                if args.experimental_wait_prefix:
                    _stop_and_verify_group(process)
                    continue
                if process.poll() is None:
                    if args.supervised_batch_process_group:
                        process.terminate()
                    else:
                        os.killpg(process.pid, signal.SIGTERM)
            for process in reversed(processes):
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    if args.supervised_batch_process_group:
                        process.kill()
                    else:
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=10)
            if args.supervised_batch_process_group:
                print("TWO_LUNA_PROCESS_CLEANUP=top_level_exited", flush=True)
            if args.experimental_wait_prefix:
                after = checked_existing_budget(ledger, args.budget_cap, args.budget_authorized_max,
                                                args.budget_max_requests, None, None)
                final_budget = OpenAIRunBudget(ledger, args.budget_cap,
                                              authorized_max_usd=args.budget_authorized_max,
                                              max_requests=args.budget_max_requests)
                _, recorded, reconciled, reconciliation_error = _finalize_provenance(
                    run_dir / "policy.jsonl", final_budget, start_ledger["requests"], timeout_seconds=0)
                _write_private_json(run_dir / "prefix-run-receipt.json", {
                    "startLedger": start_ledger, "afterLedger": after,
                    "sessionCapUsd": args.session_cap_usd, "sessionMaxRequests": args.session_max_requests,
                    "newUnsettledRequests": after["unsettledRequests"] - start_ledger["unsettledRequests"],
                    "cleanupVerified": True, "qualification": False,
                    "providerAttemptsRecorded": recorded, "provenanceReconciled": reconciled,
                    "provenanceError": reconciliation_error,
                })
                print(f"RUN_ARTIFACTS={run_dir}", flush=True)
        finally:
            for log in logs:
                log.close()
            if args.supervised_batch_process_group or args.experimental_wait_prefix:
                signal.signal(signal.SIGTERM, previous_term)


if __name__ == "__main__":
    raise SystemExit(main())
