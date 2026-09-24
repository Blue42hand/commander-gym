"""Immutable model/checkpoint lineage over exact dataset manifests.

This module records identity/provenance only.  It deliberately does not define
training algorithms, promotion policy, benchmark thresholds, or pilot routing;
those belong to later learning work.  Physical placement and content addressing
are delegated to :mod:`commander_gym.storage`.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from .dataset_manifest import DatasetManifest, DatasetManifestError, DatasetManifestStore
from .evidence import EvidenceError
from .external_provenance import ExternalProvenanceError, ExternalProvenanceStore
from .storage import LocalArtifactStore, StorageError, StorageLayout, StoredBlob, parse_artifact_id

MODEL_LINEAGE_SCHEMA_VERSION = 1
MODEL_LINEAGE_CATALOG_SCHEMA_VERSION = 1
MODEL_LINEAGE_KIND = "commander-gym.model-lineage"


class ModelLineageError(EvidenceError):
    """Raised when model lineage cannot be validated or joined unambiguously."""


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ModelLineageError(f"{label} must be a non-empty string")
    return value


def _artifact_id(value: Any, label: str) -> str:
    candidate = _require_string(value, label)
    try:
        parse_artifact_id(candidate)
    except StorageError as exc:
        raise ModelLineageError(f"{label} must be a valid artifact ID") from exc
    return candidate


@dataclass(frozen=True)
class DatasetLineageRef:
    """Exact immutable dataset manifest consumed by one model/checkpoint build."""

    manifest_artifact_id: str
    dataset_id: str
    version: str
    dataset_digest: str

    def validate(self) -> None:
        _artifact_id(self.manifest_artifact_id, "dataset manifest artifact ID")
        _require_string(self.dataset_id, "dataset_id")
        _require_string(self.version, "dataset version")
        _artifact_id(self.dataset_digest, "dataset_digest")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "manifest_artifact_id": self.manifest_artifact_id,
            "dataset_id": self.dataset_id,
            "version": self.version,
            "dataset_digest": self.dataset_digest,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DatasetLineageRef":
        if not isinstance(value, Mapping):
            raise ModelLineageError("dataset lineage reference must be an object")
        result = cls(
            manifest_artifact_id=value.get("manifest_artifact_id"),
            dataset_id=value.get("dataset_id"),
            version=value.get("version"),
            dataset_digest=value.get("dataset_digest"),
        )
        result.validate()
        return result


@dataclass(frozen=True)
class ExternalModelLineageRef:
    """Exact immutable imported model manifest used as a parent/initialization."""

    manifest_artifact_id: str
    model_id: str
    version: str
    model_digest: str
    checkpoint_artifact_id: str

    def validate(self) -> None:
        _artifact_id(self.manifest_artifact_id, "external model manifest artifact ID")
        _require_string(self.model_id, "external model_id")
        _require_string(self.version, "external model version")
        _artifact_id(self.model_digest, "external model_digest")
        _artifact_id(self.checkpoint_artifact_id, "external checkpoint artifact ID")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "manifest_artifact_id": self.manifest_artifact_id,
            "model_id": self.model_id,
            "version": self.version,
            "model_digest": self.model_digest,
            "checkpoint_artifact_id": self.checkpoint_artifact_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExternalModelLineageRef":
        if not isinstance(value, Mapping):
            raise ModelLineageError("external model lineage reference must be an object")
        result = cls(
            manifest_artifact_id=value.get("manifest_artifact_id"),
            model_id=value.get("model_id"),
            version=value.get("version"),
            model_digest=value.get("model_digest"),
            checkpoint_artifact_id=value.get("checkpoint_artifact_id"),
        )
        result.validate()
        return result


@dataclass(frozen=True)
class ModelArtifactRef:
    """One content-addressed artifact produced by a model/checkpoint build."""

    role: str
    artifact_id: str

    def validate(self) -> None:
        _require_string(self.role, "model artifact role")
        _artifact_id(self.artifact_id, "model artifact ID")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {"role": self.role, "artifact_id": self.artifact_id}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ModelArtifactRef":
        if not isinstance(value, Mapping):
            raise ModelLineageError("model artifact reference must be an object")
        result = cls(role=value.get("role"), artifact_id=value.get("artifact_id"))
        result.validate()
        return result


@dataclass(frozen=True)
class ModelLineageRecord:
    """Immutable identity lineage from produced artifacts to exact datasets."""

    model_id: str
    version: str
    created_at: str
    producer_revision: str
    datasets: tuple[DatasetLineageRef, ...]
    artifacts: tuple[ModelArtifactRef, ...]
    external_parent_models: tuple[ExternalModelLineageRef, ...] = ()
    schema_version: int = MODEL_LINEAGE_SCHEMA_VERSION
    kind: str = MODEL_LINEAGE_KIND

    def validate(self) -> None:
        if self.schema_version != MODEL_LINEAGE_SCHEMA_VERSION:
            raise ModelLineageError(
                f"unsupported model lineage schema_version {self.schema_version!r}; "
                f"expected {MODEL_LINEAGE_SCHEMA_VERSION}"
            )
        if self.kind != MODEL_LINEAGE_KIND:
            raise ModelLineageError(f"model lineage kind must be {MODEL_LINEAGE_KIND!r}")
        _require_string(self.model_id, "model_id")
        _require_string(self.version, "model version")
        _require_string(self.created_at, "model created_at")
        _require_string(self.producer_revision, "producer_revision")

        if not isinstance(self.datasets, tuple):
            raise ModelLineageError("model lineage datasets must be a tuple")
        if not isinstance(self.external_parent_models, tuple):
            raise ModelLineageError("external_parent_models must be a tuple")
        if not self.datasets and not self.external_parent_models:
            raise ModelLineageError(
                "model lineage requires a dataset or an external parent model"
            )
        dataset_artifacts: set[str] = set()
        dataset_identities: set[tuple[str, str]] = set()
        for item in self.datasets:
            if not isinstance(item, DatasetLineageRef):
                raise ModelLineageError("datasets must contain DatasetLineageRef values")
            item.validate()
            if item.manifest_artifact_id in dataset_artifacts:
                raise ModelLineageError("dataset manifest artifact references must be unique")
            identity = (item.dataset_id, item.version)
            if identity in dataset_identities:
                raise ModelLineageError("dataset id/version references must be unique")
            dataset_artifacts.add(item.manifest_artifact_id)
            dataset_identities.add(identity)

        external_parent_artifacts: set[str] = set()
        external_parent_identities: set[tuple[str, str]] = set()
        for item in self.external_parent_models:
            if not isinstance(item, ExternalModelLineageRef):
                raise ModelLineageError(
                    "external_parent_models must contain ExternalModelLineageRef values"
                )
            item.validate()
            if item.manifest_artifact_id in external_parent_artifacts:
                raise ModelLineageError(
                    "external parent model manifest references must be unique"
                )
            identity = (item.model_id, item.version)
            if identity in external_parent_identities:
                raise ModelLineageError(
                    "external parent model id/version references must be unique"
                )
            external_parent_artifacts.add(item.manifest_artifact_id)
            external_parent_identities.add(identity)

        if not isinstance(self.artifacts, tuple) or not self.artifacts:
            raise ModelLineageError("model lineage requires at least one produced artifact")
        artifact_pairs: set[tuple[str, str]] = set()
        for item in self.artifacts:
            if not isinstance(item, ModelArtifactRef):
                raise ModelLineageError("artifacts must contain ModelArtifactRef values")
            item.validate()
            pair = (item.role, item.artifact_id)
            if pair in artifact_pairs:
                raise ModelLineageError("model artifact role/ID references must be unique")
            artifact_pairs.add(pair)

    def _digest_payload(self) -> dict[str, Any]:
        self.validate()
        result: dict[str, Any] = {
            "model_lineage_schema_version": self.schema_version,
            "kind": self.kind,
            "model_id": self.model_id,
            "version": self.version,
            "created_at": self.created_at,
            "producer_revision": self.producer_revision,
            "datasets": [
                item.to_dict()
                for item in sorted(
                    self.datasets,
                    key=lambda item: (
                        item.dataset_id,
                        item.version,
                        item.manifest_artifact_id,
                    ),
                )
            ],
            "artifacts": [
                item.to_dict()
                for item in sorted(
                    self.artifacts,
                    key=lambda item: (item.role, item.artifact_id),
                )
            ],
        }
        if self.external_parent_models:
            result["external_parent_models"] = [
                item.to_dict()
                for item in sorted(
                    self.external_parent_models,
                    key=lambda item: (
                        item.model_id,
                        item.version,
                        item.manifest_artifact_id,
                    ),
                )
            ]
        return result

    @property
    def lineage_digest(self) -> str:
        return "sha256:" + hashlib.sha256(_canonical_json_bytes(self._digest_payload())).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        result = self._digest_payload()
        result["lineage_digest"] = self.lineage_digest
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ModelLineageRecord":
        if not isinstance(value, Mapping):
            raise ModelLineageError("model lineage record must be an object")
        raw_datasets = value.get("datasets", [])
        raw_artifacts = value.get("artifacts")
        raw_external_parents = value.get("external_parent_models", [])
        if not isinstance(raw_datasets, list):
            raise ModelLineageError("model lineage datasets must be an array")
        if not isinstance(raw_artifacts, list):
            raise ModelLineageError("model lineage artifacts must be an array")
        if not isinstance(raw_external_parents, list):
            raise ModelLineageError("external_parent_models must be an array")
        record = cls(
            model_id=value.get("model_id"),
            version=value.get("version"),
            created_at=value.get("created_at"),
            producer_revision=value.get("producer_revision"),
            datasets=tuple(DatasetLineageRef.from_dict(item) for item in raw_datasets),
            artifacts=tuple(ModelArtifactRef.from_dict(item) for item in raw_artifacts),
            external_parent_models=tuple(
                ExternalModelLineageRef.from_dict(item) for item in raw_external_parents
            ),
            schema_version=value.get("model_lineage_schema_version"),
            kind=value.get("kind"),
        )
        record.validate()
        expected_digest = _artifact_id(value.get("lineage_digest"), "lineage_digest")
        if record.lineage_digest != expected_digest:
            raise ModelLineageError("lineage_digest does not match canonical lineage content")
        return record


@dataclass(frozen=True)
class ModelLineageWriteResult:
    artifact: StoredBlob
    catalog_path: Path
    lineage_digest: str
    source_evidence_artifact_ids: tuple[str, ...]
    source_external_manifest_artifact_ids: tuple[str, ...]
    external_parent_model_manifest_artifact_ids: tuple[str, ...]


class ModelLineageStore:
    """Store model lineage through the #74 content-addressed storage substrate."""

    def __init__(self, layout: StorageLayout):
        if not isinstance(layout, StorageLayout):
            raise ModelLineageError("layout must be a StorageLayout")
        self.layout = layout
        self.artifacts = LocalArtifactStore(layout)
        self.datasets = DatasetManifestStore(layout)
        self.external = ExternalProvenanceStore(layout)

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _catalog_path(self, *, model_id: str, version: str) -> Path:
        model_digest = self._digest(_require_string(model_id, "model_id"))
        version_digest = self._digest(_require_string(version, "model version"))
        return (
            self.layout.path("models")
            / "lineage"
            / model_digest[:2]
            / model_digest
            / f"{version_digest}.json"
        )

    def _resolve_datasets(
        self,
        record: ModelLineageRecord,
    ) -> tuple[tuple[DatasetManifest, ...], tuple[str, ...], tuple[str, ...]]:
        manifests: list[DatasetManifest] = []
        source_evidence: set[str] = set()
        source_external: set[str] = set()
        for ref in record.datasets:
            try:
                manifest = self.datasets.read_artifact(ref.manifest_artifact_id)
            except DatasetManifestError as exc:
                raise ModelLineageError(
                    f"dataset manifest artifact is invalid: {ref.manifest_artifact_id}"
                ) from exc
            if manifest.dataset_id != ref.dataset_id or manifest.version != ref.version:
                raise ModelLineageError("dataset lineage identity does not match exact manifest")
            if manifest.dataset_digest != ref.dataset_digest:
                raise ModelLineageError("dataset lineage digest does not match exact manifest")
            manifests.append(manifest)
            for split in manifest.splits:
                for selection in split.selections:
                    source_evidence.add(selection.evidence_artifact_id)
                for selection in split.external_selections:
                    source_external.add(selection.source_manifest_artifact_id)
        return (
            tuple(manifests),
            tuple(sorted(source_evidence)),
            tuple(sorted(source_external)),
        )

    def _resolve_external_parent_models(
        self, record: ModelLineageRecord
    ) -> tuple[str, ...]:
        resolved: list[str] = []
        for ref in record.external_parent_models:
            try:
                manifest = self.external.read_model_artifact(ref.manifest_artifact_id)
            except ExternalProvenanceError as exc:
                raise ModelLineageError(
                    f"external parent model manifest is invalid: {ref.manifest_artifact_id}"
                ) from exc
            if manifest.model_id != ref.model_id or manifest.version != ref.version:
                raise ModelLineageError(
                    "external parent model identity does not match exact manifest"
                )
            if manifest.model_digest != ref.model_digest:
                raise ModelLineageError(
                    "external parent model digest does not match exact manifest"
                )
            if manifest.checkpoint_artifact_id != ref.checkpoint_artifact_id:
                raise ModelLineageError(
                    "external parent checkpoint does not match exact manifest"
                )
            resolved.append(ref.manifest_artifact_id)
        return tuple(sorted(resolved))

    def _validate_model_artifacts(self, record: ModelLineageRecord) -> None:
        for ref in record.artifacts:
            try:
                self.artifacts.read_bytes(ref.artifact_id)
            except StorageError as exc:
                raise ModelLineageError(
                    f"produced model artifact not found: {ref.artifact_id}"
                ) from exc

    def _write_catalog_entry(self, path: Path, payload: Mapping[str, Any]) -> None:
        data = _canonical_json_bytes(payload)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != data:
                raise ModelLineageError(
                    "model_id/version is already cataloged to different immutable lineage"
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
                raise ModelLineageError("concurrent model-lineage catalog write was inconsistent")
        finally:
            if temporary.exists():
                temporary.unlink()

    def write(self, record: ModelLineageRecord) -> ModelLineageWriteResult:
        if not isinstance(record, ModelLineageRecord):
            raise ModelLineageError("record must be a ModelLineageRecord")
        record.validate()
        (
            _,
            source_evidence_artifact_ids,
            source_external_manifest_artifact_ids,
        ) = self._resolve_datasets(record)
        external_parent_model_manifest_artifact_ids = self._resolve_external_parent_models(
            record
        )
        self._validate_model_artifacts(record)

        payload = _canonical_json_bytes(record.to_dict())
        artifact = self.artifacts.put_bytes(payload)
        catalog_path = self._catalog_path(model_id=record.model_id, version=record.version)
        self._write_catalog_entry(
            catalog_path,
            {
                "catalog_schema_version": MODEL_LINEAGE_CATALOG_SCHEMA_VERSION,
                "model_id": record.model_id,
                "version": record.version,
                "producer_revision": record.producer_revision,
                "lineage_digest": record.lineage_digest,
                "artifact_id": artifact.artifact_id,
                "artifact_size_bytes": artifact.size_bytes,
                "dataset_manifest_artifact_ids": sorted(
                    item.manifest_artifact_id for item in record.datasets
                ),
                "source_evidence_artifact_ids": list(source_evidence_artifact_ids),
                "source_external_manifest_artifact_ids": list(
                    source_external_manifest_artifact_ids
                ),
                "external_parent_model_manifest_artifact_ids": list(
                    external_parent_model_manifest_artifact_ids
                ),
                "model_artifact_ids": sorted({item.artifact_id for item in record.artifacts}),
            },
        )
        return ModelLineageWriteResult(
            artifact=artifact,
            catalog_path=catalog_path,
            lineage_digest=record.lineage_digest,
            source_evidence_artifact_ids=source_evidence_artifact_ids,
            source_external_manifest_artifact_ids=source_external_manifest_artifact_ids,
            external_parent_model_manifest_artifact_ids=(
                external_parent_model_manifest_artifact_ids
            ),
        )

    def read_artifact(self, artifact_id: str) -> ModelLineageRecord:
        try:
            payload = self.artifacts.read_bytes(artifact_id)
        except StorageError as exc:
            raise ModelLineageError(f"model lineage artifact not found: {artifact_id}") from exc
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ModelLineageError("model lineage artifact is not UTF-8 JSON") from exc
        if not isinstance(value, Mapping):
            raise ModelLineageError("model lineage artifact must decode to an object")
        record = ModelLineageRecord.from_dict(value)
        expected = "sha256:" + hashlib.sha256(payload).hexdigest()
        if artifact_id != expected:
            raise ModelLineageError("model lineage artifact identity mismatch")
        return record

    def read(self, *, model_id: str, version: str) -> ModelLineageRecord:
        path = self._catalog_path(model_id=model_id, version=version)
        try:
            catalog = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ModelLineageError(f"model lineage not found: {model_id}:{version}") from exc
        except json.JSONDecodeError as exc:
            raise ModelLineageError("model lineage catalog entry is not valid JSON") from exc
        if not isinstance(catalog, Mapping):
            raise ModelLineageError("model lineage catalog entry must be an object")
        if catalog.get("catalog_schema_version") != MODEL_LINEAGE_CATALOG_SCHEMA_VERSION:
            raise ModelLineageError("unsupported model lineage catalog schema version")
        if catalog.get("model_id") != model_id or catalog.get("version") != version:
            raise ModelLineageError("model lineage catalog identity mismatch")
        artifact_id = _artifact_id(catalog.get("artifact_id"), "model lineage catalog artifact_id")
        record = self.read_artifact(artifact_id)
        if record.model_id != model_id or record.version != version:
            raise ModelLineageError("model lineage record identity does not match catalog")
        if record.lineage_digest != catalog.get("lineage_digest"):
            raise ModelLineageError("model lineage digest does not match catalog")
        return record

    def source_evidence_artifact_ids(self, record: ModelLineageRecord) -> tuple[str, ...]:
        """Resolve exact raw-evidence artifacts reachable through source datasets."""

        if not isinstance(record, ModelLineageRecord):
            raise ModelLineageError("record must be a ModelLineageRecord")
        record.validate()
        _, source_evidence, _ = self._resolve_datasets(record)
        return source_evidence

    def source_external_manifest_artifact_ids(
        self, record: ModelLineageRecord
    ) -> tuple[str, ...]:
        """Resolve imported external-source manifests reachable through datasets."""

        if not isinstance(record, ModelLineageRecord):
            raise ModelLineageError("record must be a ModelLineageRecord")
        record.validate()
        _, _, source_external = self._resolve_datasets(record)
        return source_external
