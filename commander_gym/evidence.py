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
MODEL_IO_SCHEMA_VERSION = 1
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


def _pilot_metadata(record: EvidenceRecord) -> Mapping[str, Any]:
    pilot_metadata = record.metadata.get("pilot_metadata")
    if not isinstance(pilot_metadata, Mapping):
        raise EvidenceError(
            f"decision {record.decision_id!r} must record pilot_metadata for raw evidence"
        )
    return pilot_metadata


def _model_io(record: EvidenceRecord) -> dict[str, Any] | None:
    """Validate the optional provider-attempt trace carried by pilot metadata.

    This is an additive nested schema inside raw-evidence v1. Legacy evidence therefore
    remains readable without pretending it contained model I/O that was never captured.
    """

    value = _pilot_metadata(record).get("modelIo")
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise EvidenceError(f"decision {record.decision_id!r} modelIo must be an object")
    if value.get("schemaVersion") != MODEL_IO_SCHEMA_VERSION:
        raise EvidenceError(
            f"decision {record.decision_id!r} modelIo schemaVersion is unsupported"
        )
    provider = _require_string(
        value.get("provider"), f"decision {record.decision_id!r} modelIo.provider"
    )
    selected_attempt = value.get("selectedAttempt")
    if type(selected_attempt) is not int or selected_attempt < 0:
        raise EvidenceError(
            f"decision {record.decision_id!r} modelIo.selectedAttempt must be non-negative"
        )
    attempts = value.get("attempts")
    if not isinstance(attempts, list) or not attempts:
        raise EvidenceError(
            f"decision {record.decision_id!r} modelIo.attempts must be non-empty"
        )

    normalized_attempts: list[dict[str, Any]] = []
    for index, attempt in enumerate(attempts):
        if not isinstance(attempt, Mapping):
            raise EvidenceError(
                f"decision {record.decision_id!r} modelIo attempt {index} must be an object"
            )
        if attempt.get("attempt") != index:
            raise EvidenceError(
                f"decision {record.decision_id!r} modelIo attempts must be contiguous"
            )
        request = attempt.get("request")
        response = attempt.get("response")
        if not isinstance(request, Mapping) or not isinstance(response, Mapping):
            raise EvidenceError(
                f"decision {record.decision_id!r} modelIo attempt {index} requires "
                "request and response objects"
            )
        output_text = response.get("outputText")
        refusal = response.get("refusal")
        if output_text is not None and not isinstance(output_text, str):
            raise EvidenceError(
                f"decision {record.decision_id!r} modelIo response outputText must be a string"
            )
        if refusal is not None and not isinstance(refusal, str):
            raise EvidenceError(
                f"decision {record.decision_id!r} modelIo response refusal must be a string"
            )
        validation_error = response.get("validationError")
        if validation_error is not None and not isinstance(validation_error, str):
            raise EvidenceError(
                f"decision {record.decision_id!r} modelIo validationError must be a string"
            )
        normalized_attempts.append(
            {
                "attempt": index,
                "request": dict(request),
                "response": dict(response),
            }
        )

    if selected_attempt != len(normalized_attempts) - 1:
        raise EvidenceError(
            f"decision {record.decision_id!r} selected model attempt must be the final attempt"
        )
    selected_response = normalized_attempts[selected_attempt]["response"]
    if not isinstance(selected_response.get("outputText"), str) or not selected_response.get(
        "outputText"
    ):
        raise EvidenceError(
            f"decision {record.decision_id!r} selected model attempt must preserve outputText"
        )
    if "validationError" in selected_response:
        raise EvidenceError(
            f"decision {record.decision_id!r} selected model attempt cannot be invalid"
        )

    return {
        "schemaVersion": MODEL_IO_SCHEMA_VERSION,
        "provider": provider,
        "selectedAttempt": selected_attempt,
        "attempts": normalized_attempts,
    }


