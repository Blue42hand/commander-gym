"""Materialize frontier-teacher decisions through the settled #53 dataset path.

This module does not define another corpus format. It selects exact decisions from
immutable raw-evidence artifacts into the existing :class:`DatasetManifest` contract,
then callers use :mod:`commander_gym.manifest_export` for deterministic materialization.
Selection is driven only by durable provenance: completed binding-v1 evidence whose
canonical composed Pilot route was actually handled by ``frontier_escalation``.

The resulting manifest preserves the raw evidence input/target/provenance boundary,
canonical Binding/Pilot/subsystem lineage, and frozen-held-out membership. Failed or
partial trajectories remain diagnostic evidence and are never silently promoted to
training labels here.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

from .dataset_manifest import (
    DatasetEvidenceSelection,
    DatasetManifest,
    DatasetManifestError,
    DatasetSplit,
)
from .evidence import (
    IDENTITY_EPOCH_BINDING_V1,
    EvidenceError,
    validate_raw_evidence_envelope,
)
from .identity import IdentityError, IdentityRef
from .manifest_export import (
    MANIFEST_TRAINING_TRANSFORM_NAME,
    MANIFEST_TRAINING_TRANSFORM_VERSION,
)
from .pilot_composition import (
    PILOT_ROUTING_MODE_STATIC_ORDER_V1,
    PILOT_ROUTING_PROVENANCE_SCHEMA_VERSION,
)
from .storage import LocalArtifactStore, StorageError, StorageLayout

TEACHER_DATASET_QUERY_SCHEMA_VERSION = 1
TEACHER_ROUTING_ROLE = "frontier_escalation"
TEACHER_TRAIN_SPLIT = "train"
TEACHER_FROZEN_TEST_SPLIT = "held-out"


class TeacherDatasetError(DatasetManifestError):
    """Raised when frontier-teacher evidence cannot be selected unambiguously."""


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise TeacherDatasetError(f"{label} must be a non-empty string")
    return value


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TeacherDatasetError(f"{label} must be an object")
    return value


def _load_evidence(
    artifacts: LocalArtifactStore,
    artifact_id: str,
) -> Mapping[str, Any]:
    _require_string(artifact_id, "evidence artifact ID")
    try:
        payload = artifacts.read_bytes(artifact_id)
    except StorageError as exc:
        raise TeacherDatasetError(f"raw evidence artifact not found: {artifact_id}") from exc
    try:
        evidence = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TeacherDatasetError("raw evidence artifact is not UTF-8 JSON") from exc
    if not isinstance(evidence, Mapping):
        raise TeacherDatasetError("raw evidence artifact must decode to an object")
    try:
        validate_raw_evidence_envelope(evidence)
    except EvidenceError as exc:
        raise TeacherDatasetError("teacher source is not valid immutable raw evidence") from exc
    return evidence


def _frontier_teacher_decision_id(decision: Any) -> str | None:
    """Return the decision ID only for an exact composed frontier-escalation route.

    Pre-composition/legacy routing records are not guessed into this population. Once a
    record advertises the composed routing shape, malformed identity or component
    provenance fails closed instead of being silently skipped.
    """

    decision_mapping = _require_mapping(decision, "raw evidence decision")
    provenance = _require_mapping(
        decision_mapping.get("provenance"), "raw evidence decision provenance"
    )
    routing = _require_mapping(provenance.get("routing"), "decision routing provenance")
    handled_by = routing.get("handledBy")
    if handled_by is None:
        return None
    handled = _require_mapping(handled_by, "decision routing handledBy")
    if handled.get("role") != TEACHER_ROUTING_ROLE:
        return None

    if routing.get("schemaVersion") != PILOT_ROUTING_PROVENANCE_SCHEMA_VERSION:
        raise TeacherDatasetError("frontier teacher route has unsupported routing schemaVersion")
    if routing.get("mode") != PILOT_ROUTING_MODE_STATIC_ORDER_V1:
        raise TeacherDatasetError("frontier teacher route has unsupported routing mode")
    _require_string(routing.get("path"), "frontier teacher routing path")
    _require_string(routing.get("pilotId"), "frontier teacher pilotId")
    _require_string(routing.get("pilotRevision"), "frontier teacher pilotRevision")

    component = _require_mapping(
        handled.get("component"), "frontier teacher handled component"
    )
    _require_string(component.get("kind"), "frontier teacher component kind")
    _require_string(component.get("artifactId"), "frontier teacher component artifactId")
    _require_string(component.get("version"), "frontier teacher component version")
    _require_string(component.get("digest"), "frontier teacher component digest")

    binding_payload = _require_mapping(
        provenance.get("binding"), "frontier teacher Binding provenance"
    )
    try:
        binding_ref = IdentityRef.from_dict(binding_payload)
    except IdentityError as exc:
        raise TeacherDatasetError("frontier teacher Binding provenance is invalid") from exc
    if binding_ref.artifact_type != "binding":
        raise TeacherDatasetError("frontier teacher provenance must reference a Binding")

    _require_mapping(provenance.get("pilot"), "frontier teacher pilot provenance")
    return _require_string(provenance.get("decision_id"), "frontier teacher decision_id")


def build_teacher_dataset_manifest(
    layout: StorageLayout,
    *,
    dataset_id: str,
    version: str,
    created_at: str,
    generator_revision: str,
    evidence_artifact_ids: Iterable[str],
    frozen_test_decision_ids: Iterable[str] = (),
    required_annotation_artifact_ids: Iterable[str] = (),
    purpose: str = "frontier teacher imitation",
) -> DatasetManifest:
    """Build a deterministic #53 manifest for actual frontier-escalation decisions.

    ``evidence_artifact_ids`` is the exact immutable source population considered by
    this selection. Only completed, non-diagnostic ``binding-v1`` evidence contributes
    training/evaluation labels. ``frozen_test_decision_ids`` must name eligible teacher
    decisions from that exact population; those decisions are frozen into a held-out
    split and cannot later be materialized by the training exporter.
    """

    if not isinstance(layout, StorageLayout):
        raise TeacherDatasetError("layout must be a StorageLayout")
    _require_string(dataset_id, "dataset_id")
    _require_string(version, "dataset version")
    _require_string(created_at, "dataset created_at")
    _require_string(generator_revision, "dataset generator_revision")
    _require_string(purpose, "dataset purpose")

    source_ids = tuple(evidence_artifact_ids)
    if not source_ids:
        raise TeacherDatasetError("teacher dataset requires at least one evidence artifact")
    if any(not isinstance(item, str) or not item for item in source_ids):
        raise TeacherDatasetError("evidence artifact IDs must be non-empty strings")
    if len(source_ids) != len(set(source_ids)):
        raise TeacherDatasetError("evidence artifact IDs must be unique")
    source_ids = tuple(sorted(source_ids))

    frozen_ids = tuple(frozen_test_decision_ids)
    if any(not isinstance(item, str) or not item for item in frozen_ids):
        raise TeacherDatasetError("frozen-test decision IDs must be non-empty strings")
    if len(frozen_ids) != len(set(frozen_ids)):
        raise TeacherDatasetError("frozen-test decision IDs must be unique")
    frozen = frozenset(frozen_ids)

    annotation_ids = tuple(required_annotation_artifact_ids)
    if len(annotation_ids) != len(set(annotation_ids)):
        raise TeacherDatasetError("required annotation artifact IDs must be unique")

    artifacts = LocalArtifactStore(layout)
    eligible_by_source: dict[str, tuple[str, ...]] = {}
    diagnostic_teacher_ids: set[str] = set()
    all_teacher_ids: set[str] = set()

    for artifact_id in source_ids:
        evidence = _load_evidence(artifacts, artifact_id)
        if evidence.get("identity_epoch") != IDENTITY_EPOCH_BINDING_V1:
            continue
        decisions = evidence.get("decisions")
        if not isinstance(decisions, list):
            raise TeacherDatasetError("raw evidence decisions must be an array")

        teacher_ids = tuple(
            decision_id
            for decision in decisions
            if (decision_id := _frontier_teacher_decision_id(decision)) is not None
        )
        if not teacher_ids:
            continue
        if len(teacher_ids) != len(set(teacher_ids)):
            raise TeacherDatasetError("teacher source contains duplicate decision IDs")
        all_teacher_ids.update(teacher_ids)

        qualification = _require_mapping(
            evidence.get("qualification"), "raw evidence qualification"
        )
        if (
            qualification.get("classification") != "completed"
            or qualification.get("diagnostic_only") is not False
        ):
            diagnostic_teacher_ids.update(teacher_ids)
            continue
        eligible_by_source[artifact_id] = tuple(sorted(teacher_ids))

    unknown_frozen = sorted(frozen - all_teacher_ids)
    if unknown_frozen:
        raise TeacherDatasetError(
            "frozen-test IDs are not frontier-teacher decisions in the source population: "
            + ", ".join(unknown_frozen)
        )
    diagnostic_frozen = sorted(frozen & diagnostic_teacher_ids)
    if diagnostic_frozen:
        raise TeacherDatasetError(
            "diagnostic-only teacher decisions cannot become held-out gameplay labels: "
            + ", ".join(diagnostic_frozen)
        )

    train_selections: list[DatasetEvidenceSelection] = []
    held_out_selections: list[DatasetEvidenceSelection] = []
    for artifact_id in sorted(eligible_by_source):
        decision_ids = eligible_by_source[artifact_id]
        train_ids = tuple(item for item in decision_ids if item not in frozen)
        held_out_ids = tuple(item for item in decision_ids if item in frozen)
        if train_ids:
            train_selections.append(DatasetEvidenceSelection(artifact_id, train_ids))
        if held_out_ids:
            held_out_selections.append(DatasetEvidenceSelection(artifact_id, held_out_ids))

    if not train_selections:
        raise TeacherDatasetError(
            "teacher dataset requires at least one completed frontier decision in train"
        )

    splits: list[DatasetSplit] = [
        DatasetSplit(
            name=TEACHER_TRAIN_SPLIT,
            role="train",
            selections=tuple(train_selections),
        )
    ]
    if held_out_selections:
        splits.append(
            DatasetSplit(
                name=TEACHER_FROZEN_TEST_SPLIT,
                role="frozen_test",
                selections=tuple(held_out_selections),
            )
        )

    return DatasetManifest(
        dataset_id=dataset_id,
        version=version,
        purpose=purpose,
        created_at=created_at,
        source_population="immutable binding-v1 raw evidence",
        source_query={
            "schema_version": TEACHER_DATASET_QUERY_SCHEMA_VERSION,
            "evidence_artifact_ids": list(source_ids),
            "identity_epoch": IDENTITY_EPOCH_BINDING_V1,
            "qualification": {
                "classification": "completed",
                "diagnostic_only": False,
            },
            "routing": {
                "schema_version": PILOT_ROUTING_PROVENANCE_SCHEMA_VERSION,
                "mode": PILOT_ROUTING_MODE_STATIC_ORDER_V1,
                "handled_by_role": TEACHER_ROUTING_ROLE,
            },
        },
        selection_rules={
            "target": "recorded teacher choice",
            "requires_binding": True,
            "requires_exact_pilot_route": True,
        },
        exclusion_rules={
            "diagnostic_only_evidence": True,
            "non_frontier_routes": True,
            "frozen_test_from_training": True,
        },
        required_annotation_artifact_ids=tuple(sorted(annotation_ids)),
        transformations=(
            {
                "name": MANIFEST_TRAINING_TRANSFORM_NAME,
                "version": MANIFEST_TRAINING_TRANSFORM_VERSION,
            },
        ),
        splits=tuple(splits),
        generator_revision=generator_revision,
    )
