"""Fail-closed comparison of versioned Commander Gym benchmark reports.

The comparison layer is deliberately offline and engine-independent. It only compares
reports produced against the exact same fingerprinted held-out benchmark suite. It
does not reinterpret Argentum actions or introduce a strategy/scoring policy beyond
the benchmark's existing legal/preferred measurements.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Any, Dict, Mapping, Sequence, Tuple

from .benchmark import BENCHMARK_SUITE_IDENTITY_SCHEMA
from .benchmark_runner import BENCHMARK_REPORT_VERSION

BENCHMARK_COMPARISON_VERSION = 1


class BenchmarkComparisonError(ValueError):
    """Raised when benchmark reports are not safe to compare."""


def _require_report(report: Mapping[str, Any], label: str) -> Tuple[Dict[str, Any], list[Dict[str, Any]]]:
    if not isinstance(report, Mapping):
        raise BenchmarkComparisonError(f"{label} report must be a mapping")
    if report.get("schema_version") != BENCHMARK_REPORT_VERSION:
        raise BenchmarkComparisonError(
            f"{label} report schema_version must be {BENCHMARK_REPORT_VERSION}"
        )

    benchmark = report.get("benchmark")
    if not isinstance(benchmark, Mapping):
        raise BenchmarkComparisonError(f"{label} report is missing benchmark identity")
    if benchmark.get("schema") != BENCHMARK_SUITE_IDENTITY_SCHEMA:
        raise BenchmarkComparisonError(
            f"{label} report benchmark schema must be {BENCHMARK_SUITE_IDENTITY_SCHEMA}"
        )
    fingerprint = benchmark.get("fingerprint")
    if not isinstance(fingerprint, str) or not fingerprint.startswith("sha256:"):
        raise BenchmarkComparisonError(f"{label} report has invalid benchmark fingerprint")
    case_count = benchmark.get("case_count")
    if not isinstance(case_count, int) or isinstance(case_count, bool) or case_count < 0:
        raise BenchmarkComparisonError(f"{label} report has invalid benchmark case_count")

    pilot = report.get("pilot")
    if not isinstance(pilot, Mapping):
        raise BenchmarkComparisonError(f"{label} report is missing pilot provenance")
    for field_name in ("name", "version"):
        value = pilot.get(field_name)
        if not isinstance(value, str) or not value:
            raise BenchmarkComparisonError(
                f"{label} report pilot.{field_name} must be a non-empty string"
            )

    raw_cases = report.get("cases")
    if not isinstance(raw_cases, list):
        raise BenchmarkComparisonError(f"{label} report cases must be an array")
    if len(raw_cases) != case_count:
        raise BenchmarkComparisonError(
            f"{label} report case count does not match benchmark identity"
        )

    rows: list[Dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_cases:
        if not isinstance(raw, Mapping):
            raise BenchmarkComparisonError(f"{label} report case rows must be objects")
        case_id = raw.get("case_id")
        category = raw.get("category")
        selected = raw.get("selected_action_id")
        if not isinstance(case_id, str) or not case_id:
            raise BenchmarkComparisonError(f"{label} report case_id must be non-empty")
        if case_id in seen:
            raise BenchmarkComparisonError(f"{label} report repeats case_id {case_id!r}")
        seen.add(case_id)
        if not isinstance(category, str) or not category:
            raise BenchmarkComparisonError(
                f"{label} report category for {case_id!r} must be non-empty"
            )
        if not isinstance(selected, str):
            raise BenchmarkComparisonError(
                f"{label} report selected_action_id for {case_id!r} must be a string"
            )
        for field_name in ("legal", "preferred", "invalid_output"):
            if not isinstance(raw.get(field_name), bool):
                raise BenchmarkComparisonError(
                    f"{label} report {field_name} for {case_id!r} must be boolean"
                )
        error = raw.get("error")
        if error is not None and not isinstance(error, str):
            raise BenchmarkComparisonError(
                f"{label} report error for {case_id!r} must be a string or null"
            )
        rows.append(dict(raw))

    return dict(benchmark), rows


def _rate(rows: Sequence[Mapping[str, Any]], field: str) -> float | None:
    if not rows:
        return None
    if field == "error":
        return sum(row.get("error") is not None for row in rows) / len(rows)
    return sum(bool(row[field]) for row in rows) / len(rows)


def _metric_pair(baseline: float | int | None, candidate: float | int | None) -> Dict[str, Any]:
    delta = None
    if baseline is not None and candidate is not None:
        delta = candidate - baseline
    return {"baseline": baseline, "candidate": candidate, "delta": delta}


def _summary_for_rows(
    baseline_rows: Sequence[Mapping[str, Any]],
    candidate_rows: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    improved = 0
    regressed = 0
    selection_changes = 0
    candidate_by_id = {row["case_id"]: row for row in candidate_rows}
    for baseline in baseline_rows:
        candidate = candidate_by_id[baseline["case_id"]]
        baseline_preferred = bool(baseline["preferred"])
        candidate_preferred = bool(candidate["preferred"])
        if candidate_preferred and not baseline_preferred:
            improved += 1
        elif baseline_preferred and not candidate_preferred:
            regressed += 1
        if baseline["selected_action_id"] != candidate["selected_action_id"]:
            selection_changes += 1

    return {
        "cases": len(baseline_rows),
        "preferred_rate": _metric_pair(
            _rate(baseline_rows, "preferred"), _rate(candidate_rows, "preferred")
        ),
        "legal_rate": _metric_pair(_rate(baseline_rows, "legal"), _rate(candidate_rows, "legal")),
        "invalid_output_rate": _metric_pair(
            _rate(baseline_rows, "invalid_output"),
            _rate(candidate_rows, "invalid_output"),
        ),
        "error_rate": _metric_pair(_rate(baseline_rows, "error"), _rate(candidate_rows, "error")),
        "preferred_improvements": improved,
        "preferred_regressions": regressed,
        "selection_changes": selection_changes,
    }


def _telemetry_metric(report: Mapping[str, Any], field: str) -> float | int | None:
    summary = report.get("summary")
    if not isinstance(summary, Mapping):
        return None
    value = summary.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BenchmarkComparisonError(f"summary.{field} must be numeric or null")
    return value


def compare_benchmark_reports(
    baseline_report: Mapping[str, Any],
    candidate_report: Mapping[str, Any],
) -> Dict[str, Any]:
    """Compare two reports only when they used exactly the same benchmark evidence.

    Preferred-action improvement/regression is intentionally the only qualitative
    classification. Legality, invalid output, errors, selections, and telemetry are
    reported separately so this research utility does not invent a new pilot policy.
    """

    baseline_identity, baseline_rows = _require_report(baseline_report, "baseline")
    candidate_identity, candidate_rows = _require_report(candidate_report, "candidate")
    if baseline_identity != candidate_identity:
        raise BenchmarkComparisonError(
            "benchmark identities differ; refusing a false-equivalence comparison"
        )

    baseline_by_id = {row["case_id"]: row for row in baseline_rows}
    candidate_by_id = {row["case_id"]: row for row in candidate_rows}
    if set(baseline_by_id) != set(candidate_by_id):
        raise BenchmarkComparisonError(
            "benchmark case ids differ despite matching identity; report is inconsistent"
        )

    comparison_rows: list[Dict[str, Any]] = []
    baseline_by_category: Dict[str, list[Dict[str, Any]]] = defaultdict(list)
    candidate_by_category: Dict[str, list[Dict[str, Any]]] = defaultdict(list)

    for case_id in sorted(baseline_by_id):
        baseline = baseline_by_id[case_id]
        candidate = candidate_by_id[case_id]
        if baseline["category"] != candidate["category"]:
            raise BenchmarkComparisonError(
                f"category differs for {case_id!r} despite matching benchmark identity"
            )
        category = baseline["category"]
        baseline_by_category[category].append(baseline)
        candidate_by_category[category].append(candidate)

        if candidate["preferred"] and not baseline["preferred"]:
            preferred_change = "improved"
        elif baseline["preferred"] and not candidate["preferred"]:
            preferred_change = "regressed"
        else:
            preferred_change = "unchanged"

        comparison_rows.append(
            {
                "case_id": case_id,
                "category": category,
                "preferred_change": preferred_change,
                "selection_changed": baseline["selected_action_id"]
                != candidate["selected_action_id"],
                "baseline": {
                    "selected_action_id": baseline["selected_action_id"],
                    "legal": baseline["legal"],
                    "preferred": baseline["preferred"],
                    "rank": baseline.get("rank"),
                    "invalid_output": baseline["invalid_output"],
                    "error": baseline.get("error"),
                },
                "candidate": {
                    "selected_action_id": candidate["selected_action_id"],
                    "legal": candidate["legal"],
                    "preferred": candidate["preferred"],
                    "rank": candidate.get("rank"),
                    "invalid_output": candidate["invalid_output"],
                    "error": candidate.get("error"),
                },
            }
        )

    categories = {
        category: _summary_for_rows(
            baseline_by_category[category], candidate_by_category[category]
        )
        for category in sorted(baseline_by_category)
    }
    telemetry_fields = ("mean_latency_ms", "input_tokens", "output_tokens", "cost_usd")

    return {
        "schema_version": BENCHMARK_COMPARISON_VERSION,
        "benchmark": deepcopy(baseline_identity),
        "baseline_pilot": deepcopy(dict(baseline_report["pilot"])),
        "candidate_pilot": deepcopy(dict(candidate_report["pilot"])),
        "summary": _summary_for_rows(baseline_rows, candidate_rows),
        "telemetry": {
            field: _metric_pair(
                _telemetry_metric(baseline_report, field),
                _telemetry_metric(candidate_report, field),
            )
            for field in telemetry_fields
        },
        "categories": categories,
        "cases": comparison_rows,
    }
