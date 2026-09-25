"""Engine-independent held-out benchmark records and scoring helpers.

Benchmark cases are durable research artifacts owned by Commander Gym. They are
intentionally loadable and scoreable without an engine process, transport layer,
or private deck corpus.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

from .records import ActionRecord, DecisionRecord, RecordValidationError

BENCHMARK_SCHEMA_VERSION = 1
BENCHMARK_SUITE_IDENTITY_SCHEMA = "commander-gym-benchmark-suite@v1"
BENCHMARK_SUITE_IDENTITY_SCHEMA_V2 = "commander-gym-benchmark-suite@v2"
BENCHMARK_SCENARIO_INPUT_IDENTITY_SCHEMA = "commander-gym-benchmark-scenario-input@v1"


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


def _validate_common(
    *,
    case_id: str,
    category: str,
    held_out: bool,
    tags: List[str],
    schema_version: int,
) -> None:
    if schema_version != BENCHMARK_SCHEMA_VERSION:
        raise RecordValidationError(
            f"unsupported benchmark schema_version {schema_version}; "
            f"expected {BENCHMARK_SCHEMA_VERSION}"
        )
    if not isinstance(case_id, str) or not case_id:
        raise RecordValidationError("case_id must be a non-empty string")
    if not isinstance(category, str) or not category:
        raise RecordValidationError("category must be a non-empty string")
    if held_out is not True:
        raise RecordValidationError("benchmark cases must be marked held_out=true")
    if not isinstance(tags, list) or not all(
        isinstance(tag, str) and tag for tag in tags
    ):
        raise RecordValidationError("tags must be an array of non-empty strings")


def _validate_judgment_actions(
    judgment: BenchmarkJudgment,
    legal_actions: Sequence[ActionRecord],
) -> None:
    legal = {action.action_id for action in legal_actions}
    judgment_ids = judgment.preferred_action_ids + judgment.ranked_action_ids
    for action_id in judgment_ids:
        if action_id not in legal:
            raise RecordValidationError(
                f"benchmark judgment references non-legal action {action_id!r}"
            )


@dataclass(frozen=True)
class BenchmarkCase:
    """One explicitly held-out recorded decision and its reference judgment."""

    case_id: str
    category: str
    decision: DecisionRecord
    judgment: BenchmarkJudgment
    held_out: bool = True
    tags: List[str] = field(default_factory=list)
    schema_version: int = BENCHMARK_SCHEMA_VERSION

    def validate(self) -> None:
        _validate_common(
            case_id=self.case_id,
            category=self.category,
            held_out=self.held_out,
            tags=self.tags,
            schema_version=self.schema_version,
        )
        self.decision.validate()
        _validate_judgment_actions(self.judgment, self.decision.legal_actions)

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


@dataclass(frozen=True)
class BenchmarkScenario:
    """Input-only held-out scenario with no observed native decision provenance."""

    case_id: str
    category: str
    decision_type: str
    seat: int
    observation_schema: str
    observation: Dict[str, Any]
    legal_actions: List[ActionRecord]
    judgment: BenchmarkJudgment
    held_out: bool = True
    tags: List[str] = field(default_factory=list)
    schema_version: int = BENCHMARK_SCHEMA_VERSION

    def validate(self) -> None:
        _validate_common(
            case_id=self.case_id,
            category=self.category,
            held_out=self.held_out,
            tags=self.tags,
            schema_version=self.schema_version,
        )
        if not isinstance(self.decision_type, str) or not self.decision_type:
            raise RecordValidationError("decision_type must be a non-empty string")
        if not isinstance(self.seat, int) or isinstance(self.seat, bool) or self.seat < 0:
            raise RecordValidationError("seat must be a non-negative integer")
        if not isinstance(self.observation_schema, str) or not self.observation_schema:
            raise RecordValidationError("observation_schema must be a non-empty string")
        if not isinstance(self.observation, dict):
            raise RecordValidationError("observation must be an object")
        if not isinstance(self.legal_actions, list) or not self.legal_actions:
            raise RecordValidationError("legal_actions must be a non-empty array")
        for action in self.legal_actions:
            if not isinstance(action, ActionRecord):
                raise RecordValidationError("legal_actions must contain ActionRecord values")
            action.validate()
        action_ids = [action.action_id for action in self.legal_actions]
        if len(action_ids) != len(set(action_ids)):
            raise RecordValidationError("legal_actions action_id values must be unique")
        _validate_judgment_actions(self.judgment, self.legal_actions)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BenchmarkScenario":
        if value.get("case_kind") != "input_scenario":
            raise RecordValidationError("input-only benchmark rows require case_kind=input_scenario")
        judgment_value = value.get("judgment")
        tags_value = value.get("tags", [])
        legal_value = value.get("legal_actions")
        if not isinstance(judgment_value, dict):
            raise RecordValidationError("judgment must be an object")
        if not isinstance(tags_value, list):
            raise RecordValidationError("tags must be an array")
        if not isinstance(legal_value, list):
            raise RecordValidationError("legal_actions must be an array")
        forbidden = {
            "decision",
            "game_id",
            "decision_id",
            "chosen_action_id",
            "pilot",
            "deck_id",
            "deck_version",
            "primer_version",
            "outcome",
            "metadata",
        }
        present = sorted(key for key in forbidden if key in value)
        if present:
            raise RecordValidationError(
                "input scenario must not carry observed decision/provenance fields: "
                + ", ".join(present)
            )
        scenario = cls(
            schema_version=value.get("schema_version", BENCHMARK_SCHEMA_VERSION),
            case_id=value.get("case_id"),
            category=value.get("category"),
            decision_type=value.get("decision_type"),
            seat=value.get("seat"),
            observation_schema=value.get("observation_schema"),
            observation=value.get("observation"),
            legal_actions=[ActionRecord.from_dict(item) for item in legal_value],
            judgment=BenchmarkJudgment.from_dict(judgment_value),
            held_out=value.get("held_out", True),
            tags=list(tags_value),
        )
        scenario.validate()
        return scenario


BenchmarkEntry = Union[BenchmarkCase, BenchmarkScenario]


def _canonical_fingerprint(payload: Mapping[str, Any]) -> str:
    try:
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RecordValidationError(
            f"benchmark artifact must be canonically JSON serializable: {exc}"
        ) from exc
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def benchmark_suite_identity(cases: Sequence[BenchmarkEntry]) -> Dict[str, Any]:
    """Return stable identity while preserving exact legacy v1 fingerprints."""

    seen: set[str] = set()
    entries = list(cases)
    for case in entries:
        case.validate()
        if case.case_id in seen:
            raise RecordValidationError(f"duplicate benchmark case_id {case.case_id!r}")
        seen.add(case.case_id)

    if all(isinstance(case, BenchmarkCase) for case in entries):
        serialized_cases = [asdict(case) for case in entries]
        payload = {
            "schema": BENCHMARK_SUITE_IDENTITY_SCHEMA,
            "cases": serialized_cases,
        }
        return {
            "schema": BENCHMARK_SUITE_IDENTITY_SCHEMA,
            "case_count": len(serialized_cases),
            "fingerprint": _canonical_fingerprint(payload),
        }

    serialized_cases: List[Dict[str, Any]] = []
    for case in entries:
        if isinstance(case, BenchmarkCase):
            serialized_cases.append({"case_kind": "recorded_decision", **asdict(case)})
        else:
            serialized_cases.append({"case_kind": "input_scenario", **asdict(case)})
    payload = {
        "schema": BENCHMARK_SUITE_IDENTITY_SCHEMA_V2,
        "cases": serialized_cases,
    }
    return {
        "schema": BENCHMARK_SUITE_IDENTITY_SCHEMA_V2,
        "case_count": len(serialized_cases),
        "fingerprint": _canonical_fingerprint(payload),
    }


def scenario_input_identity(
    *,
    decision_type: str,
    seat: int,
    observation_schema: str,
    observation: Mapping[str, Any],
    legal_actions: Sequence[ActionRecord],
) -> Dict[str, str]:
    """Fingerprint only the held-out policy input, independent of label/judgment."""

    payload = {
        "schema": BENCHMARK_SCENARIO_INPUT_IDENTITY_SCHEMA,
        "decision_type": decision_type,
        "seat": seat,
        "observation_schema": observation_schema,
        "observation": dict(observation),
        "legal_actions": [asdict(action) for action in legal_actions],
    }
    return {
        "schema": BENCHMARK_SCENARIO_INPUT_IDENTITY_SCHEMA,
        "fingerprint": _canonical_fingerprint(payload),
    }


def benchmark_entry_input_identity(case: BenchmarkEntry) -> Dict[str, str]:
    case.validate()
    if isinstance(case, BenchmarkCase):
        decision = case.decision
        return scenario_input_identity(
            decision_type=decision.decision_type,
            seat=decision.seat,
            observation_schema=decision.observation_schema,
            observation=decision.observation,
            legal_actions=decision.legal_actions,
        )
    return scenario_input_identity(
        decision_type=case.decision_type,
        seat=case.seat,
        observation_schema=case.observation_schema,
        observation=case.observation,
        legal_actions=case.legal_actions,
    )


def load_jsonl(path: Path | str) -> List[BenchmarkEntry]:
    """Load legacy recorded cases and explicit input-only scenarios."""

    cases: List[BenchmarkEntry] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            if not raw.strip():
                continue
            try:
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise RecordValidationError("benchmark row must be an object")
                case_kind = value.get("case_kind")
                if case_kind is None:
                    cases.append(BenchmarkCase.from_dict(value))
                elif case_kind == "input_scenario":
                    cases.append(BenchmarkScenario.from_dict(value))
                elif case_kind == "recorded_decision":
                    legacy_value = dict(value)
                    legacy_value.pop("case_kind", None)
                    cases.append(BenchmarkCase.from_dict(legacy_value))
                else:
                    raise RecordValidationError(
                        "case_kind must be recorded_decision or input_scenario"
                    )
            except (json.JSONDecodeError, RecordValidationError, TypeError, ValueError) as exc:
                raise RecordValidationError(f"{path}:{line_number}: {exc}") from exc
    return cases


def _legal_actions(case: BenchmarkEntry) -> Sequence[ActionRecord]:
    return case.decision.legal_actions if isinstance(case, BenchmarkCase) else case.legal_actions


def score_action(case: BenchmarkEntry, action_id: str) -> Dict[str, Any]:
    """Score one selected action without assuming a single objective ground truth."""

    case.validate()
    legal = {action.action_id for action in _legal_actions(case)}
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
