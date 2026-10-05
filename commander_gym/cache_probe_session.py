"""Crash-safe spend journal for one private, saved-position cache probe.

This module never reads credentials or sends provider requests. A caller supplies the
existing shared budget and the request executor. The first manifest freezes absolute
ledger ceilings; every later process must reuse them exactly.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping

from .openai_run_budget import OpenAIRunBudget

TRIALS = (
    (25, "baseline"), (29, "baseline"), (26, "baseline"),
    (25, "cache"), (29, "cache"), (26, "cache"),
    (44, "baseline"), (46, "baseline"),
    (44, "cache"), (46, "cache"),
)
INCREMENTAL_USD = 0.50
MAX_ATTEMPTS = 12
MAX_RETRIES = 2
MAX_EXTRA_REQUEST_BYTES = 2048


class ProbeSessionError(RuntimeError):
    pass


def _private_json(path: Path, value: Mapping[str, Any]) -> None:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _append(path: Path, value: Mapping[str, Any]) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def request_reservation(request: Mapping[str, Any]) -> tuple[int, float]:
    """Count bytes and reserve every estimated input token as a cache write."""
    size = len(json.dumps(request, ensure_ascii=False).encode("utf-8"))
    estimated_input = size + 4096
    if estimated_input >= OpenAIRunBudget.LONG_CONTEXT_INPUT_TOKENS:
        raise ProbeSessionError("request exceeds the short-context experiment bound")
    return size, OpenAIRunBudget._cost(
        estimated_input, OpenAIRunBudget.MAX_OUTPUT_TOKENS,
        cache_write_tokens=estimated_input,
    )


def _max_reservation(request_bytes: int) -> float:
    estimated_input = request_bytes + MAX_EXTRA_REQUEST_BYTES + 4096
    if estimated_input >= OpenAIRunBudget.LONG_CONTEXT_INPUT_TOKENS:
        raise ProbeSessionError("retry bound exceeds short context")
    return OpenAIRunBudget._cost(
        estimated_input, OpenAIRunBudget.MAX_OUTPUT_TOKENS,
        cache_write_tokens=estimated_input,
    )


def _usage_charge(usage: Any, reserved: float) -> tuple[float, int, int, int]:
    """Return conservative ledger charge and token counters for one provider result."""
    if usage is None:
        return reserved, 0, 0, 1
    value = usage if isinstance(usage, Mapping) else vars(usage)
    input_tokens = value.get("input_tokens")
    output_tokens = value.get("output_tokens")
    if (type(input_tokens) is not int or type(output_tokens) is not int
        or input_tokens < 0 or output_tokens < 0):
        return reserved, 0, 0, 1
    details = value.get("input_tokens_details")
    details = details if isinstance(details, Mapping) else vars(details) if details is not None else {}
    cached = details.get("cached_tokens")
    writes = details.get("cache_write_tokens")
    if (type(cached) is not int or type(writes) is not int
        or cached < 0 or writes < 0 or cached + writes > input_tokens):
        cached, writes = 0, input_tokens
    charge = OpenAIRunBudget._cost(
        input_tokens, output_tokens,
        cached_tokens=cached, cache_write_tokens=writes,
    )
    return charge, input_tokens, output_tokens, 0


@dataclass
class ProbeSession:
    run_dir: Path
    manifest: dict[str, Any]

    @classmethod
    def open(
        cls, run_dir: Path, budget: OpenAIRunBudget, *,
        trace_sha256: str, source_head: str,
        requests: list[Mapping[str, Any]],
    ) -> "ProbeSession":
        """Create once or resume using frozen absolute limits and payload bounds."""
        if len(requests) != len(TRIALS):
            raise ProbeSessionError("probe requires the exact ten primary requests")
        if not run_dir.is_absolute():
            raise ProbeSessionError("run directory must be absolute")
        run_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        if run_dir.is_symlink():
            raise ProbeSessionError("run directory cannot be a symlink")
        manifest_path = run_dir / "manifest.json"
        bounds = []
        for (position, variant), request in zip(TRIALS, requests, strict=True):
            size, reserve = request_reservation(request)
            bounds.append({"position": position, "variant": variant,
                           "requestSha256": hashlib.sha256(json.dumps(
                               request, sort_keys=True, separators=(",", ":")
                           ).encode()).hexdigest(),
                           "requestBytes": size,
                           "maxRequestBytes": size + MAX_EXTRA_REQUEST_BYTES,
                           "primaryReservationUsd": reserve,
                           "maxAttemptReservationUsd": _max_reservation(size)})
        worst_two = sorted((item["maxAttemptReservationUsd"] for item in bounds), reverse=True)[:2]
        worst_total = sum(item["maxAttemptReservationUsd"] for item in bounds) + sum(worst_two)
        if worst_total > INCREMENTAL_USD:
            raise ProbeSessionError("frozen payload bounds exceed the incremental cap")
        identity = {"schemaVersion": 1, "traceSha256": trace_sha256,
                    "sourceHead": source_head, "trials": bounds,
                    "incrementalCapUsd": INCREMENTAL_USD,
                    "maxAttempts": MAX_ATTEMPTS, "maxRetries": MAX_RETRIES,
                    "worstTotalReservationUsd": worst_total}
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if any(manifest.get(key) != value for key, value in identity.items()):
                raise ProbeSessionError("resume manifest, source, or payload bounds changed")
            start = manifest.get("startLedger")
            if (not isinstance(start, dict)
                or manifest.get("sessionCapUsd") != min(
                    18.0, start.get("estimatedUsd", -1) + INCREMENTAL_USD)
                or manifest.get("sessionMaxRequests") != min(
                    2212, start.get("requests", -1) + MAX_ATTEMPTS)):
                raise ProbeSessionError("frozen session ceilings differ from start ledger")
        else:
            if (run_dir / "intents.jsonl").exists() or (run_dir / "results.jsonl").exists():
                raise ProbeSessionError("attempt journal exists without its start manifest")
            start = budget.snapshot()
            if start.get("capUsd") != 18 or start.get("maxRequests") != 2212:
                raise ProbeSessionError("shared ledger limits are not the qualified $18/2212")
            manifest = {**identity,
                        "startLedger": {key: start[key] for key in (
                            "estimatedUsd", "requests", "inputTokens", "outputTokens",
                            "unsettledRequests")},
                        "sessionCapUsd": min(18.0, start["estimatedUsd"] + INCREMENTAL_USD),
                        "sessionMaxRequests": min(2212, start["requests"] + MAX_ATTEMPTS)}
            _private_json(manifest_path, manifest)
        session = cls(run_dir, manifest)
        session.verify(budget.snapshot())
        return session

    def guarded_budget(self, ledger_path: Path) -> OpenAIRunBudget:
        """Reconstruct absolute, persisted limits; never derive new ones on resume."""
        return OpenAIRunBudget(
            ledger_path, 18.0, authorized_max_usd=18.0, max_requests=2212,
            session_cap_usd=self.manifest["sessionCapUsd"],
            session_max_requests=self.manifest["sessionMaxRequests"],
            require_cache_usage_details=True,
        )

    def verify(self, snapshot: Mapping[str, Any]) -> None:
        """Fail on ambiguous interruption, other writers, or historical settlement."""
        intents = _read_jsonl(self.run_dir / "intents.jsonl")
        results = _read_jsonl(self.run_dir / "results.jsonl")
        if len(intents) != len(results):
            raise ProbeSessionError("unresolved attempt intent; do not repeat an ambiguous request")
        if len(intents) > MAX_ATTEMPTS:
            raise ProbeSessionError("attempt count exceeded frozen limit")
        for ordinal, (intent, result) in enumerate(zip(intents, results, strict=True)):
            if intent.get("ordinal") != ordinal or result.get("ordinal") != ordinal:
                raise ProbeSessionError("attempt journal order changed")
        start = self.manifest["startLedger"]
        expected = {
            "requests": start["requests"] + len(intents),
            "unsettledRequests": start["unsettledRequests"] + sum(
                result["unsettledDelta"] for result in results),
            "inputTokens": start["inputTokens"] + sum(result["inputTokens"] for result in results),
            "outputTokens": start["outputTokens"] + sum(result["outputTokens"] for result in results),
        }
        if any(snapshot.get(key) != value for key, value in expected.items()):
            raise ProbeSessionError("shared ledger counters changed outside this session")
        expected_usd = start["estimatedUsd"] + sum(result["ledgerChargeUsd"] for result in results)
        if not math.isclose(snapshot.get("estimatedUsd", -1), expected_usd, abs_tol=1e-7):
            raise ProbeSessionError("shared ledger estimate changed outside this session")
        if sum(intent["reservationUsd"] for intent in intents) > INCREMENTAL_USD:
            raise ProbeSessionError("attempt reservations exceeded incremental cap")
        if snapshot["estimatedUsd"] > self.manifest["sessionCapUsd"] + 1e-7:
            raise ProbeSessionError("shared ledger exceeded frozen session ceiling")

    def begin(self, trial_index: int, request: Mapping[str, Any], *, retry: bool = False) -> tuple[int, float]:
        """Fsync an intent before dispatch and charge its reservation permanently."""
        intents = _read_jsonl(self.run_dir / "intents.jsonl")
        results = _read_jsonl(self.run_dir / "results.jsonl")
        if len(intents) != len(results):
            raise ProbeSessionError("unresolved earlier attempt")
        if len(intents) >= MAX_ATTEMPTS:
            raise ProbeSessionError("session attempt limit reached")
        if results and results[-1]["unsettledDelta"]:
            raise ProbeSessionError("an earlier provider attempt remains unsettled")
        next_primary = sum(not item["retry"] for item in intents)
        if retry:
            if (not results or results[-1]["trialIndex"] != trial_index
                or results[-1]["valid"] or results[-1]["retry"]):
                raise ProbeSessionError("retry must immediately follow its invalid primary")
        elif trial_index != next_primary:
            raise ProbeSessionError("primary attempt order changed")
        if retry and sum(bool(item["retry"]) for item in intents) >= MAX_RETRIES:
            raise ProbeSessionError("session retry limit reached")
        bound = self.manifest["trials"][trial_index]
        size, reservation = request_reservation(request)
        if size > bound["maxRequestBytes"]:
            raise ProbeSessionError("request exceeds frozen payload bound")
        if not retry and hashlib.sha256(json.dumps(
            request, sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest() != bound["requestSha256"]:
            raise ProbeSessionError("primary request differs from frozen payload")
        if (sum(item["reservationUsd"] for item in intents) + reservation
            > INCREMENTAL_USD):
            raise ProbeSessionError("incremental reservation ceiling reached")
        ordinal = len(intents)
        _append(self.run_dir / "intents.jsonl", {
            "ordinal": ordinal, "trialIndex": trial_index, "retry": retry,
            "requestBytes": size, "reservationUsd": reservation,
        })
        return ordinal, reservation

    def finish(
        self, ordinal: int, *, usage: Any, reserved: float,
        valid: bool, result_kind: str,
        diagnostics: Mapping[str, Any] | None = None,
    ) -> None:
        intents = _read_jsonl(self.run_dir / "intents.jsonl")
        results = _read_jsonl(self.run_dir / "results.jsonl")
        if ordinal != len(results) or len(intents) != len(results) + 1:
            raise ProbeSessionError("attempt result order changed")
        charge, input_tokens, output_tokens, unsettled = _usage_charge(usage, reserved)
        _append(self.run_dir / "results.jsonl", {
            "ordinal": ordinal, "trialIndex": intents[ordinal]["trialIndex"],
            "retry": intents[ordinal]["retry"], "valid": valid,
            "resultKind": result_kind, "inputTokens": input_tokens,
            "outputTokens": output_tokens, "ledgerChargeUsd": charge,
            "unsettledDelta": unsettled,
            "diagnostics": dict(diagnostics or {}),
        })
