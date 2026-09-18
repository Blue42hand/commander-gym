"""Guards preventing held-out benchmark leakage into training datasets."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Set

from .benchmark import load_jsonl
from .records import DecisionRecord, RecordValidationError


def benchmark_decision_ids(paths: Iterable[Path | str]) -> Set[str]:
    """Return decision IDs reserved by held-out benchmark artifacts."""

    decision_ids: Set[str] = set()
    for path in paths:
        for case in load_jsonl(path):
            case.validate()
            decision_ids.add(case.decision.decision_id)
    return decision_ids


def assert_training_records_exclude_benchmark(
    records: Iterable[DecisionRecord],
    benchmark_paths: Iterable[Path | str],
) -> None:
    """Fail if any training record reuses a held-out benchmark decision ID."""

    held_out_ids = benchmark_decision_ids(benchmark_paths)
    leaked = sorted(
        {
            record.decision_id
            for record in records
            if record.decision_id in held_out_ids
        }
    )
    if leaked:
        raise RecordValidationError(
            "held-out benchmark decisions must not be ingested into training: "
            + ", ".join(leaked)
        )