def _model_io_sections(record: EvidenceRecord) -> dict[str, dict[str, Any]] | None:
    model_io = _model_io(record)
    if model_io is None:
        return None

    input_attempts: list[dict[str, Any]] = []
    target_attempts: list[dict[str, Any]] = []
    provenance_attempts: list[dict[str, Any]] = []
    for attempt in model_io["attempts"]:
        index = attempt["attempt"]
        response = attempt["response"]
        input_attempts.append({"attempt": index, "request": attempt["request"]})

        target_attempt: dict[str, Any] = {"attempt": index}
        if "outputText" in response:
            target_attempt["output_text"] = response["outputText"]
        if "refusal" in response:
            target_attempt["refusal"] = response["refusal"]
        target_attempts.append(target_attempt)

        provenance_attempt = {"attempt": index}
        provenance_attempt.update(
            {
                key: value
                for key, value in response.items()
                if key not in {"outputText", "refusal"}
            }
        )
        provenance_attempts.append(provenance_attempt)

    selected_attempt = model_io["selectedAttempt"]
    provider = model_io["provider"]
    return {
        "input": {
            "schema_version": MODEL_IO_SCHEMA_VERSION,
            "provider": provider,
            "attempts": input_attempts,
        },
        "target": {
            "schema_version": MODEL_IO_SCHEMA_VERSION,
            "selected_attempt": selected_attempt,
            "attempts": target_attempts,
        },
        "provenance": {
            "schema_version": MODEL_IO_SCHEMA_VERSION,
            "provider": provider,
            "selected_attempt": selected_attempt,
            "attempts": provenance_attempts,
        },
    }


def _input_for_record(record: EvidenceRecord) -> dict[str, Any]:
    common = {
        "decision_type": record.decision_type,
        "seat": record.seat,
        "observation_schema": record.observation_schema,
        "observation": record.observation,
    }
    sections = _model_io_sections(record)
    if sections is not None:
        common["model_io"] = sections["input"]
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
        target: dict[str, Any] = {"chosen_action_id": record.chosen_action_id}
    elif isinstance(record, StructuredDecisionRecord):
        target = {"response": record.response}
    else:
        raise EvidenceError("unsupported raw evidence record type")
    sections = _model_io_sections(record)
    if sections is not None:
        target["model_io"] = sections["target"]
    return target


def _decision_routing(record: EvidenceRecord) -> dict[str, Any]:
    routing = _pilot_metadata(record).get("routing")
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
    metadata = dict(record.metadata)
    pilot_metadata = metadata.get("pilot_metadata")
    if isinstance(pilot_metadata, Mapping) and "modelIo" in pilot_metadata:
        sanitized_pilot_metadata = dict(pilot_metadata)
        sanitized_pilot_metadata.pop("modelIo", None)
        metadata["pilot_metadata"] = sanitized_pilot_metadata
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
        "metadata": metadata,
    }
    sections = _model_io_sections(record)
    if sections is not None:
        provenance["model_io"] = sections["provenance"]
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
        _model_io(record)

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


