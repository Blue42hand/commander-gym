"""Guards preventing held-out benchmark leakage into training datasets."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Set

from .benchmark import (
    BenchmarkCase,
    BenchmarkScenario,
    benchmark_entry_input_identity,
    load_jsonl,
    scenario_input_identity,
)
from .records import DecisionRecord, RecordValidationError


def benchmark_decision_ids(paths: Iterable[Path | str]) -> Set[str]:
    """Return recorded decision IDs reserved by held-out benchmark artifacts."""

    decision_ids: Set[str] = set()
    for path in paths:
        for case in load_jsonl(path):
            case.validate()
            if isinstance(case, BenchmarkCase):
                decision_ids.add(case.decision.decision_id)
    return decision_ids


def benchmark_scenario_input_fingerprints(paths: Iterable[Path | str]) -> Set[str]:
    """Return judgment-independent input fingerprints for held-out scenarios."""

    fingerprints: Set[str] = set()
    for path in paths:
        for case in load_jsonl(path):
            case.validate()
            if isinstance(case, BenchmarkScenario):
                fingerprints.add(benchmark_entry_input_identity(case)["fingerprint"])
    return fingerprints


def _record_input_fingerprint(record: DecisionRecord) -> str:
    record.validate()
    return scenario_input_identity(
        decision_type=record.decision_type,
        seat=record.seat,
        observation_schema=record.observation_schema,
        observation=record.observation,
        legal_actions=record.legal_actions,
    )["fingerprint"]


def assert_training_records_exclude_benchmark(
    records: Iterable[DecisionRecord],
    benchmark_paths: Iterable[Path | str],
) -> None:
    """Fail if training reuses held-out recorded decisions or scenario inputs."""

    paths = list(benchmark_paths)
    held_out_ids = benchmark_decision_ids(paths)
    held_out_scenario_inputs = benchmark_scenario_input_fingerprints(paths)

    leaked_decisions = set()
    leaked_scenarios = set()
    for record in records:
        if record.decision_id in held_out_ids:
            leaked_decisions.add(record.decision_id)
        if _record_input_fingerprint(record) in held_out_scenario_inputs:
            leaked_scenarios.add(record.decision_id)

    if leaked_decisions:
        raise RecordValidationError(
            "held-out benchmark decisions must not be ingested into training: "
            + ", ".join(sorted(leaked_decisions))
        )
    if leaked_scenarios:
        raise RecordValidationError(
            "held-out benchmark scenario inputs must not be ingested into training: "
            + ", ".join(sorted(leaked_scenarios))
        )
