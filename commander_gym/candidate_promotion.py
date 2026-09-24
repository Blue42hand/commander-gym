"""Immutable candidate automation artifacts and fail-closed promotion gates.

This module owns the generic #73 contract between producing a candidate skill/policy
and making that exact component eligible for a future Pilot revision.  It deliberately
does not mutate :class:`commander_gym.identity.Pilot`, choose training algorithms, or
change the settled #53/#74 schemas.

Candidates and qualification records are immutable content-addressed artifacts stored
through the existing #74 substrate.  Promotion requires three independent gates:

* replay on an exact ``validation`` split from a #53 DatasetManifest;
* frozen held-out evaluation on an exact ``frozen_test`` split;
* shadow-mode evidence tied to immutable raw run evidence.

A failed gate remains durable diagnostic research evidence.  The only successful output
is an exact :class:`ArtifactRef` that a separate, explicit Pilot revision may consume;
live pilots never rewrite their own production automation through this API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from .dataset_manifest import DatasetManifestError, DatasetManifestStore
from .deck_package import ArtifactRef
from .evidence import EvidenceError, validate_raw_evidence_envelope
from .storage import LocalArtifactStore, StorageError, StorageLayout, StoredBlob, parse_artifact_id

CANDIDATE_ARTIFACT_SCHEMA_VERSION = 1
CANDIDATE_ARTIFACT_KIND = "commander-gym.pilot-candidate"
CANDIDATE_CATALOG_SCHEMA_VERSION = 1
CANDIDATE_TYPES = frozenset({"skill", "policy"})

QUALIFICATION_SCHEMA_VERSION = 1
QUALIFICATION_KIND = "commander-gym.pilot-candidate-qualification"
QUALIFICATION_CATALOG_SCHEMA_VERSION = 1
QUALIFICATION_STAGES = ("replay", "frozen_held_out", "shadow")


class CandidatePromotionError(EvidenceError):
    """Raised when candidate or qualification identity is ambiguous or unsafe."""


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CandidatePromotionError(
            f"candidate metadata must be canonically JSON serializable: {exc}"
        ) from exc


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise CandidatePromotionError(f"{label} must be a non-empty string")
    return value


def _artifact_id(value: Any, label: str) -> str:
    candidate = _require_string(value, label)
    try:
        parse_artifact_id(candidate)
    except StorageError as exc:
        raise CandidatePromotionError(f"{label} must be a valid artifact ID") from exc
    return candidate


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CandidatePromotionError(f"{label} must be an object")
    return value


@dataclass(frozen=True)
class CandidateArtifactRecord:
    """Versioned candidate skill/policy identity around exact executable bytes."""

    candidate_id: str
    version: str
    candidate_type: str
    component_kind: str
    created_at: str
    producer_revision: str
    payload_artifact_id: str
    source_artifact_ids: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = CANDIDATE_ARTIFACT_SCHEMA_VERSION
    kind: str = CANDIDATE_ARTIFACT_KIND

    def validate(self) -> None:
        if self.schema_version != CANDIDATE_ARTIFACT_SCHEMA_VERSION:
            raise CandidatePromotionError(
                f"unsupported candidate schema_version {self.schema_version!r}; "
                f"expected {CANDIDATE_ARTIFACT_SCHEMA_VERSION}"
            )
        if self.kind != CANDIDATE_ARTIFACT_KIND:
            raise CandidatePromotionError(
                f"candidate artifact kind must be {CANDIDATE_ARTIFACT_KIND!r}"
            )
        _require_string(self.candidate_id, "candidate_id")
        _require_string(self.version, "candidate version")
        if self.candidate_type not in CANDIDATE_TYPES:
            raise CandidatePromotionError(
                f"candidate_type must be one of {sorted(CANDIDATE_TYPES)!r}"
            )
        _require_string(self.component_kind, "component_kind")
        _require_string(self.created_at, "candidate created_at")
        _require_string(self.producer_revision, "candidate producer_revision")
        _artifact_id(self.payload_artifact_id, "candidate payload_artifact_id")
        if not isinstance(self.source_artifact_ids, tuple):
            raise CandidatePromotionError("source_artifact_ids must be a tuple")
        if len(self.source_artifact_ids) != len(set(self.source_artifact_ids)):
            raise CandidatePromotionError("candidate source artifact IDs must be unique")
        for artifact_id in self.source_artifact_ids:
            _artifact_id(artifact_id, "candidate source artifact ID")
        _require_mapping(self.metadata, "candidate metadata")
        _canonical_json_bytes(dict(self.metadata))

    def _digest_payload(self) -> dict[str, Any]:
        self.validate()
        return {
            "candidate_schema_version": self.schema_version,
            "kind": self.kind,
            "candidate_id": self.candidate_id,
            "version": self.version,
            "candidate_type": self.candidate_type,
            "component_kind": self.component_kind,
            "created_at": self.created_at,
            "producer_revision": self.producer_revision,
            "payload_artifact_id": self.payload_artifact_id,
            "source_artifact_ids": sorted(self.source_artifact_ids),
            "metadata": dict(self.metadata),
        }

    @property
    def candidate_digest(self) -> str:
        return "sha256:" + hashlib.sha256(_canonical_json_bytes(self._digest_payload())).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        result = self._digest_payload()
        result["candidate_digest"] = self.candidate_digest
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CandidateArtifactRecord":
        if not isinstance(value, Mapping):
            raise CandidatePromotionError("candidate artifact record must be an object")
        raw_sources = value.get("source_artifact_ids", [])
        if not isinstance(raw_sources, list):
            raise CandidatePromotionError("source_artifact_ids must be an array")
        record = cls(
            candidate_id=value.get("candidate_id"),
            version=value.get("version"),
            candidate_type=value.get("candidate_type"),
            component_kind=value.get("component_kind"),
            created_at=value.get("created_at"),
            producer_revision=value.get("producer_revision"),
            payload_artifact_id=value.get("payload_artifact_id"),
            source_artifact_ids=tuple(raw_sources),
            metadata=value.get("metadata", {}),
            schema_version=value.get("candidate_schema_version"),
            kind=value.get("kind"),
        )
        record.validate()
        expected = _artifact_id(value.get("candidate_digest"), "candidate_digest")
        if record.candidate_digest != expected:
            raise CandidatePromotionError(
                "candidate_digest does not match canonical candidate content"
            )
        return record

    def component_ref(self) -> ArtifactRef:
        """Return the exact component identity a future Pilot revision may consume."""

        self.validate()
        return ArtifactRef(
            kind=self.component_kind,
            artifact_id=self.candidate_id,
            version=self.version,
            digest=self.payload_artifact_id,
            metadata={"candidate_type": self.candidate_type},
        )


@dataclass(frozen=True)
class DatasetGateRef:
    """Exact #53 dataset split used by one replay or held-out gate."""

    manifest_artifact_id: str
    dataset_id: str
    version: str
    dataset_digest: str
    split_name: str

    def validate(self) -> None:
        _artifact_id(self.manifest_artifact_id, "dataset manifest artifact ID")
        _require_string(self.dataset_id, "dataset_id")
        _require_string(self.version, "dataset version")
        _artifact_id(self.dataset_digest, "dataset_digest")
        _require_string(self.split_name, "dataset split_name")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "manifest_artifact_id": self.manifest_artifact_id,
            "dataset_id": self.dataset_id,
            "version": self.version,
            "dataset_digest": self.dataset_digest,
            "split_name": self.split_name,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DatasetGateRef":
        if not isinstance(value, Mapping):
            raise CandidatePromotionError("dataset gate reference must be an object")
        result = cls(
            manifest_artifact_id=value.get("manifest_artifact_id"),
            dataset_id=value.get("dataset_id"),
            version=value.get("version"),
            dataset_digest=value.get("dataset_digest"),
            split_name=value.get("split_name"),
        )
        result.validate()
        return result


