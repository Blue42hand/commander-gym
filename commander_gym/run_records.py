"""Versioned, engine-independent experiment/run provenance.

This module keeps the durable research record separate from any transport, process,
or filesystem lifecycle. It records enough provenance to compare or reproduce a
run while leaving authoritative game state and rules provenance to the engine.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Union

from .identity import IdentityError, IdentityRef
from .records import PilotProvenance, RecordValidationError

RUN_RECORD_SCHEMA_VERSION = 1
RUN_STATUS_COMPLETED = "completed"
RUN_STATUS_STOPPED = "stopped"
RUN_STATUS_FAILED = "failed"
RUN_TERMINAL_STATUSES = frozenset(
    {RUN_STATUS_COMPLETED, RUN_STATUS_STOPPED, RUN_STATUS_FAILED}
)

Seed = Union[int, str]


def _require_optional_string(name: str, value: Optional[str]) -> None:
    if value is not None and (not isinstance(value, str) or not value):
        raise RecordValidationError(f"{name} must be a non-empty string or null")


def _parse_aware_timestamp(name: str, value: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise RecordValidationError(f"{name} must be a non-empty RFC 3339 timestamp")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise RecordValidationError(f"{name} must be a valid RFC 3339 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RecordValidationError(f"{name} must include a timezone offset")
    return parsed


def _binding_ref_from_value(value: Any) -> Optional[IdentityRef]:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise RecordValidationError("participant.binding must be an object or null")
    try:
        binding = IdentityRef.from_dict(value)
    except IdentityError as exc:
        raise RecordValidationError(f"invalid participant.binding identity: {exc}") from exc
    if binding.artifact_type != "binding":
        raise RecordValidationError(
            "participant.binding must reference artifact_type='binding'"
        )
    return binding


def _validate_binding_ref(binding: Optional[IdentityRef]) -> None:
    if binding is None:
        return
    if not isinstance(binding, IdentityRef):
        raise RecordValidationError("participant.binding must be IdentityRef or null")
    try:
        binding.validate()
    except IdentityError as exc:
        raise RecordValidationError(f"invalid participant.binding identity: {exc}") from exc
    if binding.artifact_type != "binding":
        raise RecordValidationError(
            "participant.binding must reference artifact_type='binding'"
        )


@dataclass(frozen=True)
class EngineProvenance:
    """Identity of the authoritative game/environment used for a run."""

    implementation: str
    version: str
    schema: Optional[str] = None
    revision: Optional[str] = None

    def validate(self) -> None:
        for name, value in (
            ("engine.implementation", self.implementation),
            ("engine.version", self.version),
        ):
            if not isinstance(value, str) or not value:
                raise RecordValidationError(f"{name} must be a non-empty string")
        _require_optional_string("engine.schema", self.schema)
        _require_optional_string("engine.revision", self.revision)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EngineProvenance":
        if not isinstance(value, Mapping):
            raise RecordValidationError("engine must be an object")
        engine = cls(
            implementation=value.get("implementation"),
            version=value.get("version"),
            schema=value.get("schema"),
            revision=value.get("revision"),
        )
        engine.validate()
        return engine


@dataclass(frozen=True)
class RunParticipant:
    """Player/deck identity used at one seat during a run."""

    seat: int
    pilot: PilotProvenance
    deck_id: Optional[str] = None
    deck_version: Optional[str] = None
    primer_version: Optional[str] = None
    binding: Optional[IdentityRef] = None

    def validate(self) -> None:
        if not isinstance(self.seat, int) or isinstance(self.seat, bool) or self.seat < 0:
            raise RecordValidationError("participant.seat must be a non-negative integer")
        if not isinstance(self.pilot, PilotProvenance):
            raise RecordValidationError("participant.pilot must be PilotProvenance")
        PilotProvenance.from_dict(asdict(self.pilot))
        _require_optional_string("participant.deck_id", self.deck_id)
        _require_optional_string("participant.deck_version", self.deck_version)
        _require_optional_string("participant.primer_version", self.primer_version)
        if (self.deck_id is None) != (self.deck_version is None):
            raise RecordValidationError(
                "participant.deck_id and participant.deck_version must be supplied together"
            )
        _validate_binding_ref(self.binding)

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        result = asdict(self)
        if self.binding is None:
            result.pop("binding", None)
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunParticipant":
        if not isinstance(value, Mapping):
            raise RecordValidationError("participant must be an object")
        pilot = value.get("pilot")
        if not isinstance(pilot, Mapping):
            raise RecordValidationError("participant.pilot must be an object")
        participant = cls(
            seat=value.get("seat"),
            pilot=PilotProvenance.from_dict(pilot),
            deck_id=value.get("deck_id"),
            deck_version=value.get("deck_version"),
            primer_version=value.get("primer_version"),
            binding=_binding_ref_from_value(value.get("binding")),
        )
        participant.validate()
        return participant


@dataclass(frozen=True)
class RunTermination:
    """Stable terminal classification independent of engine-specific error text."""

    status: str
    reason: Optional[str] = None
    failure_domain: Optional[str] = None

    def validate(self) -> None:
        if not isinstance(self.status, str) or self.status not in RUN_TERMINAL_STATUSES:
            raise RecordValidationError(
                f"termination.status must be one of {sorted(RUN_TERMINAL_STATUSES)}"
            )
        _require_optional_string("termination.reason", self.reason)
        _require_optional_string("termination.failure_domain", self.failure_domain)
        if self.status == RUN_STATUS_COMPLETED and self.failure_domain is not None:
            raise RecordValidationError("completed runs must not carry a failure_domain")
        if self.status in {RUN_STATUS_STOPPED, RUN_STATUS_FAILED} and self.reason is None:
            raise RecordValidationError(
                f"{self.status} runs must include a terminal reason"
            )
        if self.status == RUN_STATUS_FAILED and self.failure_domain is None:
            raise RecordValidationError("failed runs must include a failure_domain")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunTermination":
        if not isinstance(value, Mapping):
            raise RecordValidationError("termination must be an object")
        termination = cls(
            status=value.get("status"),
            reason=value.get("reason"),
            failure_domain=value.get("failure_domain"),
        )
        termination.validate()
        return termination


@dataclass(frozen=True)
class RunRecord:
    """Durable provenance for one evaluation/training execution unit.

    A run may fail before a seat or game exists, so participants and game_id are
    intentionally optional. When decision_ids are recorded, game_id is required
    so they can be joined unambiguously to :class:`DecisionRecord` evidence.
    """

    run_id: str
    started_at: str
    finished_at: str
    engine: EngineProvenance
    termination: RunTermination
    participants: List[RunParticipant] = field(default_factory=list)
    experiment_id: Optional[str] = None
    benchmark_id: Optional[str] = None
    game_id: Optional[str] = None
    seed: Optional[Seed] = None
    decision_ids: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    schema_version: int = RUN_RECORD_SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != RUN_RECORD_SCHEMA_VERSION:
            raise RecordValidationError(
                f"unsupported run schema_version {self.schema_version}; "
                f"expected {RUN_RECORD_SCHEMA_VERSION}"
            )
        if not isinstance(self.run_id, str) or not self.run_id:
            raise RecordValidationError("run_id must be a non-empty string")
        started = _parse_aware_timestamp("started_at", self.started_at)
        finished = _parse_aware_timestamp("finished_at", self.finished_at)
        if finished < started:
            raise RecordValidationError("finished_at must not precede started_at")
        if not isinstance(self.engine, EngineProvenance):
            raise RecordValidationError("engine must be EngineProvenance")
        self.engine.validate()
        if not isinstance(self.termination, RunTermination):
            raise RecordValidationError("termination must be RunTermination")
        self.termination.validate()
        if not isinstance(self.participants, list):
            raise RecordValidationError("participants must be an array")
        seats = []
        for participant in self.participants:
            if not isinstance(participant, RunParticipant):
                raise RecordValidationError("participants must contain RunParticipant values")
            participant.validate()
            seats.append(participant.seat)
        if len(seats) != len(set(seats)):
            raise RecordValidationError("participant seats must be unique")
        for name, value in (
            ("experiment_id", self.experiment_id),
            ("benchmark_id", self.benchmark_id),
            ("game_id", self.game_id),
        ):
            _require_optional_string(name, value)
        if self.seed is not None:
            if isinstance(self.seed, bool) or not isinstance(self.seed, (int, str)):
                raise RecordValidationError("seed must be an integer, string, or null")
            if isinstance(self.seed, str) and not self.seed:
                raise RecordValidationError("seed must not be an empty string")
        if not isinstance(self.decision_ids, list):
            raise RecordValidationError("decision_ids must be an array")
        if any(not isinstance(item, str) or not item for item in self.decision_ids):
            raise RecordValidationError("decision_ids must contain non-empty strings")
        if len(self.decision_ids) != len(set(self.decision_ids)):
            raise RecordValidationError("decision_ids must be unique")
        if self.decision_ids and self.game_id is None:
            raise RecordValidationError("game_id is required when decision_ids are recorded")
        if not isinstance(self.metrics, dict):
            raise RecordValidationError("metrics must be an object")
        if not isinstance(self.metadata, dict):
            raise RecordValidationError("metadata must be an object")

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        result = asdict(self)
        result["participants"] = [participant.to_dict() for participant in self.participants]
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunRecord":
        if not isinstance(value, Mapping):
            raise RecordValidationError("run record must be an object")
        schema_version = value.get("schema_version", RUN_RECORD_SCHEMA_VERSION)
        if schema_version != RUN_RECORD_SCHEMA_VERSION:
            raise RecordValidationError(
                f"unsupported run schema_version {schema_version}; "
                f"expected {RUN_RECORD_SCHEMA_VERSION}"
            )
        engine = value.get("engine")
        termination = value.get("termination")
        participants = value.get("participants", [])
        decision_ids = value.get("decision_ids", [])
        if not isinstance(engine, Mapping):
            raise RecordValidationError("engine must be an object")
        if not isinstance(termination, Mapping):
            raise RecordValidationError("termination must be an object")
        if not isinstance(participants, list):
            raise RecordValidationError("participants must be an array")
        if not isinstance(decision_ids, list):
            raise RecordValidationError("decision_ids must be an array")
        record = cls(
            schema_version=schema_version,
            run_id=value.get("run_id"),
            started_at=value.get("started_at"),
            finished_at=value.get("finished_at"),
            engine=EngineProvenance.from_dict(engine),
            termination=RunTermination.from_dict(termination),
            participants=[RunParticipant.from_dict(item) for item in participants],
            experiment_id=value.get("experiment_id"),
            benchmark_id=value.get("benchmark_id"),
            game_id=value.get("game_id"),
            seed=value.get("seed"),
            decision_ids=list(decision_ids),
            metrics=value.get("metrics", {}),
            metadata=value.get("metadata", {}),
        )
        record.validate()
        return record
