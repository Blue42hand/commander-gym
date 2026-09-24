"""Preparation-only reporting for the #114 1v1-to-Commander transfer benchmark.

This module composes the existing model-independent held-out benchmark runner rather
than defining another policy interface. It adds the transfer experiment's cohort
roles, capability slices, source-group sidecar identity, and cross-cohort
disagreement reporting.

External model/dataset provenance remains owned by #113. This module stores only
opaque provenance artifact IDs and an exact case->source-group mapping so it can
consume that contract without redefining it.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Dict, Mapping, Sequence, Tuple

from .benchmark import BenchmarkCase, benchmark_suite_identity
from .benchmark_runner import BENCHMARK_REPORT_VERSION

TRANSFER_BENCHMARK_SCHEMA_VERSION = 1
TRANSFER_CAPABILITY_TAG_PREFIX = "capability:"
TRANSFER_CAPABILITIES = (
    "mulligan",
    "sequencing",
    "combat_attack",
    "combat_block",
    "resource_use",
    "interaction_timing",
    "threat_assessment",
    "deck_plan",
    "recovery",
    "multiplayer_interaction_allocation",
    "commander_engine",
    "long_game",
)
TRANSFER_COHORT_ROLES = (
    "baseline",
    "one_v_one_enriched",
    "commander_adapted",
    "frontier_reference",
)


class TransferBenchmarkError(ValueError):
    """Raised when transfer-benchmark evidence cannot be compared safely."""


@dataclass(frozen=True)
class TransferCohort:
    """One evaluated transfer cohort and its exact external/model provenance refs."""

    role: str
    report: Mapping[str, Any]
    provenance_artifact_ids: Tuple[str, ...]

    def validate(self) -> None:
        if self.role not in TRANSFER_COHORT_ROLES:
            raise TransferBenchmarkError(
                f"cohort role must be one of {list(TRANSFER_COHORT_ROLES)!r}"
            )
        if not isinstance(self.report, Mapping):
            raise TransferBenchmarkError("cohort report must be an object")
        if (
            not isinstance(self.provenance_artifact_ids, tuple)
            or not self.provenance_artifact_ids
            or any(
                not isinstance(item, str) or not item
                for item in self.provenance_artifact_ids
            )
        ):
            raise TransferBenchmarkError(
                "cohort provenance_artifact_ids must be a non-empty tuple of strings"
            )
        if len(self.provenance_artifact_ids) != len(set(self.provenance_artifact_ids)):
            raise TransferBenchmarkError("cohort provenance_artifact_ids must be unique")


def capability_tag(capability: str) -> str:
    if capability not in TRANSFER_CAPABILITIES:
        raise TransferBenchmarkError(f"unknown transfer capability {capability!r}")
    return f"{TRANSFER_CAPABILITY_TAG_PREFIX}{capability}"


def _case_capabilities(case: BenchmarkCase) -> Tuple[str, ...]:
    case.validate()
    capabilities = []
    for tag in case.tags:
        if not tag.startswith(TRANSFER_CAPABILITY_TAG_PREFIX):
            continue
        capability = tag[len(TRANSFER_CAPABILITY_TAG_PREFIX) :]
        if capability not in TRANSFER_CAPABILITIES:
            raise TransferBenchmarkError(
                f"case {case.case_id!r} uses unknown capability tag {tag!r}"
            )
        capabilities.append(capability)
    if not capabilities:
        raise TransferBenchmarkError(
            f"case {case.case_id!r} must carry at least one transfer capability tag"
        )
    return tuple(sorted(set(capabilities)))


def _validate_source_groups(
    cases: Sequence[BenchmarkCase],
    source_group_by_case: Mapping[str, str],
) -> Dict[str, str]:
    if not isinstance(source_group_by_case, Mapping):
        raise TransferBenchmarkError("source_group_by_case must be an object")
    expected = {case.case_id for case in cases}
    actual = set(source_group_by_case)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise TransferBenchmarkError(
            f"source-group membership must exactly match benchmark cases; "
            f"missing={missing!r} extra={extra!r}"
        )
    normalized: Dict[str, str] = {}
    for case_id, group_id in source_group_by_case.items():
        if not isinstance(group_id, str) or not group_id:
            raise TransferBenchmarkError(
                f"source group for {case_id!r} must be a non-empty string"
            )
        normalized[case_id] = group_id
    return normalized


def _source_group_identity(source_group_by_case: Mapping[str, str]) -> Dict[str, Any]:
    payload = {
        "schema": "commander-gym-transfer-source-groups@v1",
        "membership": [
            {"case_id": case_id, "source_group_id": source_group_by_case[case_id]}
            for case_id in sorted(source_group_by_case)
        ],
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return {
        "schema": payload["schema"],
        "case_count": len(source_group_by_case),
        "group_count": len(set(source_group_by_case.values())),
        "fingerprint": f"sha256:{hashlib.sha256(encoded).hexdigest()}",
    }


def _validate_report(
    report: Mapping[str, Any],
    benchmark_identity: Mapping[str, Any],
    expected_case_ids: set[str],
    label: str,
) -> Dict[str, Dict[str, Any]]:
    if report.get("schema_version") != BENCHMARK_REPORT_VERSION:
        raise TransferBenchmarkError(
            f"{label} report schema_version must be {BENCHMARK_REPORT_VERSION}"
        )
    if report.get("benchmark") != benchmark_identity:
        raise TransferBenchmarkError(
            f"{label} report benchmark identity differs from the transfer suite"
        )
    pilot = report.get("pilot")
    if not isinstance(pilot, Mapping):
        raise TransferBenchmarkError(f"{label} report is missing pilot identity")
    for field_name in ("name", "version"):
        value = pilot.get(field_name)
        if not isinstance(value, str) or not value:
            raise TransferBenchmarkError(
                f"{label} pilot.{field_name} must be a non-empty string"
            )
    raw_rows = report.get("cases")
    if not isinstance(raw_rows, list):
        raise TransferBenchmarkError(f"{label} report cases must be an array")
    rows: Dict[str, Dict[str, Any]] = {}
    for raw in raw_rows:
        if not isinstance(raw, Mapping):
            raise TransferBenchmarkError(f"{label} report case rows must be objects")
        case_id = raw.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise TransferBenchmarkError(f"{label} report case_id must be non-empty")
        if case_id in rows:
            raise TransferBenchmarkError(f"{label} report repeats case_id {case_id!r}")
        if not isinstance(raw.get("selected_action_id"), str):
            raise TransferBenchmarkError(
                f"{label} selected_action_id for {case_id!r} must be a string"
            )
        for field_name in ("legal", "preferred", "invalid_output", "escalated"):
            if not isinstance(raw.get(field_name), bool):
                raise TransferBenchmarkError(
                    f"{label} {field_name} for {case_id!r} must be boolean"
                )
        error = raw.get("error")
        if error is not None and not isinstance(error, str):
            raise TransferBenchmarkError(
                f"{label} error for {case_id!r} must be a string or null"
            )
        rows[case_id] = dict(raw)
    if set(rows) != expected_case_ids:
        raise TransferBenchmarkError(
            f"{label} report case membership differs from the transfer suite"
        )
    return rows


def _rate(rows: Sequence[Mapping[str, Any]], field_name: str) -> float | None:
    if not rows:
        return None
    if field_name == "error":
        return sum(row.get("error") is not None for row in rows) / len(rows)
    return sum(bool(row[field_name]) for row in rows) / len(rows)


def _numeric_values(rows: Sequence[Mapping[str, Any]], field_name: str) -> list[float]:
    values = []
    for row in rows:
        value = row.get(field_name)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TransferBenchmarkError(
                f"benchmark row {field_name} must be numeric or null"
            )
        values.append(float(value))
    return values


def _cohort_metrics(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    latency = _numeric_values(rows, "latency_ms")
    input_tokens = _numeric_values(rows, "input_tokens")
    output_tokens = _numeric_values(rows, "output_tokens")
    cost = _numeric_values(rows, "cost_usd")
    return {
        "cases": len(rows),
        "preferred_rate": _rate(rows, "preferred"),
        "legal_rate": _rate(rows, "legal"),
        "invalid_output_rate": _rate(rows, "invalid_output"),
        "error_rate": _rate(rows, "error"),
        "escalation_rate": _rate(rows, "escalated"),
        "mean_latency_ms": (sum(latency) / len(latency)) if latency else None,
        "input_tokens": int(sum(input_tokens)) if input_tokens else None,
        "output_tokens": int(sum(output_tokens)) if output_tokens else None,
        "cost_usd": sum(cost) if cost else None,
    }


def _disagreement(
    case_ids: Sequence[str],
    rows_by_role: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> Dict[str, Any]:
    if not case_ids:
        return {
            "cases": 0,
            "any_disagreement_rate": None,
            "all_agree_rate": None,
            "case_ids_with_disagreement": [],
        }
    disagreed = []
    for case_id in case_ids:
        selections = {
            rows_by_role[role][case_id]["selected_action_id"]
            for role in TRANSFER_COHORT_ROLES
        }
        if len(selections) > 1:
            disagreed.append(case_id)
    rate = len(disagreed) / len(case_ids)
    return {
        "cases": len(case_ids),
        "any_disagreement_rate": rate,
        "all_agree_rate": 1.0 - rate,
        "case_ids_with_disagreement": sorted(disagreed),
    }


def build_transfer_benchmark_summary(
    cases: Sequence[BenchmarkCase],
    cohorts: Sequence[TransferCohort],
    *,
    source_group_by_case: Mapping[str, str],
) -> Dict[str, Any]:
    """Build one exact four-cohort #114 comparison over already-frozen cases.

    This is preparation/reporting only: it does not start Argentum, train a model,
    or turn the frontier reference into ground truth.
    """

    case_list = list(cases)
    if not case_list:
        raise TransferBenchmarkError("transfer benchmark requires at least one case")
    benchmark_identity = benchmark_suite_identity(case_list)
    capabilities_by_case = {
        case.case_id: _case_capabilities(case)
        for case in case_list
    }
    source_groups = _validate_source_groups(case_list, source_group_by_case)

    cohort_by_role: Dict[str, TransferCohort] = {}
    for cohort in cohorts:
        if not isinstance(cohort, TransferCohort):
            raise TransferBenchmarkError("cohorts must contain TransferCohort values")
        cohort.validate()
        if cohort.role in cohort_by_role:
            raise TransferBenchmarkError(f"duplicate transfer cohort role {cohort.role!r}")
        cohort_by_role[cohort.role] = cohort
    if set(cohort_by_role) != set(TRANSFER_COHORT_ROLES):
        missing = sorted(set(TRANSFER_COHORT_ROLES) - set(cohort_by_role))
        extra = sorted(set(cohort_by_role) - set(TRANSFER_COHORT_ROLES))
        raise TransferBenchmarkError(
            f"transfer benchmark requires exactly the canonical cohorts; "
            f"missing={missing!r} extra={extra!r}"
        )

    case_ids = {case.case_id for case in case_list}
    rows_by_role = {
        role: _validate_report(
            cohort_by_role[role].report,
            benchmark_identity,
            case_ids,
            role,
        )
        for role in TRANSFER_COHORT_ROLES
    }

    overall_metrics = {
        role: _cohort_metrics([rows_by_role[role][case_id] for case_id in sorted(case_ids)])
        for role in TRANSFER_COHORT_ROLES
    }
    baseline_preferred = overall_metrics["baseline"]["preferred_rate"]
    for role in TRANSFER_COHORT_ROLES:
        preferred = overall_metrics[role]["preferred_rate"]
        overall_metrics[role]["preferred_delta_vs_baseline"] = (
            None
            if baseline_preferred is None or preferred is None
            else preferred - baseline_preferred
        )

    capability_summaries: Dict[str, Any] = {}
    for capability in TRANSFER_CAPABILITIES:
        capability_case_ids = sorted(
            case_id
            for case_id, capabilities in capabilities_by_case.items()
            if capability in capabilities
        )
        if not capability_case_ids:
            continue
        cohort_metrics = {
            role: _cohort_metrics(
                [rows_by_role[role][case_id] for case_id in capability_case_ids]
            )
            for role in TRANSFER_COHORT_ROLES
        }
        baseline = cohort_metrics["baseline"]["preferred_rate"]
        for role in TRANSFER_COHORT_ROLES:
            preferred = cohort_metrics[role]["preferred_rate"]
            cohort_metrics[role]["preferred_delta_vs_baseline"] = (
                None if baseline is None or preferred is None else preferred - baseline
            )
        capability_summaries[capability] = {
            "cases": len(capability_case_ids),
            "case_ids": capability_case_ids,
            "cohorts": cohort_metrics,
            "disagreement": _disagreement(capability_case_ids, rows_by_role),
        }

    return {
        "schema_version": TRANSFER_BENCHMARK_SCHEMA_VERSION,
        "benchmark": benchmark_identity,
        "source_groups": _source_group_identity(source_groups),
        "cohorts": {
            role: {
                "pilot": dict(cohort_by_role[role].report["pilot"]),
                "provenance_artifact_ids": list(
                    cohort_by_role[role].provenance_artifact_ids
                ),
                "metrics": overall_metrics[role],
            }
            for role in TRANSFER_COHORT_ROLES
        },
        "capabilities": capability_summaries,
        "disagreement": _disagreement(sorted(case_ids), rows_by_role),
        "notes": {
            "frontier_reference_is_ground_truth": False,
            "source_group_semantics_owned_by": "commander-gym#113",
        },
    }