@dataclass(frozen=True)
class QualificationStage:
    """One immutable replay, frozen-held-out, or shadow evaluation result."""

    stage: str
    passed: bool
    evaluated_at: str
    evaluator_revision: str
    result_artifact_id: str
    metrics: Mapping[str, Any] = field(default_factory=dict)
    dataset: DatasetGateRef | None = None
    source_evidence_artifact_ids: tuple[str, ...] = ()
    failure_reason: str | None = None

    def validate(self) -> None:
        if self.stage not in QUALIFICATION_STAGES:
            raise CandidatePromotionError(
                f"qualification stage must be one of {QUALIFICATION_STAGES!r}"
            )
        if type(self.passed) is not bool:
            raise CandidatePromotionError("qualification passed must be a boolean")
        _require_string(self.evaluated_at, "qualification evaluated_at")
        _require_string(self.evaluator_revision, "qualification evaluator_revision")
        _artifact_id(self.result_artifact_id, "qualification result_artifact_id")
        _require_mapping(self.metrics, "qualification metrics")
        _canonical_json_bytes(dict(self.metrics))
        if not isinstance(self.source_evidence_artifact_ids, tuple):
            raise CandidatePromotionError(
                "qualification source_evidence_artifact_ids must be a tuple"
            )
        if len(self.source_evidence_artifact_ids) != len(
            set(self.source_evidence_artifact_ids)
        ):
            raise CandidatePromotionError(
                "qualification source evidence artifact IDs must be unique"
            )
        for artifact_id in self.source_evidence_artifact_ids:
            _artifact_id(artifact_id, "qualification source evidence artifact ID")

        if self.stage in {"replay", "frozen_held_out"}:
            if not isinstance(self.dataset, DatasetGateRef):
                raise CandidatePromotionError(
                    f"{self.stage} qualification requires an exact DatasetGateRef"
                )
            self.dataset.validate()
        elif self.dataset is not None:
            raise CandidatePromotionError("shadow qualification must not claim a dataset split")

        if self.stage == "shadow" and not self.source_evidence_artifact_ids:
            raise CandidatePromotionError(
                "shadow qualification requires immutable source run evidence"
            )

        if self.passed:
            if self.failure_reason not in (None, ""):
                raise CandidatePromotionError(
                    "passed qualification must not also carry a failure_reason"
                )
        else:
            _require_string(self.failure_reason, "failed qualification failure_reason")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        result: dict[str, Any] = {
            "stage": self.stage,
            "passed": self.passed,
            "evaluated_at": self.evaluated_at,
            "evaluator_revision": self.evaluator_revision,
            "result_artifact_id": self.result_artifact_id,
            "metrics": dict(self.metrics),
            "source_evidence_artifact_ids": sorted(self.source_evidence_artifact_ids),
        }
        if self.dataset is not None:
            result["dataset"] = self.dataset.to_dict()
        if self.failure_reason is not None:
            result["failure_reason"] = self.failure_reason
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "QualificationStage":
        if not isinstance(value, Mapping):
            raise CandidatePromotionError("qualification stage must be an object")
        raw_sources = value.get("source_evidence_artifact_ids", [])
        if not isinstance(raw_sources, list):
            raise CandidatePromotionError(
                "qualification source_evidence_artifact_ids must be an array"
            )
        raw_dataset = value.get("dataset")
        result = cls(
            stage=value.get("stage"),
            passed=value.get("passed"),
            evaluated_at=value.get("evaluated_at"),
            evaluator_revision=value.get("evaluator_revision"),
            result_artifact_id=value.get("result_artifact_id"),
            metrics=value.get("metrics", {}),
            dataset=(DatasetGateRef.from_dict(raw_dataset) if raw_dataset is not None else None),
            source_evidence_artifact_ids=tuple(raw_sources),
            failure_reason=value.get("failure_reason"),
        )
        result.validate()
        return result


