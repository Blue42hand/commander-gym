"""Versioned, transport-independent training and evaluation records.

These records are owned by Commander Gym because they define durable research evidence,
not engine state. They intentionally depend only on Python's standard library.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from .identity import IdentityError, IdentityRef

SCHEMA_VERSION = 1


class RecordValidationError(ValueError):
    """Raised when a training/evaluation record is structurally invalid."""


def _binding_ref_from_value(value: Any) -> Optional[IdentityRef]:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise RecordValidationError("binding must be an object or null")
    try:
        binding = IdentityRef.from_dict(value)
    except IdentityError as exc:
        raise RecordValidationError(f"invalid binding identity: {exc}") from exc
    if binding.artifact_type != "binding":
        raise RecordValidationError("binding must reference artifact_type='binding'")
    return binding


def _validate_binding_ref(binding: Optional[IdentityRef]) -> None:
    if binding is None:
        return
    if not isinstance(binding, IdentityRef):
        raise RecordValidationError("binding must be IdentityRef or null")
    try:
        binding.validate()
    except IdentityError as exc:
        raise RecordValidationError(f"invalid binding identity: {exc}") from exc
    if binding.artifact_type != "binding":
        raise RecordValidationError("binding must reference artifact_type='binding'")


@dataclass(frozen=True)
class ActionRecord:
    action_id: str
    payload: Dict[str, Any] = field(default_factory=dict)
    label: Optional[str] = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ActionRecord":
        action_id = value.get("action_id")
        if not isinstance(action_id, str) or not action_id:
            raise RecordValidationError("action.action_id must be a non-empty string")
        payload = value.get("payload", {})
        if not isinstance(payload, dict):
            raise RecordValidationError("action.payload must be an object")
        label = value.get("label")
        if label is not None and not isinstance(label, str):
            raise RecordValidationError("action.label must be a string or null")
        return cls(action_id=action_id, payload=dict(payload), label=label)


@dataclass(frozen=True)
class PilotProvenance:
    source: str
    implementation: str
    version: Optional[str] = None
    model: Optional[str] = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PilotProvenance":
        source = value.get("source")
        implementation = value.get("implementation")
        if not isinstance(source, str) or not source:
            raise RecordValidationError("pilot.source must be a non-empty string")
        if not isinstance(implementation, str) or not implementation:
            raise RecordValidationError("pilot.implementation must be a non-empty string")
        version = value.get("version")
        model = value.get("model")
        if version is not None and not isinstance(version, str):
            raise RecordValidationError("pilot.version must be a string or null")
        if model is not None and not isinstance(model, str):
            raise RecordValidationError("pilot.model must be a string or null")
        return cls(source=source, implementation=implementation, version=version, model=model)


@dataclass(frozen=True)
class DecisionRecord:
    game_id: str
    decision_id: str
    decision_type: str
    seat: int
    observation_schema: str
    observation: Dict[str, Any]
    legal_actions: List[ActionRecord]
    chosen_action_id: str
    pilot: PilotProvenance
    deck_id: str
    deck_version: str
    primer_version: Optional[str] = None
    binding: Optional[IdentityRef] = None
    outcome: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise RecordValidationError(
                f"unsupported schema_version {self.schema_version}; expected {SCHEMA_VERSION}"
            )
        for name, value in (
            ("game_id", self.game_id),
            ("decision_id", self.decision_id),
            ("decision_type", self.decision_type),
            ("observation_schema", self.observation_schema),
            ("chosen_action_id", self.chosen_action_id),
            ("deck_id", self.deck_id),
            ("deck_version", self.deck_version),
        ):
            if not isinstance(value, str) or not value:
                raise RecordValidationError(f"{name} must be a non-empty string")
        if not isinstance(self.seat, int) or self.seat < 0:
            raise RecordValidationError("seat must be a non-negative integer")
        if not isinstance(self.observation, dict):
            raise RecordValidationError("observation must be an object")
        if not self.legal_actions:
            raise RecordValidationError("legal_actions must not be empty")
        action_ids = [action.action_id for action in self.legal_actions]
        if len(action_ids) != len(set(action_ids)):
            raise RecordValidationError("legal action ids must be unique")
        if self.chosen_action_id not in set(action_ids):
            raise RecordValidationError("chosen_action_id must reference a legal action")
        _validate_binding_ref(self.binding)
        if self.outcome is not None and not isinstance(self.outcome, dict):
            raise RecordValidationError("outcome must be an object or null")
        if not isinstance(self.metadata, dict):
            raise RecordValidationError("metadata must be an object")

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        result = asdict(self)
        if self.binding is None:
            result.pop("binding", None)
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DecisionRecord":
        schema_version = value.get("schema_version", SCHEMA_VERSION)
        if schema_version != SCHEMA_VERSION:
            raise RecordValidationError(
                f"unsupported schema_version {schema_version}; expected {SCHEMA_VERSION}"
            )
        actions_value = value.get("legal_actions")
        if not isinstance(actions_value, list):
            raise RecordValidationError("legal_actions must be an array")
        pilot_value = value.get("pilot")
        if not isinstance(pilot_value, dict):
            raise RecordValidationError("pilot must be an object")
        record = cls(
            schema_version=schema_version,
            game_id=value.get("game_id"),
            decision_id=value.get("decision_id"),
            decision_type=value.get("decision_type"),
            seat=value.get("seat"),
            observation_schema=value.get("observation_schema"),
            observation=value.get("observation", {}),
            legal_actions=[ActionRecord.from_dict(item) for item in actions_value],
            chosen_action_id=value.get("chosen_action_id"),
            pilot=PilotProvenance.from_dict(pilot_value),
            deck_id=value.get("deck_id"),
            deck_version=value.get("deck_version"),
            primer_version=value.get("primer_version"),
            binding=_binding_ref_from_value(value.get("binding")),
            outcome=value.get("outcome"),
            metadata=value.get("metadata", {}),
        )
        record.validate()
        return record


@dataclass(frozen=True)
class StructuredDecisionRecord:
    """Durable evidence for one native non-enumerable Argentum decision response."""

    game_id: str
    decision_id: str
    decision_type: str
    seat: int
    observation_schema: str
    observation: Dict[str, Any]
    native_decision_semantic_id: str
    response: Dict[str, Any]
    pilot: PilotProvenance
    deck_id: str
    deck_version: str
    primer_version: Optional[str] = None
    binding: Optional[IdentityRef] = None
    outcome: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise RecordValidationError(
                f"unsupported schema_version {self.schema_version}; expected {SCHEMA_VERSION}"
            )
        for name, value in (
            ("game_id", self.game_id),
            ("decision_id", self.decision_id),
            ("decision_type", self.decision_type),
            ("observation_schema", self.observation_schema),
            ("native_decision_semantic_id", self.native_decision_semantic_id),
            ("deck_id", self.deck_id),
            ("deck_version", self.deck_version),
        ):
            if not isinstance(value, str) or not value:
                raise RecordValidationError(f"{name} must be a non-empty string")
        if not isinstance(self.seat, int) or self.seat < 0:
            raise RecordValidationError("seat must be a non-negative integer")
        if not isinstance(self.observation, dict):
            raise RecordValidationError("observation must be an object")
        if not isinstance(self.response, dict) or not self.response:
            raise RecordValidationError("response must be a non-empty object")
        if "decisionId" in self.response:
            raise RecordValidationError(
                "durable structured response must not contain live decisionId routing"
            )
        _validate_binding_ref(self.binding)
        if self.outcome is not None and not isinstance(self.outcome, dict):
            raise RecordValidationError("outcome must be an object or null")
        if not isinstance(self.metadata, dict):
            raise RecordValidationError("metadata must be an object")

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        result = asdict(self)
        if self.binding is None:
            result.pop("binding", None)
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "StructuredDecisionRecord":
        schema_version = value.get("schema_version", SCHEMA_VERSION)
        if schema_version != SCHEMA_VERSION:
            raise RecordValidationError(
                f"unsupported schema_version {schema_version}; expected {SCHEMA_VERSION}"
            )
        pilot_value = value.get("pilot")
        if not isinstance(pilot_value, dict):
            raise RecordValidationError("pilot must be an object")
        record = cls(
            schema_version=schema_version,
            game_id=value.get("game_id"),
            decision_id=value.get("decision_id"),
            decision_type=value.get("decision_type"),
            seat=value.get("seat"),
            observation_schema=value.get("observation_schema"),
            observation=value.get("observation", {}),
            native_decision_semantic_id=value.get("native_decision_semantic_id"),
            response=value.get("response", {}),
            pilot=PilotProvenance.from_dict(pilot_value),
            deck_id=value.get("deck_id"),
            deck_version=value.get("deck_version"),
            primer_version=value.get("primer_version"),
            binding=_binding_ref_from_value(value.get("binding")),
            outcome=value.get("outcome"),
            metadata=value.get("metadata", {}),
        )
        record.validate()
        return record