def _validate_envelope_model_io(
    input_value: Mapping[str, Any],
    target: Mapping[str, Any],
    provenance: Mapping[str, Any],
) -> None:
    present = ["model_io" in section for section in (input_value, target, provenance)]
    if not any(present):
        return
    if not all(present):
        raise EvidenceError("raw evidence model_io must appear in input, target, and provenance")

    input_io = input_value["model_io"]
    target_io = target["model_io"]
    provenance_io = provenance["model_io"]
    if not all(isinstance(value, Mapping) for value in (input_io, target_io, provenance_io)):
        raise EvidenceError("raw evidence model_io sections must be objects")
    if any(
        value.get("schema_version") != MODEL_IO_SCHEMA_VERSION
        for value in (input_io, target_io, provenance_io)
    ):
        raise EvidenceError("raw evidence model_io schema_version is unsupported")
    provider = _require_string(input_io.get("provider"), "raw evidence model_io.provider")
    if provenance_io.get("provider") != provider:
        raise EvidenceError("raw evidence model_io provider mismatch")
    selected_attempt = target_io.get("selected_attempt")
    if type(selected_attempt) is not int or selected_attempt < 0:
        raise EvidenceError("raw evidence model_io selected_attempt must be non-negative")
    if provenance_io.get("selected_attempt") != selected_attempt:
        raise EvidenceError("raw evidence model_io selected_attempt mismatch")

    input_attempts = input_io.get("attempts")
    target_attempts = target_io.get("attempts")
    provenance_attempts = provenance_io.get("attempts")
    if not all(isinstance(value, list) for value in (input_attempts, target_attempts, provenance_attempts)):
        raise EvidenceError("raw evidence model_io attempts must be arrays")
    if not input_attempts or len(input_attempts) != len(target_attempts) or len(input_attempts) != len(provenance_attempts):
        raise EvidenceError("raw evidence model_io attempt counts must match")
    if selected_attempt != len(input_attempts) - 1:
        raise EvidenceError("raw evidence selected model attempt must be final")

    for index, (input_attempt, target_attempt, provenance_attempt) in enumerate(
        zip(input_attempts, target_attempts, provenance_attempts)
    ):
        if not all(
            isinstance(value, Mapping)
            for value in (input_attempt, target_attempt, provenance_attempt)
        ):
            raise EvidenceError("raw evidence model_io attempt entries must be objects")
        if any(
            value.get("attempt") != index
            for value in (input_attempt, target_attempt, provenance_attempt)
        ):
            raise EvidenceError("raw evidence model_io attempt indexes must be contiguous")
        if not isinstance(input_attempt.get("request"), Mapping):
            raise EvidenceError("raw evidence model_io request must be an object")
        output_text = target_attempt.get("output_text")
        refusal = target_attempt.get("refusal")
        if output_text is not None and not isinstance(output_text, str):
            raise EvidenceError("raw evidence model output_text must be a string")
        if refusal is not None and not isinstance(refusal, str):
            raise EvidenceError("raw evidence model refusal must be a string")
    if not isinstance(target_attempts[selected_attempt].get("output_text"), str) or not target_attempts[
        selected_attempt
    ].get("output_text"):
        raise EvidenceError("selected raw evidence model attempt must preserve output_text")


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
        _validate_envelope_model_io(input_value, target, provenance)
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


def _model_io_accounting(decisions: list[Mapping[str, Any]]) -> tuple[int | None, int | None]:
    measured = False
    model_input_bytes = 0
    model_output_bytes = 0
    for decision in decisions:
        input_value = decision.get("input")
        target = decision.get("target")
        if not isinstance(input_value, Mapping) or not isinstance(target, Mapping):
            continue
        input_io = input_value.get("model_io")
        target_io = target.get("model_io")
        if not isinstance(input_io, Mapping) or not isinstance(target_io, Mapping):
            continue
        measured = True
        attempts = input_io.get("attempts", [])
        if isinstance(attempts, list):
            for attempt in attempts:
                if isinstance(attempt, Mapping) and isinstance(attempt.get("request"), Mapping):
                    model_input_bytes += len(_canonical_json_bytes(attempt["request"]))
        target_attempts = target_io.get("attempts", [])
        if isinstance(target_attempts, list):
            for attempt in target_attempts:
                if not isinstance(attempt, Mapping):
                    continue
                output_text = attempt.get("output_text")
                if isinstance(output_text, str):
                    model_output_bytes += len(output_text.encode("utf-8"))
                refusal = attempt.get("refusal")
                if isinstance(refusal, str):
                    model_output_bytes += len(refusal.encode("utf-8"))
    if not measured:
        return None, None
    return model_input_bytes, model_output_bytes


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
        model_input_bytes, model_output_bytes = _model_io_accounting(decisions)
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
            model_input_bytes=model_input_bytes,
            model_output_bytes=model_output_bytes,
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
