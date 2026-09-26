"""Offline pilot execution and reporting for held-out benchmark cases.

This module is deliberately engine-independent. It evaluates a pilot against
already-captured :class:`BenchmarkCase` records and records enough provenance to
compare implementations without starting Argentum, Forge, or any other runtime.

Held-out judgments and post-decision provenance are scoring evidence, not pilot
input. The runner projects each case onto :class:`BenchmarkInput` before calling
the pilot so a benchmark adapter cannot accidentally read the reference judgment,
recorded choice/outcome, deck identity, or source pilot provenance.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Protocol, Tuple, Union

from .benchmark import BenchmarkCase, BenchmarkEntry, BenchmarkScenario, benchmark_suite_identity, score_action
from .records import ActionRecord

BENCHMARK_REPORT_VERSION = 1


@dataclass(frozen=True)
class BenchmarkInput:
    """Information that was available to the evaluated pilot at decision time.

    This intentionally mirrors the leakage boundary used by dataset export. It
    excludes benchmark judgments/categories/tags plus all post-decision and
    provenance fields carried by ``DecisionRecord``.
    """

    decision_type: str
    seat: int
    observation_schema: str
    observation: Dict[str, Any]
    legal_actions: Tuple[ActionRecord, ...]


@dataclass(frozen=True)
class PilotDecision:
    """One offline pilot answer plus optional usage/provenance metadata."""

    action_id: str
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    escalated: bool = False
    escalation_reason: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


PilotResult = Union[str, PilotDecision]


class BenchmarkPilot(Protocol):
    """Minimal interface required by the leakage-safe offline benchmark runner."""

    name: str
    version: str

    def choose_action(self, benchmark_input: BenchmarkInput) -> PilotResult:
        ...


@dataclass(frozen=True)
class CallablePilot:
    """Adapter for small benchmark-only pilot callables."""

    name: str
    version: str
    chooser: Callable[[BenchmarkInput], PilotResult]

    def choose_action(self, benchmark_input: BenchmarkInput) -> PilotResult:
        return self.chooser(benchmark_input)


def benchmark_input_for_case(case: BenchmarkEntry) -> BenchmarkInput:
    """Project a held-out case onto only the information available at choice time.

    Nested mappings are deep-copied so a mutable benchmark adapter cannot alter the
    held-out case or scoring evidence while it is being evaluated.
    """

    case.validate()
    if isinstance(case, BenchmarkCase):
        decision_type = case.decision.decision_type
        seat = case.decision.seat
        observation_schema = case.decision.observation_schema
        observation = case.decision.observation
        legal_actions = case.decision.legal_actions
    else:
        decision_type = case.decision_type
        seat = case.seat
        observation_schema = case.observation_schema
        observation = case.observation
        legal_actions = case.legal_actions
    return BenchmarkInput(
        decision_type=decision_type,
        seat=seat,
        observation_schema=observation_schema,
        observation=deepcopy(observation),
        legal_actions=tuple(
            ActionRecord(
                action_id=action.action_id,
                payload=deepcopy(action.payload),
                label=action.label,
            )
            for action in legal_actions
        ),
    )


def _normalize_decision(value: PilotResult) -> PilotDecision:
    if isinstance(value, str):
        decision = PilotDecision(action_id=value)
    elif isinstance(value, PilotDecision):
        decision = value
    else:
        raise TypeError("pilot must return an action id string or PilotDecision")

    if not isinstance(decision.action_id, str) or not decision.action_id:
        raise ValueError("pilot action_id must be a non-empty string")
    for field_name, count in (
        ("input_tokens", decision.input_tokens),
        ("output_tokens", decision.output_tokens),
    ):
        if count is not None and (not isinstance(count, int) or isinstance(count, bool) or count < 0):
            raise ValueError(f"pilot {field_name} must be a non-negative integer or null")
    if decision.cost_usd is not None and (
        not isinstance(decision.cost_usd, (int, float))
        or isinstance(decision.cost_usd, bool)
        or decision.cost_usd < 0
    ):
        raise ValueError("pilot cost_usd must be a non-negative number or null")
    if type(decision.escalated) is not bool:
        raise ValueError("pilot escalated must be a boolean")
    if decision.escalation_reason is not None and (
        not isinstance(decision.escalation_reason, str) or not decision.escalation_reason
    ):
        raise ValueError("pilot escalation_reason must be a non-empty string or null")
    if decision.escalation_reason is not None and not decision.escalated:
        raise ValueError("pilot escalation_reason requires escalated=true")
    if not isinstance(decision.metadata, Mapping):
        raise ValueError("pilot metadata must be a mapping")
    return decision


def _summarize(group: list[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(group)
    if not total:
        return {
            "cases": 0,
            "legal_rate": None,
            "invalid_output_rate": None,
            "preferred_rate": None,
            "error_rate": None,
            "escalation_rate": None,
        }
    return {
        "cases": total,
        "legal_rate": sum(bool(row["legal"]) for row in group) / total,
        "invalid_output_rate": sum(bool(row["invalid_output"]) for row in group) / total,
        "preferred_rate": sum(bool(row["preferred"]) for row in group) / total,
        "error_rate": sum(row["error"] is not None for row in group) / total,
        "escalation_rate": sum(bool(row["escalated"]) for row in group) / total,
    }


def run_benchmark(cases: Iterable[BenchmarkEntry], pilot: BenchmarkPilot) -> Dict[str, Any]:
    """Run a pilot over held-out cases and return a versioned offline report.

    Pilot failures are evidence, not reasons to substitute another policy. A
    model/adapter exception or malformed response is recorded as an invalid
    output for that case and benchmark execution continues with later cases.

    The pilot receives only :class:`BenchmarkInput`; reference judgments and
    post-decision provenance remain runner-side for scoring and reporting. The
    report carries a fingerprint of the exact held-out suite so comparisons fail
    closed instead of treating different benchmark evidence as equivalent.
    """

    if not isinstance(pilot.name, str) or not pilot.name:
        raise ValueError("pilot.name must be a non-empty string")
    if not isinstance(pilot.version, str) or not pilot.version:
        raise ValueError("pilot.version must be a non-empty string")

    case_list = list(cases)
    benchmark_identity = benchmark_suite_identity(case_list)

    rows: list[Dict[str, Any]] = []
    by_category: Dict[str, list[Dict[str, Any]]] = defaultdict(list)
    total_latency_ms = 0.0
    input_tokens = 0
    output_tokens = 0
    cost_usd = 0.0
    saw_input_tokens = False
    saw_output_tokens = False
    saw_cost = False

    for case in case_list:
        benchmark_input = benchmark_input_for_case(case)
        started = perf_counter()
        try:
            decision = _normalize_decision(pilot.choose_action(benchmark_input))
            error = None
        except Exception as exc:  # benchmark evidence must retain pilot failures
            decision = PilotDecision(action_id="")
            error = f"{type(exc).__name__}: {exc}"
        latency_ms = (perf_counter() - started) * 1000.0
        total_latency_ms += latency_ms

        if decision.input_tokens is not None:
            input_tokens += decision.input_tokens
            saw_input_tokens = True
        if decision.output_tokens is not None:
            output_tokens += decision.output_tokens
            saw_output_tokens = True
        if decision.cost_usd is not None:
            cost_usd += float(decision.cost_usd)
            saw_cost = True

        score = score_action(case, decision.action_id)
        row = {
            **score,
            "category": case.category,
            "selected_action_id": decision.action_id,
            "invalid_output": not bool(score["legal"]),
            "error": error,
            "latency_ms": latency_ms,
            "input_tokens": decision.input_tokens,
            "output_tokens": decision.output_tokens,
            "cost_usd": decision.cost_usd,
            "escalated": decision.escalated,
            "escalation_reason": decision.escalation_reason,
            "pilot_metadata": dict(decision.metadata),
        }
        rows.append(row)
        by_category[case.category].append(row)

    return {
        "schema_version": BENCHMARK_REPORT_VERSION,
        "benchmark": benchmark_identity,
        "pilot": {"name": pilot.name, "version": pilot.version},
        "summary": {
            **_summarize(rows),
            "mean_latency_ms": (total_latency_ms / len(rows)) if rows else None,
            "input_tokens": input_tokens if saw_input_tokens else None,
            "output_tokens": output_tokens if saw_output_tokens else None,
            "cost_usd": cost_usd if saw_cost else None,
        },
        "categories": {
            category: _summarize(group)
            for category, group in sorted(by_category.items())
        },
        "cases": rows,
    }


def first_legal_pilot(name: str = "first-legal", version: str = "1") -> CallablePilot:
    """Public deterministic baseline that always chooses the first legal action."""

    return CallablePilot(name, version, lambda benchmark_input: benchmark_input.legal_actions[0].action_id)
