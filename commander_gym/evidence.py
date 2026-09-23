"""Immutable raw run evidence built on Commander Gym's portable storage substrate.

This module owns evidence semantics, not physical placement. It consumes the canonical
RunRecord / DecisionRecord / StructuredDecisionRecord contracts and stores one
content-addressed, chronological raw-evidence envelope through :mod:`commander_gym.storage`.
A small fail-closed catalog maps a stable run ID to that immutable artifact without
making filesystem paths part of durable identity.

The envelope deliberately separates information available at choice time (``input``),
the recorded choice (``target``), and post-choice / diagnostic material
(``provenance``). That boundary is suitable for later dataset construction without
silently leaking outcomes or routing diagnostics into model-facing inputs.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from .records import DecisionRecord, RecordValidationError, StructuredDecisionRecord
from .run_records import (
    RUN_STATUS_COMPLETED,
    RUN_STATUS_FAILED,
    RUN_STATUS_STOPPED,
    RunRecord,
)
from .storage import LocalArtifactStore, StorageLayout, StoredBlob

RAW_EVIDENCE_SCHEMA_VERSION = 1
RAW_EVIDENCE_CATALOG_SCHEMA_VERSION = 1
RAW_EVIDENCE_KIND = "commander-gym.raw-run-evidence"
IDENTITY_EPOCH_BINDING_V1 = "binding-v1"
IDENTITY_EPOCH_PRE_BINDING_V1 = "pre-binding-v1"

EvidenceRecord = DecisionRecord | StructuredDecisionRecord


class EvidenceError(RecordValidationError):
    """Raised when raw evidence cannot be recorded without ambiguity or leakage."""


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise EvidenceError(f"{label} must be a non-empty string")
    return value


def _record_kind(record: EvidenceRecord) -> str:
    if isinstance(record, DecisionRecord):
        return "action"
    if isinstance(record, StructuredDecisionRecord):
        return "structured_decision"
    raise EvidenceError(
        "raw evidence records must be DecisionRecord or StructuredDecisionRecord values"
    )


def _input_for_record(record: EvidenceRecord) -> dict[str, Any]:
    common = {
        "decision_type": record.decision_type,
        "seat": record.seat,
        "observation_schema": record.observation_schema,
        "observation": record.observation,
    }
    if isinstance(record, DecisionRecord):
        return {
            **common,
            "legal_actions": [asdict(action) for action in record.legal_actions],
        }
    if isinstance(record, StructuredDecisionRecord):
        return {
            **common,
            "native_decision_semantic_id": record.native_decision_semantic_id,
        }
    raise EvidenceError("unsupported raw evidence record type")


def _target_for_record(record: EvidenceRecord) -> dict[str, Any]:
    if isinstance(record, DecisionRecord):
        return {"chosen_action_id": record.chosen_action_id}
    if isinstance(record, StructuredDecisionRecord):
        return {"response": record.response}
    raise EvidenceError("unsupported raw evidence record type")


def _decision_routing(record: EvidenceRecord) -> dict[str, Any]:
    pilot_metadata = record.metadata.get("pilot_metadata")
    if not isinstance(pilot_metadata, Mapping):
        raise EvidenceError(
            f"decision {record.decision_id!r} must record pilot_metadata for raw evidence"
        )
    routing = pilot_metadata.get("routing")
    if not isinstance(routing, Mapping):
        raise EvidenceError(
            f"decision {record.decision_id!r} must record the actual routing subsystem"
        )
    _require_string(routing.get("path"), f"decision {record.decision_id!r} routing.path")
    return dict(routing)


def _decision_state_digests(record: EvidenceRecord) -> tuple[str, str]:
    input_digest = _require_string(
        record.metadata.get("input_state_digest"),
        f"decision {record.decision_id!r} input_state_digest",
    )
    result_digest = _require_string(
        record.metadata.get("result_state_digest"),
        f"decision {record.decision_id!r} result_state_digest",
    )
    return input_digest, result_digest


def _provenance_for_record(record: EvidenceRecord) -> dict[str, Any]:
    input_digest, result_digest = _decision_state_digests(record)
    routing = _decision_routing(record)
    provenance = {
        "game_id": record.game_id,
        "decision_id": record.decision_id,
        "record_schema_version": record.schema_version,
        "pilot": asdict(record.pilot),
        "deck_id": record.deck_id,
        "deck_version": record.deck_version,
        "primer_version": record.primer_version,
        "routing": routing,
        "input_state_digest": input_digest,
        "result_state_digest": result_digest,
        "outcome": record.outcome,
        "metadata": record.metadata,
    }
    if record.binding is not None:
        provenance["binding"] = record.binding.to_dict()
    return provenance


def _qualification_for_run(run: RunRecord) -> dict[str, Any]:
    status = run.termination.status
    if status == RUN_STATUS_COMPLETED:
        classification = "completed"
        diagnostic_only = False
    elif status == RUN_STATUS_STOPPED:
        classification = "partial"
        diagnostic_only = True
    elif status == RUN_STATUS_FAILED:
        classification = "failed"
        diagnostic_only = True
    else:  # RunRecord.validate() should already make this unreachable.
        raise EvidenceError(f"unsupported run termination status: {status!r}")
    return {
        "classification": classification,
        "diagnostic_only": diagnostic_only,
        "reason": run.termination.reason,
        "failure_domain": run.termination.failure_domain,
    }


def _validate_run_decision_join(
    run: RunRecord,
    records: list[EvidenceRecord],
) -> str:
    if run.decision_ids != [record.decision_id for record in records]:
        raise EvidenceError(
            "run.decision_ids must exactly match raw evidence decisions in chronological order"
        )
    if len(run.decision_ids) != len(set(run.decision_ids)):
        raise EvidenceError("raw evidence decision_ids must be unique")

    participants = {participant.seat: participant for participant in run.participants}
    binding_presence: list[bool] = []
    for record in records:
        _record_kind(record)
        record.validate()
        if record.game_id != run.game_id:
            raise EvidenceError(
                f"decision {record.decision_id!r} belongs to game {record.game_id!r}, "
                f"not run game {run.game_id!r}"
            )
        participant = participants.get(record.seat)
        if participant is None:
            raise EvidenceError(
                f"decision {record.decision_id!r} has no matching run participant "
                f"for seat {record.seat}"
            )
        participant_binding = participant.binding
        if (record.binding is None) != (participant_binding is None):
            raise EvidenceError(
                f"decision {record.decision_id!r} Binding presence does not match "
                f"run participant seat {record.seat}"
            )
        if record.binding is not None and record.binding != participant_binding:
            raise EvidenceError(
                f"decision {record.decision_id!r} Binding does not match "
                f"run participant seat {record.seat}"
            )
        binding_presence.append(record.binding is not None)
        _decision_state_digests(record)
        _decision_routing(record)

    if binding_presence and any(binding_presence) and not all(binding_presence):
        raise EvidenceError(
            "raw evidence cannot mix canonical Binding-aware and pre-Binding decisions"
        )
    return (
        IDENTITY_EPOCH_BINDING_V1
        if binding_presence and all(binding_presence)
        else IDENTITY_EPOCH_PRE_BINDING_V1
    )


def build_raw_evidence_envelope(
    run: RunRecord,
    records: Iterable[EvidenceRecord],
    *,
    commander_gym_revision: str,
) -> dict[str, Any]:
    """Build one immutable chronological raw-evidence envelope.

    New foundation evidence requires exact engine schema/revision provenance and an
    explicit Commander Gym revision. Legacy/pre-Binding records remain distinguishable
    through ``identity_epoch`` rather than being silently reinterpreted as canonical
    Binding evidence.
    """

    if not isinstance(run, RunRecord):
        raise EvidenceError("run must be a RunRecord")
    run.validate()
    _require_string(commander_gym_revision, "commander_gym_revision")
    _require_string(run.engine.schema, "run.engine.schema")
    _require_string(run.engine.revision, "run.engine.revision")

    materialized = list(records)
    identity_epoch = _validate_run_decision_join(run, materialized)
    decisions: list[dict[str, Any]] = []
    for sequence_index, record in enumerate(materialized):
        decisions.append(
            {
                "sequence_index": sequence_index,
                "record_kind": _record_kind(record),
                "input": _input_for_record(record),
                "target": _target_for_record(record),
                "provenance": _provenance_for_record(record),
            }
        )

    envelope = {
        "evidence_schema_version": RAW_EVIDENCE_SCHEMA_VERSION,
        "kind": RAW_EVIDENCE_KIND,
        "producer": {
            "implementation": "commander-gym",
            "revision": commander_gym_revision,
        },
        "source_schemas": {
            "run_record": run.schema_version,
            "decision_records": sorted({record.schema_version for record in materialized}),
        },
        "identity_epoch": identity_epoch,
        "qualification": _qualification_for_run(run),
        "run": run.to_dict(),
        "decisions": decisions,
    }
    validate_raw_evidence_envelope(envelope)
    return envelope


def validate_raw_evidence_envelope(value: Mapping[str, Any]) -> None:
    """Validate the immutable envelope boundary without reconstructing game state."""

    if not isinstance(value, Mapping):
        raise EvidenceError("raw evidence envelope must be an object")
    if value.get("evidence_schema_version") != RAW_EVIDENCE_SCHEMA_VERSION:
        raise EvidenceError(
            "unsupported raw evidence schema_version "
            f"{value.get('evidence_schema_version')!r}; expected {RAW_EVIDENCE_SCHEMA_VERSION}"
        )
    if value.get("kind") != RAW_EVIDENCE_KIND:
        raise EvidenceError(f"raw evidence kind must be {RAW_EVIDENCE_KIND!r}")
    producer = value.get("producer")
    if not isinstance(producer, Mapping):
        raise EvidenceError("raw evidence producer must be an object")
    if producer.get("implementation") != "commander-gym":
        raise EvidenceError("raw evidence producer.implementation must be commander-gym")
    _require_string(producer.get("revision"), "raw evidence producer.revision")

    run_value = value.get("run")
    if not isinstance(run_value, Mapping):
        raise EvidenceError("raw evidence run must be an object")
    run = RunRecord.from_dict(run_value)

    identity_epoch = value.get("identity_epoch")
    if identity_epoch not in {IDENTITY_EPOCH_BINDING_V1, IDENTITY_EPOCH_PRE_BINDING_V1}:
        raise EvidenceError("raw evidence identity_epoch is unsupported")

    qualification = value.get("qualification")
    if not isinstance(qualification, Mapping):
        raise EvidenceError("raw evidence qualification must be an object")
    expected_qualification = _qualification_for_run(run)
    if dict(qualification) != expected_qualification:
        raise EvidenceError("raw evidence qualification does not match run termination")

    decisions = value.get("decisions")
    if not isinstance(decisions, list):
        raise EvidenceError("raw evidence decisions must be an array")
    decision_ids: list[str] = []
    for index, decision in enumerate(decisions):
        if not isinstance(decision, Mapping):
            raise EvidenceError("each raw evidence decision must be an object")
        if decision.get("sequence_index") != index:
            raise EvidenceError("raw evidence decision sequence_index must be contiguous")
        if decision.get("record_kind") not in {"action", "structured_decision"}:
            raise EvidenceError("raw evidence decision record_kind is unsupported")
        input_value = decision.get("input")
        target = decision.get("target")
        provenance = decision.get("provenance")
        if not isinstance(input_value, Mapping) or not isinstance(target, Mapping):
            raise EvidenceError("raw evidence decision input/target must be objects")
        if not isinstance(provenance, Mapping):
            raise EvidenceError("raw evidence decision provenance must be an object")
        decision_id = _require_string(
            provenance.get("decision_id"), "raw evidence provenance.decision_id"
        )
        decision_ids.append(decision_id)
        if identity_epoch == IDENTITY_EPOCH_BINDING_V1:
            binding = provenance.get("binding")
            if not isinstance(binding, Mapping):
                raise EvidenceError(
                    "binding-v1 raw evidence decisions must preserve canonical Binding identity"
                )
        elif "binding" in provenance:
            raise EvidenceError(
                "pre-binding raw evidence must not be relabeled with canonical Binding identity"
            )
        routing = provenance.get("routing")
        if not isinstance(routing, Mapping):
            raise EvidenceError("raw evidence provenance.routing must be an object")
        _require_string(routing.get("path"), "raw evidence provenance.routing.path")
        _require_string(
            provenance.get("input_state_digest"),
            "raw evidence provenance.input_state_digest",
        )
        _require_string(
            provenance.get("result_state_digest"),
            "raw evidence provenance.result_state_digest",
        )

    if decision_ids != run.decision_ids:
        raise EvidenceError(
            "raw evidence decision chronology does not match run.decision_ids"
        )


@dataclass(frozen=True)
class EvidenceAccounting:
    """Per-run physical/evidence accounting emitted alongside the immutable blob."""

    raw_bytes: int
    stored_bytes: int
    compressed_bytes: int | None
    observation_bytes: int
    evidence_input_bytes: int
    evidence_target_bytes: int
    model_input_bytes: int | None
    model_output_bytes: int | None
    logs_debug_bytes: int | None
    decision_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RawEvidenceWriteResult:
    artifact: StoredBlob
    catalog_path: Path
    accounting: EvidenceAccounting
    identity_epoch: str
    qualification: Mapping[str, Any]


class RawEvidenceStore:
    """Write/read immutable raw evidence through the #74 storage substrate."""

    def __init__(self, layout: StorageLayout):
        if not isinstance(layout, StorageLayout):
            raise EvidenceError("layout must be a StorageLayout")
        self.layout = layout
        self.artifacts = LocalArtifactStore(layout)

    def _catalog_path(self, run_id: str) -> Path:
        _require_string(run_id, "run_id")
        digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
        return self.layout.path("catalog") / "raw-run-evidence" / digest[:2] / f"{digest}.json"

    def _catalog_payload(
        self,
        *,
        run_id: str,
        artifact: StoredBlob,
        envelope: Mapping[str, Any],
        accounting: EvidenceAccounting,
    ) -> dict[str, Any]:
        return {
            "catalog_schema_version": RAW_EVIDENCE_CATALOG_SCHEMA_VERSION,
            "run_id": run_id,
            "artifact_id": artifact.artifact_id,
            "artifact_size_bytes": artifact.size_bytes,
            "evidence_schema_version": RAW_EVIDENCE_SCHEMA_VERSION,
            "identity_epoch": envelope["identity_epoch"],
            "qualification": envelope["qualification"],
            "producer": envelope["producer"],
            "accounting": accounting.to_dict(),
        }

    def _write_catalog_entry(self, path: Path, payload: Mapping[str, Any]) -> None:
        data = _canonical_json_bytes(payload)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != data:
                raise EvidenceError(
                    "run_id is already cataloged to different immutable raw evidence"
                )
            return

        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except FileExistsError:
            if not path.exists() or path.read_bytes() != data:
                raise EvidenceError("concurrent raw evidence catalog write was inconsistent")
        finally:
            if temporary.exists():
                temporary.unlink()

    def write(
        self,
        run: RunRecord,
        records: Iterable[EvidenceRecord],
        *,
        commander_gym_revision: str,
    ) -> RawEvidenceWriteResult:
        envelope = build_raw_evidence_envelope(
            run,
            records,
            commander_gym_revision=commander_gym_revision,
        )
        payload = _canonical_json_bytes(envelope)
        artifact = self.artifacts.put_bytes(payload)
        decisions = envelope["decisions"]
        accounting = EvidenceAccounting(
            raw_bytes=len(payload),
            stored_bytes=artifact.size_bytes,
            compressed_bytes=None,
            observation_bytes=sum(
                len(_canonical_json_bytes(decision["input"]["observation"]))
                for decision in decisions
            ),
            evidence_input_bytes=sum(
                len(_canonical_json_bytes(decision["input"])) for decision in decisions
            ),
            evidence_target_bytes=sum(
                len(_canonical_json_bytes(decision["target"])) for decision in decisions
            ),
            model_input_bytes=None,
            model_output_bytes=None,
            logs_debug_bytes=None,
            decision_count=len(decisions),
        )
        catalog_path = self._catalog_path(run.run_id)
        self._write_catalog_entry(
            catalog_path,
            self._catalog_payload(
                run_id=run.run_id,
                artifact=artifact,
                envelope=envelope,
                accounting=accounting,
            ),
        )
        return RawEvidenceWriteResult(
            artifact=artifact,
            catalog_path=catalog_path,
            accounting=accounting,
            identity_epoch=envelope["identity_epoch"],
            qualification=envelope["qualification"],
        )

    def read(self, run_id: str) -> dict[str, Any]:
        catalog_path = self._catalog_path(run_id)
        try:
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise EvidenceError(f"raw evidence run not found: {run_id}") from exc
        except json.JSONDecodeError as exc:
            raise EvidenceError(f"raw evidence catalog is not valid JSON: {run_id}") from exc
        if not isinstance(catalog, Mapping):
            raise EvidenceError("raw evidence catalog entry must be an object")
        if catalog.get("catalog_schema_version") != RAW_EVIDENCE_CATALOG_SCHEMA_VERSION:
            raise EvidenceError("unsupported raw evidence catalog schema version")
        if catalog.get("run_id") != run_id:
            raise EvidenceError("raw evidence catalog run_id mismatch")
        artifact_id = _require_string(catalog.get("artifact_id"), "catalog artifact_id")
        payload = self.artifacts.read_bytes(artifact_id)
        try:
            envelope = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EvidenceError("raw evidence artifact is not canonical UTF-8 JSON") from exc
        if not isinstance(envelope, Mapping):
            raise EvidenceError("raw evidence artifact must decode to an object")
        validate_raw_evidence_envelope(envelope)
        run_value = envelope.get("run")
        if not isinstance(run_value, Mapping) or run_value.get("run_id") != run_id:
            raise EvidenceError("raw evidence artifact run_id does not match catalog")
        if artifact_id != f"sha256:{hashlib.sha256(payload).hexdigest()}":
            raise EvidenceError("raw evidence artifact identity mismatch")
        return dict(envelope)
