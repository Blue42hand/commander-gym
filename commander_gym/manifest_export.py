"""Materialize deterministic training rows from immutable dataset manifests.

This module is deliberately downstream of :mod:`commander_gym.dataset_manifest` and
:mod:`commander_gym.evidence`. It does not reinterpret game state or reconstruct
missing provenance. Instead it loads an exact cataloged dataset-manifest artifact,
selects the exact raw-evidence decisions declared by one ``train`` split, and emits
rows that preserve the raw evidence ``input`` / ``target`` / ``provenance`` boundary.

Frozen-test, validation, and diagnostic splits are rejected by this training export
path. Evaluation materialization can use a separate explicit path later; keeping the
training path narrow makes benchmark leakage fail closed by construction.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from .dataset_export import DATASET_EXPORT_SCHEMA_VERSION
from .dataset_manifest import (
    DatasetManifest,
    DatasetManifestError,
    DatasetManifestStore,
    DatasetSplit,
    validate_export_selection,
)
from .evidence import EvidenceError, validate_raw_evidence_envelope
from .storage import LocalArtifactStore, StorageError, StorageLayout

MANIFEST_TRAINING_TRANSFORM_NAME = "input-target-provenance"
MANIFEST_TRAINING_TRANSFORM_VERSION = 1


class ManifestExportError(DatasetManifestError):
    """Raised when a manifest cannot be materialized safely for training."""


def _json_clone(value: Any) -> Any:
    """Detach exported rows from the loaded immutable evidence object."""

    return json.loads(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ManifestExportError(f"{label} must be an object")
    return value


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ManifestExportError(f"{label} must be a non-empty string")
    return value


def _load_cataloged_manifest(
    layout: StorageLayout,
    manifest_artifact_id: str,
) -> DatasetManifest:
    store = DatasetManifestStore(layout)
    try:
        manifest = store.read_artifact(manifest_artifact_id)
        cataloged = store.read(dataset_id=manifest.dataset_id, version=manifest.version)
    except DatasetManifestError as exc:
        raise ManifestExportError("training export requires a valid cataloged dataset manifest") from exc
    if cataloged.to_dict() != manifest.to_dict():
        raise ManifestExportError(
            "dataset manifest artifact does not match the immutable dataset catalog entry"
        )
    return manifest


def _validate_training_transform(manifest: DatasetManifest) -> None:
    expected = (
        {
            "name": MANIFEST_TRAINING_TRANSFORM_NAME,
            "version": MANIFEST_TRAINING_TRANSFORM_VERSION,
        },
    )
    normalized = tuple(dict(item) for item in manifest.transformations)
    if normalized != expected:
        raise ManifestExportError(
            "training export only supports the explicit input-target-provenance v1 transform"
        )


def _load_evidence(
    artifacts: LocalArtifactStore,
    artifact_id: str,
) -> Mapping[str, Any]:
    try:
        payload = artifacts.read_bytes(artifact_id)
    except StorageError as exc:
        raise ManifestExportError(f"raw evidence artifact not found: {artifact_id}") from exc
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestExportError("raw evidence artifact is not UTF-8 JSON") from exc
    if not isinstance(value, Mapping):
        raise ManifestExportError("raw evidence artifact must decode to an object")
    try:
        validate_raw_evidence_envelope(value)
    except EvidenceError as exc:
        raise ManifestExportError("dataset source is not valid immutable raw evidence") from exc
    return value


def _training_split(manifest: DatasetManifest, split_name: str) -> DatasetSplit:
    split = manifest.split(split_name)
    if split.role != "train":
        raise ManifestExportError(
            f"training export requires a train split; {split_name!r} has role {split.role!r}"
        )
    return split


def _decision_id(decision: Mapping[str, Any]) -> str:
    provenance = _require_mapping(decision.get("provenance"), "raw evidence decision provenance")
    return _require_string(provenance.get("decision_id"), "raw evidence decision_id")


def _dataset_provenance(
    *,
    manifest: DatasetManifest,
    manifest_artifact_id: str,
    split: DatasetSplit,
    evidence_artifact_id: str,
) -> dict[str, Any]:
    return {
        "manifest_artifact_id": manifest_artifact_id,
        "dataset_id": manifest.dataset_id,
        "dataset_version": manifest.version,
        "dataset_digest": manifest.dataset_digest,
        "split_name": split.name,
        "split_role": split.role,
        "source_evidence_artifact_id": evidence_artifact_id,
        "required_annotation_artifact_ids": sorted(
            manifest.required_annotation_artifact_ids
        ),
        "transformations": [dict(item) for item in manifest.transformations],
        "generator_revision": manifest.generator_revision,
    }


def _source_provenance(
    evidence: Mapping[str, Any],
    evidence_artifact_id: str,
) -> dict[str, Any]:
    run = _require_mapping(evidence.get("run"), "raw evidence run")
    return {
        "artifact_id": evidence_artifact_id,
        "evidence_schema_version": evidence.get("evidence_schema_version"),
        "identity_epoch": evidence.get("identity_epoch"),
        "producer": _json_clone(evidence.get("producer")),
        "qualification": _json_clone(evidence.get("qualification")),
        "run": {
            "run_id": run.get("run_id"),
            "game_id": run.get("game_id"),
            "engine": _json_clone(run.get("engine")),
            "termination": _json_clone(run.get("termination")),
        },
    }


def build_manifest_training_rows(
    layout: StorageLayout,
    manifest_artifact_id: str,
    *,
    split_name: str = "train",
) -> list[dict[str, Any]]:
    """Materialize one exact manifest ``train`` split into deterministic rows.

    Every row is copied directly from immutable raw evidence. Only dataset/source
    lineage is added, and it is added under ``provenance``. The exporter never moves
    outcome, routing, Binding identity, engine metadata, or other post-choice fields
    into ``input``.
    """

    if not isinstance(layout, StorageLayout):
        raise ManifestExportError("layout must be a StorageLayout")
    _require_string(manifest_artifact_id, "manifest_artifact_id")
    _require_string(split_name, "split_name")

    manifest = _load_cataloged_manifest(layout, manifest_artifact_id)
    _validate_training_transform(manifest)
    split = _training_split(manifest, split_name)
    artifacts = LocalArtifactStore(layout)

    rows: list[dict[str, Any]] = []
    exported_ids: list[str] = []
    for selection in sorted(
        split.selections,
        key=lambda item: item.evidence_artifact_id,
    ):
        evidence = _load_evidence(artifacts, selection.evidence_artifact_id)
        decisions = evidence.get("decisions")
        if not isinstance(decisions, list):
            raise ManifestExportError("raw evidence decisions must be an array")
        wanted = set(selection.decision_ids)
        found: set[str] = set()
        source_lineage = _source_provenance(evidence, selection.evidence_artifact_id)
        dataset_lineage = _dataset_provenance(
            manifest=manifest,
            manifest_artifact_id=manifest_artifact_id,
            split=split,
            evidence_artifact_id=selection.evidence_artifact_id,
        )

        for decision in decisions:
            decision_mapping = _require_mapping(decision, "raw evidence decision")
            decision_id = _decision_id(decision_mapping)
            if decision_id not in wanted:
                continue
            if decision_id in found:
                raise ManifestExportError(
                    f"raw evidence contains duplicate selected decision_id {decision_id!r}"
                )
            found.add(decision_id)
            exported_ids.append(decision_id)
            provenance = dict(
                _require_mapping(
                    decision_mapping.get("provenance"),
                    "raw evidence decision provenance",
                )
            )
            provenance["source_evidence"] = source_lineage
            provenance["dataset_manifest"] = dataset_lineage
            rows.append(
                {
                    "dataset_schema_version": DATASET_EXPORT_SCHEMA_VERSION,
                    "record_kind": decision_mapping.get("record_kind"),
                    "input": _json_clone(
                        _require_mapping(decision_mapping.get("input"), "raw evidence input")
                    ),
                    "target": _json_clone(
                        _require_mapping(decision_mapping.get("target"), "raw evidence target")
                    ),
                    "provenance": _json_clone(provenance),
                }
            )

        missing = sorted(wanted - found)
        if missing:
            raise ManifestExportError(
                "dataset split selects decisions missing from raw evidence during export: "
                + ", ".join(missing)
            )

    if set(exported_ids) != set(split.decision_ids) or len(exported_ids) != len(
        split.decision_ids
    ):
        raise ManifestExportError(
            "materialized training rows do not exactly match frozen manifest membership"
        )
    try:
        validate_export_selection(manifest, split_name, exported_ids)
    except DatasetManifestError as exc:
        raise ManifestExportError("training export escaped frozen manifest membership") from exc
    return rows


def write_manifest_training_jsonl(
    path: str | os.PathLike[str],
    layout: StorageLayout,
    manifest_artifact_id: str,
    *,
    split_name: str = "train",
) -> None:
    """Atomically write deterministic JSONL for one exact manifest training split."""

    rows = build_manifest_training_rows(
        layout,
        manifest_artifact_id,
        split_name=split_name,
    )
    target = Path(path)
    if not target.parent.is_dir():
        raise ManifestExportError(
            f"dataset parent directory does not exist: {target.parent}"
        )
    payload = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows
    )
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = handle.name
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)