@dataclass(frozen=True)
class CandidateQualificationRecord:
    """Append-only exact gate result for one immutable candidate revision."""

    qualification_id: str
    revision: str
    candidate_record_artifact_id: str
    candidate_id: str
    candidate_version: str
    candidate_digest: str
    created_at: str
    gate_revision: str
    stages: tuple[QualificationStage, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = QUALIFICATION_SCHEMA_VERSION
    kind: str = QUALIFICATION_KIND

    def validate(self) -> None:
        if self.schema_version != QUALIFICATION_SCHEMA_VERSION:
            raise CandidatePromotionError(
                f"unsupported qualification schema_version {self.schema_version!r}; "
                f"expected {QUALIFICATION_SCHEMA_VERSION}"
            )
        if self.kind != QUALIFICATION_KIND:
            raise CandidatePromotionError(
                f"qualification kind must be {QUALIFICATION_KIND!r}"
            )
        _require_string(self.qualification_id, "qualification_id")
        _require_string(self.revision, "qualification revision")
        _artifact_id(
            self.candidate_record_artifact_id,
            "qualification candidate_record_artifact_id",
        )
        _require_string(self.candidate_id, "qualification candidate_id")
        _require_string(self.candidate_version, "qualification candidate_version")
        _artifact_id(self.candidate_digest, "qualification candidate_digest")
        _require_string(self.created_at, "qualification created_at")
        _require_string(self.gate_revision, "qualification gate_revision")
        if not isinstance(self.stages, tuple):
            raise CandidatePromotionError("qualification stages must be a tuple")
        if tuple(stage.stage for stage in self.stages) != QUALIFICATION_STAGES:
            raise CandidatePromotionError(
                "qualification stages must appear exactly as replay, frozen_held_out, shadow"
            )
        for stage in self.stages:
            if not isinstance(stage, QualificationStage):
                raise CandidatePromotionError(
                    "qualification stages must contain QualificationStage values"
                )
            stage.validate()
        _require_mapping(self.metadata, "qualification metadata")
        _canonical_json_bytes(dict(self.metadata))

    @property
    def eligible_for_promotion(self) -> bool:
        self.validate()
        return all(stage.passed for stage in self.stages)

    def _digest_payload(self) -> dict[str, Any]:
        self.validate()
        return {
            "qualification_schema_version": self.schema_version,
            "kind": self.kind,
            "qualification_id": self.qualification_id,
            "revision": self.revision,
            "candidate_record_artifact_id": self.candidate_record_artifact_id,
            "candidate_id": self.candidate_id,
            "candidate_version": self.candidate_version,
            "candidate_digest": self.candidate_digest,
            "created_at": self.created_at,
            "gate_revision": self.gate_revision,
            "stages": [stage.to_dict() for stage in self.stages],
            "metadata": dict(self.metadata),
            "eligible_for_promotion": self.eligible_for_promotion,
        }

    @property
    def qualification_digest(self) -> str:
        return "sha256:" + hashlib.sha256(_canonical_json_bytes(self._digest_payload())).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        result = self._digest_payload()
        result["qualification_digest"] = self.qualification_digest
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CandidateQualificationRecord":
        if not isinstance(value, Mapping):
            raise CandidatePromotionError("candidate qualification must be an object")
        raw_stages = value.get("stages")
        if not isinstance(raw_stages, list):
            raise CandidatePromotionError("qualification stages must be an array")
        record = cls(
            qualification_id=value.get("qualification_id"),
            revision=value.get("revision"),
            candidate_record_artifact_id=value.get("candidate_record_artifact_id"),
            candidate_id=value.get("candidate_id"),
            candidate_version=value.get("candidate_version"),
            candidate_digest=value.get("candidate_digest"),
            created_at=value.get("created_at"),
            gate_revision=value.get("gate_revision"),
            stages=tuple(QualificationStage.from_dict(item) for item in raw_stages),
            metadata=value.get("metadata", {}),
            schema_version=value.get("qualification_schema_version"),
            kind=value.get("kind"),
        )
        record.validate()
        if value.get("eligible_for_promotion") is not record.eligible_for_promotion:
            raise CandidatePromotionError(
                "eligible_for_promotion does not match immutable stage results"
            )
        expected = _artifact_id(value.get("qualification_digest"), "qualification_digest")
        if record.qualification_digest != expected:
            raise CandidatePromotionError(
                "qualification_digest does not match canonical qualification content"
            )
        return record


@dataclass(frozen=True)
class CandidateWriteResult:
    record_artifact: StoredBlob
    catalog_path: Path
    candidate_digest: str


@dataclass(frozen=True)
class QualificationWriteResult:
    artifact: StoredBlob
    catalog_path: Path
    qualification_digest: str
    eligible_for_promotion: bool


class CandidatePromotionStore:
    """Persist candidates/qualification through #74 without mutating production Pilots."""

    def __init__(self, layout: StorageLayout):
        if not isinstance(layout, StorageLayout):
            raise CandidatePromotionError("layout must be a StorageLayout")
        self.layout = layout
        self.artifacts = LocalArtifactStore(layout)
        self.datasets = DatasetManifestStore(layout)

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _candidate_catalog_path(self, candidate_id: str, version: str) -> Path:
        identity = self._digest(_require_string(candidate_id, "candidate_id"))
        version_digest = self._digest(_require_string(version, "candidate version"))
        return (
            self.layout.path("catalog")
            / "pilot-candidates"
            / identity[:2]
            / identity
            / f"{version_digest}.json"
        )

    def _qualification_catalog_path(self, qualification_id: str, revision: str) -> Path:
        identity = self._digest(_require_string(qualification_id, "qualification_id"))
        revision_digest = self._digest(
            _require_string(revision, "qualification revision")
        )
        return (
            self.layout.path("catalog")
            / "pilot-qualifications"
            / identity[:2]
            / identity
            / f"{revision_digest}.json"
        )

    def _write_catalog_entry(self, path: Path, payload: Mapping[str, Any]) -> None:
        data = _canonical_json_bytes(payload)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != data:
                raise CandidatePromotionError(
                    "catalog identity is already bound to different immutable content"
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
                raise CandidatePromotionError("concurrent catalog write was inconsistent")
        finally:
            if temporary.exists():
                temporary.unlink()

    def _require_artifact(self, artifact_id: str, label: str) -> bytes:
        _artifact_id(artifact_id, label)
        try:
            return self.artifacts.read_bytes(artifact_id)
        except StorageError as exc:
            raise CandidatePromotionError(f"{label} not found: {artifact_id}") from exc

    def write_candidate(self, record: CandidateArtifactRecord) -> CandidateWriteResult:
        if not isinstance(record, CandidateArtifactRecord):
            raise CandidatePromotionError("record must be a CandidateArtifactRecord")
        record.validate()
        self._require_artifact(record.payload_artifact_id, "candidate payload artifact")
        for artifact_id in record.source_artifact_ids:
            self._require_artifact(artifact_id, "candidate source artifact")

        payload = _canonical_json_bytes(record.to_dict())
        artifact = self.artifacts.put_bytes(payload)
        catalog_path = self._candidate_catalog_path(record.candidate_id, record.version)
        self._write_catalog_entry(
            catalog_path,
            {
                "catalog_schema_version": CANDIDATE_CATALOG_SCHEMA_VERSION,
                "candidate_id": record.candidate_id,
                "version": record.version,
                "candidate_type": record.candidate_type,
                "component_kind": record.component_kind,
                "candidate_digest": record.candidate_digest,
                "record_artifact_id": artifact.artifact_id,
                "payload_artifact_id": record.payload_artifact_id,
            },
        )
        return CandidateWriteResult(
            record_artifact=artifact,
            catalog_path=catalog_path,
            candidate_digest=record.candidate_digest,
        )

    def read_candidate_artifact(self, artifact_id: str) -> CandidateArtifactRecord:
        payload = self._require_artifact(artifact_id, "candidate record artifact")
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CandidatePromotionError("candidate record artifact is not UTF-8 JSON") from exc
        if not isinstance(value, Mapping):
            raise CandidatePromotionError("candidate record artifact must decode to an object")
        record = CandidateArtifactRecord.from_dict(value)
        expected = "sha256:" + hashlib.sha256(payload).hexdigest()
        if artifact_id != expected:
            raise CandidatePromotionError("candidate record artifact identity mismatch")
        self._require_artifact(record.payload_artifact_id, "candidate payload artifact")
        return record

    def read_candidate(self, *, candidate_id: str, version: str) -> CandidateArtifactRecord:
        path = self._candidate_catalog_path(candidate_id, version)
        try:
            catalog = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise CandidatePromotionError(
                f"candidate not found: {candidate_id}:{version}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise CandidatePromotionError("candidate catalog entry is not valid JSON") from exc
        if not isinstance(catalog, Mapping):
            raise CandidatePromotionError("candidate catalog entry must be an object")
        if catalog.get("catalog_schema_version") != CANDIDATE_CATALOG_SCHEMA_VERSION:
            raise CandidatePromotionError("unsupported candidate catalog schema version")
        if catalog.get("candidate_id") != candidate_id or catalog.get("version") != version:
            raise CandidatePromotionError("candidate catalog identity mismatch")
        record_artifact_id = _artifact_id(
            catalog.get("record_artifact_id"), "candidate catalog record_artifact_id"
        )
        record = self.read_candidate_artifact(record_artifact_id)
        if record.candidate_id != candidate_id or record.version != version:
            raise CandidatePromotionError("candidate record identity does not match catalog")
        if record.candidate_digest != catalog.get("candidate_digest"):
            raise CandidatePromotionError("candidate digest does not match catalog")
        return record

    def _validate_dataset_gate(self, stage: QualificationStage) -> None:
        assert stage.dataset is not None
        ref = stage.dataset
        try:
            manifest = self.datasets.read_artifact(ref.manifest_artifact_id)
        except DatasetManifestError as exc:
            raise CandidatePromotionError(
                f"qualification dataset manifest is invalid: {ref.manifest_artifact_id}"
            ) from exc
        if manifest.dataset_id != ref.dataset_id or manifest.version != ref.version:
            raise CandidatePromotionError(
                "qualification dataset identity does not match exact manifest"
            )
        if manifest.dataset_digest != ref.dataset_digest:
            raise CandidatePromotionError(
                "qualification dataset digest does not match exact manifest"
            )
        try:
            split = manifest.split(ref.split_name)
        except DatasetManifestError as exc:
            raise CandidatePromotionError(
                f"qualification dataset split not found: {ref.split_name}"
            ) from exc
        expected_role = "validation" if stage.stage == "replay" else "frozen_test"
        if split.role != expected_role:
            raise CandidatePromotionError(
                f"{stage.stage} qualification requires split role {expected_role!r}; "
                f"got {split.role!r}"
            )

    def _validate_shadow_evidence(self, stage: QualificationStage) -> None:
        for artifact_id in stage.source_evidence_artifact_ids:
            payload = self._require_artifact(artifact_id, "shadow source evidence artifact")
            try:
                value = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CandidatePromotionError(
                    "shadow source evidence artifact is not UTF-8 JSON"
                ) from exc
            if not isinstance(value, Mapping):
                raise CandidatePromotionError(
                    "shadow source evidence artifact must decode to an object"
                )
            try:
                validate_raw_evidence_envelope(value)
            except EvidenceError as exc:
                raise CandidatePromotionError(
                    "shadow qualification must reference valid immutable raw evidence"
                ) from exc

    def _resolve_candidate_for_qualification(
        self, record: CandidateQualificationRecord
    ) -> CandidateArtifactRecord:
        candidate = self.read_candidate_artifact(record.candidate_record_artifact_id)
        if (
            candidate.candidate_id != record.candidate_id
            or candidate.version != record.candidate_version
            or candidate.candidate_digest != record.candidate_digest
        ):
            raise CandidatePromotionError(
                "qualification candidate identity does not match exact candidate artifact"
            )
        cataloged = self.read_candidate(
            candidate_id=candidate.candidate_id,
            version=candidate.version,
        )
        if cataloged.candidate_digest != candidate.candidate_digest:
            raise CandidatePromotionError(
                "qualification candidate does not match cataloged immutable revision"
            )
        return candidate

    def _validate_qualification_sources(self, record: CandidateQualificationRecord) -> None:
        self._resolve_candidate_for_qualification(record)
        for stage in record.stages:
            self._require_artifact(stage.result_artifact_id, "qualification result artifact")
            if stage.stage in {"replay", "frozen_held_out"}:
                self._validate_dataset_gate(stage)
            elif stage.stage == "shadow":
                self._validate_shadow_evidence(stage)

    def write_qualification(
        self, record: CandidateQualificationRecord
    ) -> QualificationWriteResult:
        if not isinstance(record, CandidateQualificationRecord):
            raise CandidatePromotionError(
                "record must be a CandidateQualificationRecord"
            )
        record.validate()
        self._validate_qualification_sources(record)

        payload = _canonical_json_bytes(record.to_dict())
        artifact = self.artifacts.put_bytes(payload)
        catalog_path = self._qualification_catalog_path(
            record.qualification_id, record.revision
        )
        self._write_catalog_entry(
            catalog_path,
            {
                "catalog_schema_version": QUALIFICATION_CATALOG_SCHEMA_VERSION,
                "qualification_id": record.qualification_id,
                "revision": record.revision,
                "candidate_record_artifact_id": record.candidate_record_artifact_id,
                "candidate_id": record.candidate_id,
                "candidate_version": record.candidate_version,
                "candidate_digest": record.candidate_digest,
                "qualification_digest": record.qualification_digest,
                "artifact_id": artifact.artifact_id,
                "eligible_for_promotion": record.eligible_for_promotion,
            },
        )
        return QualificationWriteResult(
            artifact=artifact,
            catalog_path=catalog_path,
            qualification_digest=record.qualification_digest,
            eligible_for_promotion=record.eligible_for_promotion,
        )

    def read_qualification_artifact(
        self, artifact_id: str
    ) -> CandidateQualificationRecord:
        payload = self._require_artifact(artifact_id, "qualification artifact")
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CandidatePromotionError(
                "qualification artifact is not UTF-8 JSON"
            ) from exc
        if not isinstance(value, Mapping):
            raise CandidatePromotionError("qualification artifact must decode to an object")
        record = CandidateQualificationRecord.from_dict(value)
        expected = "sha256:" + hashlib.sha256(payload).hexdigest()
        if artifact_id != expected:
            raise CandidatePromotionError("qualification artifact identity mismatch")
        self._validate_qualification_sources(record)
        return record

    def qualified_component_ref(
        self, record: CandidateQualificationRecord
    ) -> ArtifactRef:
        """Return exact component identity only after all immutable gates pass.

        This intentionally does not edit a Pilot manifest, registry, Binding, or live
        process.  Promotion into production remains a separate explicit Pilot revision.
        """

        if not isinstance(record, CandidateQualificationRecord):
            raise CandidatePromotionError(
                "record must be a CandidateQualificationRecord"
            )
        record.validate()
        self._validate_qualification_sources(record)
        if not record.eligible_for_promotion:
            failed = [stage.stage for stage in record.stages if not stage.passed]
            raise CandidatePromotionError(
                "candidate is not eligible for promotion; failed gate(s): "
                + ", ".join(failed)
            )
        candidate = self._resolve_candidate_for_qualification(record)
        return candidate.component_ref()
