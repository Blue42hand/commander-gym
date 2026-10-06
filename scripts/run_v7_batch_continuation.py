"""Continue only games 2 and 3 of the stopped, authorized v7 batch.

This controller runs from a reviewed checkout. Each game runs the original
qualified Gym checkout and the fixed Argentum checkout through the existing
one-game launcher. A stopped or in-flight continuation is never replayed.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any, Callable, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from commander_gym.cache_probe_session import _private_json
from scripts import run_sequential_v7_batch as batch


ORIGINAL_BATCH_DIR = Path("/var/lib/commander-gym/runs/paid-v7-batch-00f5990-f1e9bf9-20261006")
ORIGINAL_HASHES = {
    "manifest.json": "a6b4d22ada298441f13e7a1ce0015fa6354d7b7053521bfb639930c73123f3f8",
    "cursor.json": "f6f6245b3cfeceea0e1c3db246e7bc688d6517ecec52f4486d6e728134cd7dad",
    "game-01-receipt.json": "aa8dfedaf3b70b75cf8aec3f08d84c490dfbd76eb90039e3efc6cb4a13e3fa32",
}
GYM_HEAD = "00f5990883bdf0cfc431b1870178131e50f1963e"
ENGINE_HEAD = "c63594159e441c555db8c03e09757d5b8c4e932d"
SERVER_JAR_SHA256 = "91c00ec829dacbc52161431085f45710945023bde0beadd799f16f5cf10ac67d"
ADAPTER_JAR_SHA256 = "b57d8a1e4af3371e2ba94d45b4ee2c98a7ee825b705d54d8a0bfd7a8c0e721bd"
START_LEDGER_SHA256 = "73531902a9d90e85bb34da6c44b3a0389913b71a285f7de9bbbabea33356ddcf"
CAP_USD = 24.870275991999986
MAX_REQUESTS = 2500
START_REQUESTS = 1920
START_UNSETTLED = 3
START_USD = 17.283737107999976
FIRST_GAME = 2
LAST_GAME = 3


def _read_original() -> dict[str, Any]:
    directory = ORIGINAL_BATCH_DIR
    if not directory.is_dir() or directory.is_symlink() or stat.S_IMODE(directory.stat().st_mode) & 0o077:
        raise batch.BatchError("original stopped batch directory is missing or not private")
    for name, digest in ORIGINAL_HASHES.items():
        path = directory / name
        if not path.is_file() or path.is_symlink() or batch._sha(path) != digest:
            raise batch.BatchError(f"original stopped batch {name} hash changed")
    manifest = json.loads((directory / "manifest.json").read_text())
    cursor = json.loads((directory / "cursor.json").read_text())
    receipt = json.loads((directory / "game-01-receipt.json").read_text())
    if (cursor != {"status": "stopped", "nextGame": 1, "reason": receipt.get("stopReasons")}
        or receipt.get("index") != 1 or receipt.get("qualified") is not False
        or manifest.get("gymHead") != GYM_HEAD
        or manifest.get("cumulativeCapUsd") != CAP_USD
        or manifest.get("maxRequests") != MAX_REQUESTS):
        raise batch.BatchError("original batch did not stop after only game 1 under the authorized limits")
    if any((directory / f"game-{index:02d}{suffix}").exists()
           for index in (2, 3) for suffix in ("", "-launcher.log", "-receipt.json")):
        raise batch.BatchError("original batch contains an unaccounted later attempt")
    return {"manifest": manifest, "receipt": receipt}


def _identity(args: argparse.Namespace, original: Mapping[str, Any]) -> dict[str, Any]:
    if args.expected_gym_head != GYM_HEAD or args.expected_engine_head != ENGINE_HEAD:
        raise batch.BatchError("qualified Gym or fixed engine pin changed")
    controller = Path(__file__).resolve().parent.parent
    if batch._head(controller) != args.expected_control_head or not batch._tracked_clean(controller):
        raise batch.BatchError("continuation controller differs from reviewed source")
    if (not args.game_server_jar.is_file() or args.game_server_jar.is_symlink()
        or batch._sha(args.game_server_jar) != SERVER_JAR_SHA256
        or not args.adapter_jar.is_file() or args.adapter_jar.is_symlink()
        or batch._sha(args.adapter_jar) != ADAPTER_JAR_SHA256):
        raise batch.BatchError("fixed engine or Gym adapter JAR changed")
    if (not args.game_server_jar.resolve().is_relative_to(args.engine_dir.resolve())
        or not args.adapter_jar.resolve().is_relative_to(args.gym_dir.resolve())):
        raise batch.BatchError("fixed JAR is outside its reviewed source checkout")
    base = batch._identity(args)
    prior = original["manifest"]
    for key in ("profiles", "model", "cacheFriendlyHistory", "maxAttempts",
                "instanceRoot", "catalog", "catalogSha256", "python",
                "apiKeyFile", "ledger", "runtimeLock", "timeout", "stallSeconds",
                "qualificationPreflight"):
        if base.get(key) != prior.get(key):
            raise batch.BatchError(f"original qualified runtime option {key} changed")
    base.pop("incrementalCapUsd")
    return {
        **base, "schemaVersion": 2, "controllerRoot": str(controller),
        "controllerHead": args.expected_control_head,
        "controllerScriptSha256": batch._sha(Path(__file__)),
        "batchHelperSha256": batch._sha(Path(batch.__file__)),
        "gameServerJar": str(args.game_server_jar), "gameServerJarSha256": SERVER_JAR_SHA256,
        "adapterJar": str(args.adapter_jar), "adapterJarSha256": ADAPTER_JAR_SHA256,
        "originalBatchDir": str(ORIGINAL_BATCH_DIR), "originalHashes": ORIGINAL_HASHES,
        "gameCount": 2, "firstOriginalGame": FIRST_GAME, "lastOriginalGame": LAST_GAME,
        "cumulativeCapUsd": CAP_USD, "maxRequests": MAX_REQUESTS,
    }


def _snapshot(ledger: Path) -> dict[str, Any]:
    value = batch._snapshot(ledger, CAP_USD, MAX_REQUESTS)
    if value.get("capUsd") != CAP_USD or value.get("maxRequests") != MAX_REQUESTS:
        raise batch.BatchError("shared cumulative cap or request limit changed")
    if value["estimatedUsd"] > CAP_USD + 1e-7 or value["requests"] > MAX_REQUESTS:
        raise batch.BatchError("shared cumulative budget exceeded")
    return value


def _initial_ledger(args: argparse.Namespace, original: Mapping[str, Any]) -> dict[str, Any]:
    if batch._sha(args.budget_ledger) != START_LEDGER_SHA256:
        raise batch.BatchError("shared ledger hash differs from approved game-1 closeout")
    start = _snapshot(args.budget_ledger)
    if (start["requests"] != START_REQUESTS or start["unsettledRequests"] != START_UNSETTLED
        or start["estimatedUsd"] != START_USD
        or start != original["receipt"].get("afterLedger")):
        raise batch.BatchError("shared ledger differs from approved game-1 closeout")
    return start


def _completed_receipts(directory: Path, cursor: Mapping[str, Any],
                        manifest: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    next_game = cursor["nextGame"]
    expected = manifest["startLedger"]
    previous_hash = START_LEDGER_SHA256
    previous_receipt_hash = None
    for index in range(FIRST_GAME, next_game):
        path = directory / f"game-{index:02d}-receipt.json"
        if not path.is_file() or path.is_symlink():
            raise batch.BatchError("prior continuation receipt is missing")
        receipt = json.loads(path.read_text())
        if (receipt.get("index") != index or receipt.get("qualified") is not True
            or receipt.get("beforeLedger") != expected
            or receipt.get("beforeLedgerSha256") != previous_hash
            or receipt.get("priorReceiptSha256") != previous_receipt_hash
            or receipt.get("afterLedgerSha256") is None):
            raise batch.BatchError("prior continuation receipt lineage changed")
        expected = receipt["afterLedger"]
        previous_hash = receipt["afterLedgerSha256"]
        previous_receipt_hash = batch._sha(path)
    if cursor.get("lastReceiptSha256") != previous_receipt_hash:
        raise batch.BatchError("continuation cursor receipt hash changed")
    return expected, previous_hash


def run_continuation(
    args: argparse.Namespace,
    *, runner: Callable[[argparse.Namespace, int, Path, Path, int, int, float], int] = batch.launch_one_game,
) -> dict[str, Any]:
    paths = (args.continuation_dir, args.gym_dir, args.engine_dir, args.instance_root,
             args.catalog, args.python, args.api_key_file, args.budget_ledger,
             args.runtime_lock, args.game_server_jar, args.adapter_jar)
    if any(not path.is_absolute() for path in paths):
        raise batch.BatchError("all continuation paths must be absolute")
    if (args.continuation_dir.resolve().is_relative_to(ORIGINAL_BATCH_DIR.resolve())
        or args.timeout <= 0 or args.stall_seconds <= 0):
        raise batch.BatchError("continuation destination or runtime bounds are invalid")
    if not args.python.is_file() or not os.access(args.python, os.X_OK):
        raise batch.BatchError("selected Python venv is unavailable")
    if not args.api_key_file.is_file() or not args.budget_ledger.is_file():
        raise batch.BatchError("authorized key file or shared ledger is unavailable")
    os.umask(0o077)
    batch._private_dir(args.continuation_dir)
    if not args.runtime_lock.parent.is_dir() or args.runtime_lock.is_symlink():
        raise batch.BatchError("shared runtime lock is unavailable")
    lock_fd = os.open(args.runtime_lock, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if stat.S_IMODE(os.fstat(lock_fd).st_mode) & 0o077:
            raise batch.BatchError("shared runtime lock must be private")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise batch.BatchError("another process owns the shared game runtime") from exc
        original = _read_original()
        identity = _identity(args, original)
        manifest_path = args.continuation_dir / "manifest.json"
        cursor_path = args.continuation_dir / "cursor.json"
        if manifest_path.exists():
            if manifest_path.is_symlink() or not cursor_path.is_file() or cursor_path.is_symlink():
                raise batch.BatchError("continuation manifest lacks a durable cursor")
            manifest = json.loads(manifest_path.read_text())
            if any(manifest.get(key) != value for key, value in identity.items()):
                raise batch.BatchError("frozen continuation identity changed")
            if (manifest.get("startLedgerSha256") != START_LEDGER_SHA256
                or manifest.get("startLedger") != original["receipt"].get("afterLedger")):
                raise batch.BatchError("frozen continuation starting ledger changed")
        else:
            if any(args.continuation_dir.iterdir()):
                raise batch.BatchError("continuation evidence exists without a frozen manifest")
            start = _initial_ledger(args, original)
            manifest = {**identity, "startLedger": start,
                        "startLedgerSha256": START_LEDGER_SHA256}
            _private_json(manifest_path, manifest)
            batch._write_cursor(cursor_path, {"status": "ready", "nextGame": FIRST_GAME,
                                               "lastReceiptSha256": None})
        cursor = json.loads(cursor_path.read_text())
        if (cursor.get("status") != "ready" or type(cursor.get("nextGame")) is not int
            or not FIRST_GAME <= cursor["nextGame"] <= LAST_GAME + 1):
            raise batch.BatchError("continuation stopped or has an ambiguous in-flight attempt")
        next_game = cursor["nextGame"]
        current, current_hash = _completed_receipts(args.continuation_dir, cursor, manifest)
        if _snapshot(args.budget_ledger) != current or batch._sha(args.budget_ledger) != current_hash:
            raise batch.BatchError("shared ledger changed outside completed continuation attempts")
        while next_game <= LAST_GAME:
            if _read_original() != original or _identity(args, original) != identity:
                raise batch.BatchError("original evidence or reviewed source changed before dispatch")
            before = _snapshot(args.budget_ledger)
            before_hash = batch._sha(args.budget_ledger)
            if before != current or before_hash != current_hash:
                raise batch.BatchError("shared ledger changed before next attempt")
            if before["requests"] >= MAX_REQUESTS or before["estimatedUsd"] >= CAP_USD:
                batch._write_cursor(cursor_path, {"status": "stopped", "nextGame": next_game,
                                                  "reason": "original cumulative cap reached",
                                                  "lastReceiptSha256": cursor.get("lastReceiptSha256")})
                break
            output_dir = args.continuation_dir / f"game-{next_game:02d}"
            log = args.continuation_dir / f"game-{next_game:02d}-launcher.log"
            receipt_path = args.continuation_dir / f"game-{next_game:02d}-receipt.json"
            if output_dir.exists() or log.exists() or receipt_path.exists():
                raise batch.BatchError("next attempt already has artifacts; refusing replay")
            prior_receipt_hash = cursor.get("lastReceiptSha256")
            batch._write_cursor(cursor_path, {"status": "inflight", "nextGame": next_game,
                                              "beforeLedger": before, "beforeLedgerSha256": before_hash,
                                              "lastReceiptSha256": prior_receipt_hash})
            args.before_estimated_usd = before["estimatedUsd"]
            code = runner(args, next_game, log, output_dir,
                          before["requests"], before["unsettledRequests"], CAP_USD)
            if _read_original() != original:
                raise batch.BatchError("original batch evidence changed during attempt")
            after = _snapshot(args.budget_ledger)
            after_hash = batch._sha(args.budget_ledger)
            receipt = batch._verify_game(index=next_game, launcher_code=code, log=log,
                                         output_dir=output_dir, before=before, after=after,
                                         manifest=manifest)
            receipt.update(beforeLedgerSha256=before_hash, afterLedgerSha256=after_hash,
                           priorReceiptSha256=prior_receipt_hash)
            _private_json(receipt_path, receipt)
            receipt_hash = batch._sha(receipt_path)
            if not receipt["qualified"]:
                batch._write_cursor(cursor_path, {"status": "stopped", "nextGame": next_game,
                                                  "reason": receipt["stopReasons"],
                                                  "lastReceiptSha256": receipt_hash})
                break
            current, current_hash = after, after_hash
            next_game += 1
            cursor = {"status": "ready", "nextGame": next_game,
                      "lastReceiptSha256": receipt_hash}
            batch._write_cursor(cursor_path, cursor)
        return json.loads(cursor_path.read_text())
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("continuation-dir", "gym-dir", "engine-dir", "instance-root", "catalog",
                 "python", "api-key-file", "budget-ledger", "runtime-lock",
                 "game-server-jar", "adapter-jar"):
        p.add_argument("--" + name, required=True, type=Path)
    p.add_argument("--expected-control-head", required=True)
    p.add_argument("--timeout", type=float, default=3600)
    p.add_argument("--stall-seconds", type=float, default=600)
    p.add_argument("--execute", action="store_true", help="permit the already-authorized paid continuation")
    return p


def main() -> int:
    args = parser().parse_args()
    if not args.execute:
        raise SystemExit("--execute is required; no game was started")
    args.expected_gym_head = GYM_HEAD
    args.expected_engine_head = ENGINE_HEAD
    args.max_requests = MAX_REQUESTS
    result = run_continuation(args)
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("status") == "ready" and result.get("nextGame") == LAST_GAME + 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
