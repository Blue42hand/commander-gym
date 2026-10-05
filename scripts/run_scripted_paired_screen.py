"""Screen reviewed scripted native callbacks offline by default; --execute makes paid calls.

The source set, exact requests, and cumulative ledger start must match the committed
manifest. This never advances a game. Stop on an invalid response or semantic divergence.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
from time import monotonic

from commander_gym.binding_catalog import load_binding_catalog
from commander_gym.game_server_bindings import GameServerBindingRegistry
from commander_gym.openai_run_budget import OpenAIRunBudget
from scripts.audit_scripted_privacy_capture import _NeverRunPilot, _bound_observation
from scripts.run_paired_view_screen import (
    _append_result, _captured_request, _run_with_watchdog, _screen_worker,
    _semantic_choice,
)


MANIFEST = Path(__file__).with_name("scripted_paired_screen_manifest.json")
REVIEWED_MANIFEST_SHA256 = "36e9f110218cc8d590b9885838dd0f1b8b300aefe935ff4adf657362358dbe9a"
REVIEWED_SOURCE = "ef2dec32b22f4f65d0cb3968ea4bf1349877c46156e88877fa838f0a5a31808e"
QUARANTINED_TRACE = "f77cb63314714b13100e7ef3aa64cfc86edf4c37cbc030fde5fde0592952e184"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _private_directory(path: Path) -> None:
    if not path.is_dir() or path.stat().st_mode & 0o077:
        raise ValueError("screen artifacts must be in an existing private directory")


def _load_reviewed(capture: Path, corpus: Path, instance_root: Path, catalog: Path):
    manifest_bytes = MANIFEST.read_bytes()
    if _sha(manifest_bytes) != REVIEWED_MANIFEST_SHA256:
        raise ValueError("screen manifest does not match the reviewed manifest")
    manifest = json.loads(manifest_bytes)
    if (manifest.get("schemaVersion") != 1 or manifest.get("sourceSetSha256") != REVIEWED_SOURCE
            or REVIEWED_SOURCE == QUARANTINED_TRACE or manifest.get("capturedCallbacks") != 78
            or manifest.get("actionCallbacks") != 76 or manifest.get("model") != "gpt-6-luna"
            or manifest.get("maxOutputTokens") != 2048 or manifest.get("maxNewRequests") != 16
            or manifest.get("wallSeconds") != 360 or manifest.get("expectedLedgerRequests") != 590
            or manifest.get("absoluteLedgerRequestLimit") != 610
            or manifest.get("authorizedCapUsd") != 6 or len(manifest.get("selected", [])) != 8):
        raise ValueError("screen manifest violates the reviewed source or bounds")
    if _sha(catalog.read_bytes()) != manifest["catalogSha256"]:
        raise ValueError("canonical Binding catalog hash changed")
    summary_path = capture.parent / "summary.json"
    if _sha(summary_path.read_bytes()) != manifest["captureSummarySha256"]:
        raise ValueError("native capture summary hash changed")
    summary = json.loads(summary_path.read_text())
    if (summary.get("engineCommit") != manifest["engineCommit"]
            or summary.get("gymCommit") != manifest["captureGymCommit"]
            or summary.get("providerCalls") != 0
            or summary.get("httpCallbacks") != 78):
        raise ValueError("native capture provenance changed")
    source_files = list(capture.glob("*.json"))
    source_hashes = [_sha(path.read_bytes()) for path in source_files]
    if (len(source_files) != 78 or len(set(source_hashes)) != 78
            or _sha("\n".join(sorted(source_hashes)).encode()) != REVIEWED_SOURCE):
        raise ValueError("native HTTP source set changed or includes the quarantined source")
    audit = json.loads((corpus / "corpus-audit.json").read_text())
    if (audit.get("sourceSetSha256") != REVIEWED_SOURCE
            or audit.get("catalogSha256") != manifest["catalogSha256"]
            or audit.get("captureSummarySha256") != manifest["captureSummarySha256"]
            or audit.get("capturedCallbacks") != 78 or audit.get("actionCallbacks") != 76
            or audit.get("paidCalls") != 0):
        raise ValueError("corpus audit no longer matches its native source")
    loaded = load_binding_catalog(catalog, instance_root=instance_root,
                                  pilot_factory=lambda _pilot, _binding: _NeverRunPilot())
    registry = GameServerBindingRegistry(loaded.resolver, loaded.binding_ids)
    rows = []
    seats: dict[str, int] = {}
    if len(audit.get("selected", [])) != 8:
        raise ValueError("corpus no longer has eight positions")
    for expected, audited in zip(manifest["selected"], audit["selected"]):
        index = expected["index"]
        if index != len(rows) or audited["index"] != index:
            raise ValueError("position order changed")
        source = (corpus / f"sample-{index:02d}-http.json").read_bytes()
        if (_sha(source) != expected["httpSha256"]
                or audited["sourceHttpSha256"] != expected["httpSha256"]
                or expected["httpSha256"] not in source_hashes):
            raise ValueError("selected native callback changed")
        body = json.loads(source)
        state = body["state"]
        viewer = state["viewingPlayerId"]
        if viewer != body["playerId"] or body["profileId"] != audited["profileId"]:
            raise ValueError("native callback seat changed")
        adapter = registry.create_seat(viewer, body["profileId"])
        observation = _bound_observation(adapter, adapter._observation(
            state, body["legalActions"], body["pendingDecision"], body["recentGameLog"],
        ))
        for compact, label in ((False, "full"), (True, "compact")):
            request = _captured_request(observation, compact)
            encoded = json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
            digest = _sha(encoded)
            if (digest != expected[f"{label}RequestSha256"]
                    or digest != audited["hashes"][f"{label}Request"]
                    or encoded != (corpus / f"sample-{index:02d}-{label}-request.json").read_bytes()
                    or request.get("model") != "gpt-6-luna"
                    or request.get("max_output_tokens") != 2048):
                raise ValueError(f"exact {label} provider request changed at position {index}")
        seats[body["profileId"]] = seats.get(body["profileId"], 0) + 1
        rows.append((index, observation))
    if sorted(seats.values()) != [4, 4] or set(seats) != set(summary["profiles"]):
        raise ValueError("screen must include four positions from each roster seat")
    return manifest, rows


def _read_ledger(path: Path, manifest: dict) -> dict:
    if not path.is_file() or not path.read_bytes():
        raise ValueError("existing nonempty cumulative ledger required")
    ledger = json.loads(path.read_text())
    if (ledger.get("schemaVersion") != 1 or ledger.get("requests") != 590
            or ledger.get("maxRequests") != 610 or ledger.get("capUsd") != 6
            or ledger.get("requests") + manifest["maxNewRequests"] != 606
            or ledger.get("unsettledRequests", 0) > ledger["requests"]):
        raise ValueError("cumulative ledger is not the reviewed 590/610 starting point")
    return ledger


@contextmanager
def _exclusive_screen_lock(ledger_path: Path):
    """Serialize both initial and continuation screens for one ledger."""
    if not ledger_path.is_absolute():
        raise ValueError("all paths must be absolute")
    ledger_path = ledger_path.resolve()
    lock_path = ledger_path.with_name(ledger_path.name + ".scripted-screen.lock")
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("another scripted paired screen is using this ledger") from exc
        yield
    finally:
        os.close(fd)


def _run(args) -> int:
    paths = (args.capture_dir, args.corpus_dir, args.instance_root, args.catalog, args.ledger, args.output)
    if not all(path.is_absolute() for path in paths):
        raise ValueError("all paths must be absolute")
    _private_directory(args.capture_dir)
    _private_directory(args.corpus_dir)
    _private_directory(args.output.parent)
    if args.output.exists():
        raise ValueError("output already exists; never silently resume a screen")
    manifest, rows = _load_reviewed(args.capture_dir, args.corpus_dir, args.instance_root, args.catalog)
    ledger = _read_ledger(args.ledger, manifest)
    # Preflight the sum of all worst-case provider reservations, without touching the ledger.
    reservations = sum(OpenAIRunBudget._cost(
        len(json.dumps(_captured_request(observation, compact), ensure_ascii=False).encode()) + 4096,
        2048,
    ) for _, observation in rows for compact in (False, True))
    if ledger["estimatedUsd"] + reservations > 6:
        raise ValueError("full sixteen-request reservation exceeds cumulative $6 cap")
    if not args.execute:
        print(json.dumps({"dryRun": True, "sourceSetSha256": REVIEWED_SOURCE,
                          "positions": len(rows), "plannedRequests": 16,
                          "ledgerRequests": 590, "maximumLedgerRequestsAfter": 606,
                          "absoluteLedgerLimit": 610, "reservationUsd": round(reservations, 6)}))
        return 0
    if args.approved_source_set_sha256 != REVIEWED_SOURCE:
        raise ValueError("paid screen requires explicit approval for this exact source set")
    if not os.environ.get("OPENAI_API_KEY"):
        raise ValueError("existing OpenAI credential unavailable")
    import openai  # noqa: F401 -- prove SDK available before writing an output artifact

    output_fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(output_fd)
    deadline = monotonic() + manifest["wallSeconds"]
    budget = OpenAIRunBudget(args.ledger, 6, authorized_max_usd=6, max_requests=610)
    if budget.snapshot()["requests"] != 590:
        raise ValueError("ledger changed after preflight")
    attempts = 0
    for index, observation in rows:
        pair = []
        for compact, label in ((False, "full"), (True, "compact")):
            if monotonic() >= deadline or attempts >= 16:
                print("Stopped at wall or request limit", file=sys.stderr)
                return 2
            before = budget.snapshot()
            artifact = args.output.with_name(f"{args.output.stem}.sample-{index:02d}.{label}.model-io.json")
            if artifact.exists():
                raise ValueError("private model I/O artifact already exists")
            started = monotonic()
            try:
                worker = _run_with_watchdog(
                    _screen_worker, (args.ledger, observation, compact, deadline, artifact), deadline,
                )
            except Exception as exc:
                worker = {"choice": None, "usage": None, "providerWallMs": None,
                          "modelIoArtifact": str(artifact) if artifact.exists() else None,
                          "error": f"{type(exc).__name__}: {exc}"}
            after = budget.snapshot()
            attempts += after["requests"] - before["requests"]
            semantic = _semantic_choice(worker["choice"]) if worker["choice"] is not None else None
            _append_result(args.output, {
                "sampleIndex": index, "view": label, "valid": worker["error"] is None,
                "error": worker["error"], "choice": worker["choice"], "semanticChoice": semantic,
                "usage": worker["usage"], "providerWallMs": worker["providerWallMs"],
                "modelIoArtifact": worker["modelIoArtifact"],
                "harnessWallMs": round((monotonic() - started) * 1000, 3),
                "ledgerRequestsBefore": before["requests"], "ledgerRequestsAfter": after["requests"],
                "ledgerEstimatedUsdBefore": before["estimatedUsd"],
                "ledgerEstimatedUsdAfter": after["estimatedUsd"],
            })
            if worker["error"] is not None or after["requests"] != before["requests"] + 1:
                print(f"Stopped on invalid or ambiguous position {index}", file=sys.stderr)
                return 2
            pair.append(semantic)
        if pair[0] != pair[1]:
            print(f"Stopped on semantic divergence at position {index}", file=sys.stderr)
            return 2
    print(json.dumps({"completedPairs": len(rows), "newRequests": attempts,
                      "estimatedCumulativeUsd": budget.snapshot()["estimatedUsd"]}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("capture-dir", "corpus-dir", "instance-root", "catalog", "ledger", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--approved-source-set-sha256", default="")
    args = parser.parse_args()
    if args.execute:
        with _exclusive_screen_lock(args.ledger):
            return _run(args)
    return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
