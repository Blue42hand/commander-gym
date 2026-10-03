"""Continue only positions 3–7 of the reviewed scripted screen; dry-run by default.

Requires the exact six-call stop receipt and the same source/request hashes. A new
invalid result stops execution; legal semantic differences are recorded for review.
This never advances a game.
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

from commander_gym.openai_run_budget import OpenAIRunBudget
from commander_gym.observation_projection import compact_seat_observation, expand_seat_observation
from commander_gym.openai_responses_pilot import _without_live_routing
from scripts.run_scripted_paired_screen import (
    MANIFEST as BASE_MANIFEST, REVIEWED_SOURCE, _load_reviewed, _private_directory, _sha,
)
from scripts.run_paired_view_screen import (
    _append_result, _captured_request, _run_with_watchdog, _screen_worker,
    _semantic_choice,
)


MANIFEST = Path(__file__).with_name("scripted_paired_screen_continuation_manifest.json")
REVIEWED_MANIFEST_SHA256 = "bca70eeacf7300a48df62cd30628c675d010453593b8d89f4f1276bd49de7655"


def _manifest() -> dict:
    raw = MANIFEST.read_bytes()
    if _sha(raw) != REVIEWED_MANIFEST_SHA256:
        raise ValueError("continuation manifest changed after review")
    m = json.loads(raw)
    if (m.get("schemaVersion") != 1 or m.get("sourceSetSha256") != REVIEWED_SOURCE
            or m.get("baseManifestSha256") != _sha(BASE_MANIFEST.read_bytes())
            or m.get("previousRequestCount") != 6 or m.get("startPosition") != 3
            or m.get("remainingPositions") != 5 or m.get("maxNewRequests") != 10
            or m.get("expectedLedgerRequests") != 596
            or m.get("expectedLedgerUnsettledRequests") != 3
            or m.get("expectedLedgerEstimatedUsd") != 5.0120125
            or m.get("maximumLedgerRequestsAfter") != 606
            or m.get("absoluteLedgerRequestLimit") != 610
            or m.get("authorizedCapUsd") != 6 or m.get("wallSeconds") != 360
            or m.get("model") != "gpt-6-luna" or m.get("maxOutputTokens") != 2048
            or m.get("semanticDivergencePolicy") != "record_and_continue"
            or len(m.get("priorModelIoSha256", [])) != 6):
        raise ValueError("continuation violates approved remaining-position bounds")
    return m


def _verify_prior(path: Path, manifest: dict, base_manifest: dict) -> None:
    _private_directory(path.parent)
    if path.name != manifest["priorResultsName"] or _sha(path.read_bytes()) != manifest["priorResultsSha256"]:
        raise ValueError("six-call receipt changed or is not the reviewed stop")
    summary_path = path.with_name(manifest["priorSummaryName"])
    if _sha(summary_path.read_bytes()) != manifest["priorSummarySha256"]:
        raise ValueError("prior screen summary changed")
    summary = json.loads(summary_path.read_text())
    if (summary.get("newRequests") != 6 or summary.get("completedPositions") != 3
            or summary.get("divergencePosition") != 2
            or summary.get("ledgerAfter", {}).get("requests") != 596
            or summary.get("sourceSetSha256") != REVIEWED_SOURCE):
        raise ValueError("prior screen summary does not record the reviewed divergence")
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    if len(rows) != 6:
        raise ValueError("prior screen must have exactly six dispatched requests")
    for offset, row in enumerate(rows):
        index, label = divmod(offset, 2)
        view = "full" if label == 0 else "compact"
        if (row.get("sampleIndex") != index or row.get("view") != view
                or row.get("valid") is not True or row.get("error") is not None
                or row.get("ledgerRequestsBefore") != 590 + offset
                or row.get("ledgerRequestsAfter") != 591 + offset):
            raise ValueError("prior screen order, validity or ledger sequence changed")
        artifact = Path(row["modelIoArtifact"])
        if (not artifact.is_absolute() or artifact.parent != path.parent
                or _sha(artifact.read_bytes()) != manifest["priorModelIoSha256"][offset]):
            raise ValueError("prior model I/O receipt changed")
        attempts = json.loads(artifact.read_text()).get("attempts")
        if not isinstance(attempts, list) or len(attempts) != 1:
            raise ValueError("prior model I/O has an unreviewed attempt count")
        request = attempts[0]["request"]
        request_hash = _sha(json.dumps(request, sort_keys=True, separators=(",", ":")).encode())
        if request_hash != base_manifest["selected"][index][view + "RequestSha256"]:
            raise ValueError("prior dispatched request does not match reviewed source")
    if (rows[0]["semanticChoice"] != rows[1]["semanticChoice"]
            or rows[2]["semanticChoice"] != rows[3]["semanticChoice"]
            or rows[4]["semanticChoice"] == rows[5]["semanticChoice"]):
        raise ValueError("prior screen stop was not the reviewed third-pair divergence")


def _verify_ledger(path: Path, manifest: dict) -> dict:
    if not path.is_file() or not path.read_bytes():
        raise ValueError("existing cumulative ledger required")
    ledger = json.loads(path.read_text())
    if (ledger.get("schemaVersion") != 1 or ledger.get("requests") != 596
            or ledger.get("maxRequests") != 610 or ledger.get("capUsd") != 6
            or ledger.get("unsettledRequests") != 3
            or abs(ledger.get("estimatedUsd", -1) - 5.0120125) > 1e-8
            or ledger["requests"] + manifest["maxNewRequests"] != 606):
        raise ValueError("continuation requires unchanged 596/610 cumulative ledger with three prior reservations")
    return ledger


def _pair_review_flag(full_choice: dict, compact_choice: dict) -> bool:
    """Legal disagreement is review evidence, never a continuation stop by itself."""
    return full_choice != compact_choice


@contextmanager
def _exclusive_screen_lock(ledger_path: Path):
    """Hold a private whole-screen lock, beyond the ledger's per-request lock."""
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
    paths = (args.capture_dir, args.corpus_dir, args.instance_root, args.catalog,
             args.ledger, args.prior_results, args.output)
    if not all(path.is_absolute() for path in paths):
        parser.error("all paths must be absolute")
    _private_directory(args.capture_dir)
    _private_directory(args.corpus_dir)
    _private_directory(args.output.parent)
    if args.output.exists() or args.output == args.prior_results:
        raise ValueError("continuation output must be new; no replay or silent resume")
    manifest = _manifest()
    base_manifest, all_rows = _load_reviewed(
        args.capture_dir, args.corpus_dir, args.instance_root, args.catalog,
    )
    _verify_prior(args.prior_results, manifest, base_manifest)
    ledger = _verify_ledger(args.ledger, manifest)
    rows = all_rows[3:8]
    if len(rows) != 5 or [index for index, _ in rows] != list(range(3, 8)):
        raise ValueError("continuation may run only the five unplayed positions")
    for _, observation in rows:
        full = _without_live_routing(observation)
        if expand_seat_observation(compact_seat_observation(full)) != full:
            raise ValueError("compact provider input loses native observation information")
    reservations = sum(OpenAIRunBudget._cost(
        len(json.dumps(_captured_request(observation, compact), ensure_ascii=False).encode()) + 4096,
        2048,
    ) for _, observation in rows for compact in (False, True))
    if ledger["estimatedUsd"] + reservations > 6:
        raise ValueError("remaining ten-request reservation exceeds existing $6 cap")
    if not args.execute:
        print(json.dumps({"dryRun": True, "sourceSetSha256": REVIEWED_SOURCE,
                          "startPosition": 3, "remainingPositions": 5,
                          "plannedNewRequests": 10, "ledgerRequests": 596,
                          "maximumLedgerRequestsAfter": 606, "absoluteLedgerLimit": 610,
                          "reservationUsd": round(reservations, 8)}))
        return 0
    if args.approved_source_set_sha256 != REVIEWED_SOURCE:
        raise ValueError("paid continuation requires explicit approved source-set hash")
    if not os.environ.get("OPENAI_API_KEY"):
        raise ValueError("previously authorized OpenAI credential unavailable")
    import openai  # noqa: F401 -- prove existing SDK available before output creation

    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    deadline = monotonic() + manifest["wallSeconds"]
    budget = OpenAIRunBudget(args.ledger, 6, authorized_max_usd=6, max_requests=610)
    if budget.snapshot()["requests"] != 596:
        raise ValueError("ledger changed after continuation preflight")
    attempts = 0
    for index, observation in rows:
        pair = []
        for compact, view in ((False, "full"), (True, "compact")):
            if monotonic() >= deadline or attempts >= 10:
                print("Stopped at continuation wall or request limit", file=sys.stderr)
                return 2
            before = budget.snapshot()
            artifact = args.output.with_name(f"{args.output.stem}.sample-{index:02d}.{view}.model-io.json")
            if artifact.exists():
                raise ValueError("model I/O artifact already exists; no replay")
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
                "sampleIndex": index, "view": view, "valid": worker["error"] is None,
                "error": worker["error"], "choice": worker["choice"], "semanticChoice": semantic,
                "pairReviewRequired": (
                    _pair_review_flag(pair[0], semantic) if compact and semantic is not None else None
                ),
                "usage": worker["usage"], "providerWallMs": worker["providerWallMs"],
                "modelIoArtifact": worker["modelIoArtifact"],
                "harnessWallMs": round((monotonic() - started) * 1000, 3),
                "ledgerRequestsBefore": before["requests"], "ledgerRequestsAfter": after["requests"],
                "ledgerEstimatedUsdBefore": before["estimatedUsd"],
                "ledgerEstimatedUsdAfter": after["estimatedUsd"],
            })
            if worker["error"] is not None or after["requests"] != before["requests"] + 1:
                print(f"Stopped on invalid or ambiguous continuation position {index}", file=sys.stderr)
                return 2
            pair.append(semantic)
        if pair[0] != pair[1]:
            print(f"Recorded legal semantic divergence at position {index} for later Pilot review",
                  file=sys.stderr)
    print(json.dumps({"completedAdditionalPairs": len(rows), "newRequests": attempts,
                      "estimatedCumulativeUsd": budget.snapshot()["estimatedUsd"]}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("capture-dir", "corpus-dir", "instance-root", "catalog", "ledger", "prior-results", "output"):
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
