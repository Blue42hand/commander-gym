"""Run only the unplayed game-03 slot after the stopped v7 continuation.

This is a single-use lineage controller, not a new game runner. It reuses the
reviewed one-game FFA launcher and strict native terminal verifier. A claimed,
stopped, or interrupted final attempt cannot be replayed by this controller.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Callable, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from commander_gym.cache_probe_session import _private_json
from scripts import probe_v7_runtime_classpath as runtime_probe
from scripts import run_sequential_v7_batch as batch
from scripts import run_v7_batch_continuation as prior


GYM_HEAD = "bda90a048f39e00e7744c5427f2bbbe7b1b29f91"
ENGINE_HEAD = "6f5cbbf55e25767b26f6d64ebfa1aa67ae8c7a7c"
CAP_USD = prior.CAP_USD
MAX_REQUESTS = prior.MAX_REQUESTS
GAME_INDEX = 3
CONTINUATION_DIR = prior.ORIGINAL_BATCH_DIR.with_name(
    "paid-v7-continuation-389f6cb-c635941-20261006"
)
FINAL_DIR = CONTINUATION_DIR / "game-03-final"
FINAL_CLAIM = CONTINUATION_DIR / "game-03-final-claim.json"
CONTINUATION_CLAIM_SHA256 = "9c1b802406278e6d01f5cc4d1e31b8d91ba3dde1e47d1227e4a8a3469d63808e"
CONTINUATION_HASHES = {
    "manifest.json": "4531acff82093c5cbecb7e76e465b728a3606c035961d99cc77927456f38ad3d",
    "cursor.json": "118226c9290a4838d16873ff5b797f570f97162cdcca99f1b6b300f183257c0c",
    "game-02-receipt.json": "fcc7860d8d6fe0115a847dbac7bdfbea0d84c9e604f38ce24be1d09b56b4e32c",
}
LEDGER_SHA256 = "26f134300b27c2416f648c7f05693dc1fddbfd38405d6ceba545a3cd23935a4d"
REQUESTS = 2175
ESTIMATED_USD = 19.808876724999962
UNSETTLED = 3


def _private_existing(path: Path, *, directory: bool = False) -> None:
    if (path.is_symlink() or (not path.is_dir() if directory else not path.is_file())
        or stat.S_IMODE(path.stat().st_mode) & 0o077):
        raise batch.BatchError(f"prior private evidence is missing or exposed: {path.name}")


def _prior_lineage() -> dict[str, Any]:
    original = prior._read_original()
    for name in prior.ORIGINAL_HASHES:
        _private_existing(prior.ORIGINAL_BATCH_DIR / name)
    _private_existing(CONTINUATION_DIR, directory=True)
    claim = prior._claim_path()
    prior._verify_claim(claim, prior._claim_value(CONTINUATION_DIR))
    if batch._sha(claim) != CONTINUATION_CLAIM_SHA256:
        raise batch.BatchError("existing continuation claim hash changed")
    for name, digest in CONTINUATION_HASHES.items():
        path = CONTINUATION_DIR / name
        _private_existing(path)
        if batch._sha(path) != digest:
            raise batch.BatchError(f"stopped continuation {name} hash changed")
    manifest = json.loads((CONTINUATION_DIR / "manifest.json").read_text())
    cursor = json.loads((CONTINUATION_DIR / "cursor.json").read_text())
    receipt = json.loads((CONTINUATION_DIR / "game-02-receipt.json").read_text())
    if (manifest.get("originalBatchDir") != str(prior.ORIGINAL_BATCH_DIR)
        or manifest.get("claimPath") != str(claim)
        or manifest.get("claimSha256") != CONTINUATION_CLAIM_SHA256
        or manifest.get("gymHead") != prior.GYM_HEAD
        or manifest.get("engineHead") != prior.ENGINE_HEAD
        or manifest.get("startLedger") != original["receipt"].get("afterLedger")
        or manifest.get("startLedgerSha256") != prior.START_LEDGER_SHA256
        or manifest.get("cumulativeCapUsd") != CAP_USD
        or manifest.get("maxRequests") != MAX_REQUESTS
        or receipt.get("index") != 2 or receipt.get("qualified") is not False
        or receipt.get("beforeLedger") != manifest["startLedger"]
        or receipt.get("beforeLedgerSha256") != prior.START_LEDGER_SHA256
        or receipt.get("priorReceiptSha256") is not None
        or receipt.get("afterLedgerSha256") != LEDGER_SHA256
        or cursor != {"status": "stopped", "nextGame": 2,
                      "reason": receipt.get("stopReasons"),
                      "lastReceiptSha256": CONTINUATION_HASHES["game-02-receipt.json"]}):
        raise batch.BatchError("stopped game-02 lineage is inconsistent")
    for directory in (prior.ORIGINAL_BATCH_DIR, CONTINUATION_DIR):
        if any((directory / f"game-03{suffix}").exists()
               or (directory / f"game-03{suffix}").is_symlink()
               for suffix in ("", "-launcher.log", "-receipt.json")):
            raise batch.BatchError("game-03 already has artifacts in prior lineage")
    return {"original": original, "manifest": manifest,
            "receipt": receipt, "cursor": cursor}


def _hex_digest(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{64}", value))


def _identity(args: argparse.Namespace, lineage: Mapping[str, Any]) -> dict[str, Any]:
    controller = Path(__file__).resolve().parent.parent
    if (args.expected_gym_head != GYM_HEAD or args.expected_engine_head != ENGINE_HEAD
        or batch._head(controller) != args.expected_control_head
        or not batch._tracked_clean(controller)):
        raise batch.BatchError("final-attempt control or repaired source pin changed")
    for digest in (args.expected_game_server_jar_sha256,
                   args.expected_adapter_jar_sha256,
                   args.expected_runtime_classpath_sha256):
        if not _hex_digest(digest):
            raise batch.BatchError("runtime artifact digest is malformed")
    if (not args.game_server_jar.is_file() or args.game_server_jar.is_symlink()
        or batch._sha(args.game_server_jar) != args.expected_game_server_jar_sha256
        or not args.adapter_jar.is_file() or args.adapter_jar.is_symlink()
        or batch._sha(args.adapter_jar) != args.expected_adapter_jar_sha256
        or not args.game_server_jar.resolve().is_relative_to(args.engine_dir.resolve())
        or not args.adapter_jar.resolve().is_relative_to(args.gym_dir.resolve())):
        raise batch.BatchError("repaired engine or Gym adapter JAR differs from reviewed pin")
    runtime = runtime_probe.fingerprint(args.engine_dir, args.gym_dir)
    if runtime["sha256"] != args.expected_runtime_classpath_sha256:
        raise batch.BatchError("effective Gradle server runtime classpath differs from reviewed pin")
    base = batch._identity(args)
    original = lineage["original"]["manifest"]
    for key in ("profiles", "model", "cacheFriendlyHistory", "maxAttempts",
                "instanceRoot", "catalog", "catalogSha256", "python",
                "apiKeyFile", "ledger", "runtimeLock", "timeout", "stallSeconds",
                "qualificationPreflight"):
        if base.get(key) != original.get(key):
            raise batch.BatchError(f"qualified runtime option {key} changed")
    base.pop("incrementalCapUsd")
    return {
        **base, "schemaVersion": 3, "controllerRoot": str(controller),
        "controllerHead": args.expected_control_head,
        "controllerScriptSha256": batch._sha(Path(__file__)),
        "batchHelperSha256": batch._sha(Path(batch.__file__)),
        "priorHelperSha256": batch._sha(Path(prior.__file__)),
        "runtimeProbeSha256": batch._sha(Path(runtime_probe.__file__)),
        "runtimeInitScriptSha256": batch._sha(runtime_probe.INIT_SCRIPT),
        "runtimeClasspath": runtime,
        "readinessGameServerJar": str(args.game_server_jar),
        "readinessGameServerJarSha256": args.expected_game_server_jar_sha256,
        "readinessAdapterJar": str(args.adapter_jar),
        "readinessAdapterJarSha256": args.expected_adapter_jar_sha256,
        "originalBatchDir": str(prior.ORIGINAL_BATCH_DIR),
        "originalHashes": prior.ORIGINAL_HASHES,
        "continuationDir": str(CONTINUATION_DIR),
        "continuationClaimSha256": CONTINUATION_CLAIM_SHA256,
        "continuationHashes": CONTINUATION_HASHES,
        "originalGame": GAME_INDEX, "gameCount": 1,
        "cumulativeCapUsd": CAP_USD, "maxRequests": MAX_REQUESTS,
    }


def _ledger(args: argparse.Namespace, expected: Mapping[str, Any]) -> dict[str, Any]:
    if batch._sha(args.budget_ledger) != LEDGER_SHA256:
        raise batch.BatchError("shared ledger hash changed after stopped game-02")
    current = prior._snapshot(args.budget_ledger)
    if (batch._sha(args.budget_ledger) != LEDGER_SHA256 or current != expected
        or current["requests"] != REQUESTS
        or current["estimatedUsd"] != ESTIMATED_USD
        or current["unsettledRequests"] != UNSETTLED):
        raise batch.BatchError("shared ledger differs from stopped game-02 receipt")
    return current


def _claim_value(identity: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "parentClaimSha256": CONTINUATION_CLAIM_SHA256,
        "parentManifestSha256": CONTINUATION_HASHES["manifest.json"],
        "game02ReceiptSha256": CONTINUATION_HASHES["game-02-receipt.json"],
        "stoppedCursorSha256": CONTINUATION_HASHES["cursor.json"],
        "priorLedgerSha256": LEDGER_SHA256,
        "finalDir": str(FINAL_DIR),
        "controllerHead": identity["controllerHead"],
        "gymHead": GYM_HEAD, "engineHead": ENGINE_HEAD,
        "cumulativeCapUsd": CAP_USD, "maxRequests": MAX_REQUESTS,
    }


def run_final(
    args: argparse.Namespace,
    *, runner: Callable[[argparse.Namespace, int, Path, Path, int, int, float], int] = batch.launch_one_game,
) -> dict[str, Any]:
    paths = (args.gym_dir, args.engine_dir, args.instance_root, args.catalog,
             args.python, args.api_key_file, args.budget_ledger, args.runtime_lock,
             args.game_server_jar, args.adapter_jar)
    if any(not path.is_absolute() for path in paths):
        raise batch.BatchError("all final-attempt runtime paths must be absolute")
    if (FINAL_DIR.parent != CONTINUATION_DIR or FINAL_CLAIM.parent != CONTINUATION_DIR
        or args.timeout <= 0 or args.stall_seconds <= 0):
        raise batch.BatchError("final-attempt destination or runtime bounds are invalid")
    if not args.python.is_file() or not os.access(args.python, os.X_OK):
        raise batch.BatchError("selected Python venv is unavailable")
    if not args.api_key_file.is_file() or not args.budget_ledger.is_file():
        raise batch.BatchError("authorized key file or shared ledger is unavailable")
    if not args.runtime_lock.parent.is_dir() or args.runtime_lock.is_symlink():
        raise batch.BatchError("shared runtime lock is unavailable")
    os.umask(0o077)
    lock_fd = os.open(args.runtime_lock, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if stat.S_IMODE(os.fstat(lock_fd).st_mode) & 0o077:
            raise batch.BatchError("shared runtime lock must be private")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise batch.BatchError("another process owns the shared game runtime") from exc
        lineage = _prior_lineage()
        identity = _identity(args, lineage)
        if FINAL_CLAIM.exists() or FINAL_CLAIM.is_symlink() or FINAL_DIR.exists() or FINAL_DIR.is_symlink():
            raise batch.BatchError("game-03 final attempt is already claimed; no replay or alternate output")
        current = _ledger(args, lineage["receipt"]["afterLedger"])
        if current["requests"] >= MAX_REQUESTS or current["estimatedUsd"] >= CAP_USD:
            raise batch.BatchError("original cumulative budget is exhausted")
        # Recheck all immutable evidence under the same lock immediately before claim creation.
        if (_prior_lineage() != lineage or _identity(args, lineage) != identity
            or _ledger(args, lineage["receipt"]["afterLedger"]) != current):
            raise batch.BatchError("lineage, reviewed source, or shared ledger changed before claim")
        _private_json(FINAL_CLAIM, _claim_value(identity))
        batch._private_dir(FINAL_DIR)
        manifest = {
            **identity, "startLedger": current, "startLedgerSha256": LEDGER_SHA256,
            "claimPath": str(FINAL_CLAIM), "claimSha256": batch._sha(FINAL_CLAIM),
            "game02ReceiptSha256": CONTINUATION_HASHES["game-02-receipt.json"],
        }
        _private_json(FINAL_DIR / "manifest.json", manifest)
        cursor_path = FINAL_DIR / "cursor.json"
        batch._write_cursor(cursor_path, {"status": "ready", "originalGame": GAME_INDEX})
        if (_prior_lineage() != lineage or _identity(args, lineage) != identity
            or _ledger(args, lineage["receipt"]["afterLedger"]) != current
            or batch._sha(FINAL_CLAIM) != manifest["claimSha256"]):
            raise batch.BatchError("lineage, source, claim, or ledger changed before dispatch")
        output = FINAL_DIR / "game-03"
        log = FINAL_DIR / "game-03-launcher.log"
        receipt_path = FINAL_DIR / "game-03-receipt.json"
        if any(path.exists() or path.is_symlink() for path in (output, log, receipt_path)):
            raise batch.BatchError("game-03 output exists; refusing duplicate dispatch")
        batch._write_cursor(cursor_path, {"status": "inflight", "originalGame": GAME_INDEX,
                                          "beforeLedger": current,
                                          "beforeLedgerSha256": LEDGER_SHA256})
        args.before_estimated_usd = current["estimatedUsd"]
        code = runner(args, GAME_INDEX, log, output,
                      current["requests"], current["unsettledRequests"], CAP_USD)
        if (_prior_lineage() != lineage or _identity(args, lineage) != identity
            or batch._sha(FINAL_CLAIM) != manifest["claimSha256"]):
            raise batch.BatchError("lineage, source, or claim changed during game-03")
        after = prior._snapshot(args.budget_ledger)
        after_hash = batch._sha(args.budget_ledger)
        receipt = batch._verify_game(index=GAME_INDEX, launcher_code=code,
                                     log=log, output_dir=output, before=current,
                                     after=after, manifest=manifest)
        receipt.update(beforeLedgerSha256=LEDGER_SHA256,
                       afterLedgerSha256=after_hash,
                       priorReceiptSha256=CONTINUATION_HASHES["game-02-receipt.json"])
        _private_json(receipt_path, receipt)
        batch._write_cursor(cursor_path, {
            "status": "completed" if receipt["qualified"] else "stopped",
            "originalGame": GAME_INDEX,
            "lastReceiptSha256": batch._sha(receipt_path),
            "reason": None if receipt["qualified"] else receipt["stopReasons"],
        })
        return json.loads(cursor_path.read_text())
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("gym-dir", "engine-dir", "instance-root", "catalog", "python",
                 "api-key-file", "budget-ledger", "runtime-lock", "game-server-jar",
                 "adapter-jar"):
        p.add_argument("--" + name, required=True, type=Path)
    p.add_argument("--expected-control-head", required=True)
    p.add_argument("--expected-game-server-jar-sha256", required=True)
    p.add_argument("--expected-adapter-jar-sha256", required=True)
    p.add_argument("--expected-runtime-classpath-sha256", required=True)
    p.add_argument("--timeout", type=float, default=3600)
    p.add_argument("--stall-seconds", type=float, default=600)
    p.add_argument("--execute", action="store_true")
    return p


def main() -> int:
    args = parser().parse_args()
    if not args.execute:
        raise SystemExit("--execute is required; no game was started")
    args.expected_gym_head = GYM_HEAD
    args.expected_engine_head = ENGINE_HEAD
    args.max_requests = MAX_REQUESTS
    result = run_final(args)
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
