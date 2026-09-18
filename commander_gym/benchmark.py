"""Engine-independent held-out benchmark records and scoring helpers.

Benchmark cases are durable research artifacts owned by Commander Gym. They are
intentionally loadable and scoreable without an engine process, transport layer,
or private deck corpus.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .records import DecisionRecord, RecordValidationError

BENCHMARK_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class BenchmarkJudgment:
    """Reference judgment for one held-out strategic decision."""

    preferred_action_ids: List[str]
    ranked_action_ids: List[str] = field(default_factory=list)
    rationale: Optional[str] = None
    provenance: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BenchmarkJudgment":
        preferred = value.get("preferred_action_ids", [])
        ranked = value.get("ranked_action_ids", [])
        if not isinstance(preferred, list) or not all(
            isinstance(action_id, str) and action_id for action_id in preferred
        ):
            raise RecordValidationError(
                "judgment.preferred_action_ids must be an array of non-empty strings"
            )
        if not isinstance(ranked, list) or not all(
            isinstance(action_id, str) and action_id for action_id in ranked
        ):
            raise RecordValidationError(
                "judgment.ranked_action_ids must be an array of non-empty strings"
            )
        rationale = value.get("rationale")
        if rationale is not None and not isinstance(rationale, str):
            raise RecordValidationError("judgment.rationale must be a string or null")
        provenance = value.get("provenance", {})
        if not isinstance(provenance, dict):
            raise RecordValidationError("judgment.provenance must be an object")
        return cls(
            preferred_action_ids=list(preferred),
            ranked_action_ids=list(ranked),
            rationale=rationale,
            provenance=dict(provenance),
        )


@dataclass(frozen=True)
class BenchmarkCase:
    """One explicitly held-out benchmark decision and its reference judgment."""

    case_id: str
    category: str
    decision: DecisionRecord
    judgment: BenchmarkJudgment
    held_out: bool = True
    tags: List[str] = field(default_factory=list)
    schema_version: int = BENCHMARK_SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != BENCHMARK_SCHEMA_VERSION:
            raise RecordValidationError(
                f"unsupported benchmark schema_version {self.schema_version}; "
                f"expected {BENCHMARK_SCHEMA_VERSION}"
            )
        if not isinstance(self.case_id, str) or not self.case_id:
            raise RecordValidationError("case_id must be a non-empty string")
        if not isinstance(self.category, str) or not self.category:
            raise RecordValidationError("category must be a non-empty string")
        if self.held_out is not True:
            raise RecordValidationError("benchmark cases must be marked held_out=true")
        if not isinstance(self.tags, list) or not all(
            isinstance(tag, str) and tag for tag in self.tags
        ):
            raise RecordValidationError("tags must be an array of non-empty strings")

        self.decision.validate()
        legal = {action.action_id for action in self.decision.legal_actions}
        judgment_ids = self.judgment.preferred_action_ids + self.judgment.ranked_action_ids
        for action_id in judgment_ids:
            if action_id not in legal:
                raise RecordValidationError(
                    f"benchmark judgment references non-legal action {action_id!r}"
                )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BenchmarkCase":
        decision_value = value.get("decision")
        judgment_value = value.get("judgment")
        tags_value = value.get("tags", [])
        if not isinstance(decision_value, dict):
            raise RecordValidationError("decision must be an object")
        if not isinstance(judgment_value, dict):
            raise RecordValidationError("judgment must be an object")
        if not isinstance(tags_value, list):
            raise RecordValidationError("tags must be an array")

        case = cls(
            schema_version=value.get("schema_version", BENCHMARK_SCHEMA_VERSION),
            case_id=value.get("case_id"),
            category=value.get("category"),
            decision=DecisionRecord.from_dict(decision_value),
            judgment=BenchmarkJudgment.from_dict(judgment_value),
            held_out=value.get("held_out", True),
            tags=list(tags_value),
        )
        case.validate()
        return case


def load_jsonl(path: Path | str) -> List[BenchmarkCase]:
    """Load a benchmark JSONL file, failing closed on the first invalid row."""

    cases: List[BenchmarkCase] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            if not raw.strip():
                continue
            try:
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise RecordValidationError("benchmark row must be an object")
                cases.append(BenchmarkCase.from_dict(value))
            except (json.JSONDecodeError, RecordValidationError, TypeError, ValueError) as exc:
                raise RecordValidationError(f"{path}:{line_number}: {exc}") from exc
    return cases


def score_action(case: BenchmarkCase, action_id: str) -> Dict[str, Any]:
    """Score one selected action without assuming a single objective ground truth."""

    case.validate()
    legal = {action.action_id for action in case.decision.legal_actions}
    is_legal = action_id in legal
    preferred = action_id in set(case.judgment.preferred_action_ids)
    rank = None
    if action_id in case.judgment.ranked_action_ids:
        rank = case.judgment.ranked_action_ids.index(action_id) + 1
    return {
        "case_id": case.case_id,
        "legal": is_legal,
        "preferred": preferred,
        "rank": rank,
    }


def summarize_scores(scores: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Summarize legality and preferred-action rates for benchmark results."""

    total = len(scores)
    if total == 0:
        return {"cases": 0, "legal_rate": None, "preferred_rate": None}
    legal = sum(bool(score.get("legal")) for score in scores)
    preferred = sum(bool(score.get("preferred")) for score in scores)
    return {
        "cases": total,
        "legal_rate": legal / total,
        "preferred_rate": preferred / total,
    }
