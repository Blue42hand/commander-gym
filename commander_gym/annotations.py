"""Append-only annotations over immutable Commander Gym raw evidence.

Annotations are derived judgments, not raw gameplay history.  They are stored as
independently versioned, content-addressed artifacts and joined back to one exact
raw-evidence artifact.  New judgments create new annotation revisions; neither the
raw evidence nor an existing annotation artifact is rewritten.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from .evidence import EvidenceError, validate_raw_evidence_envelope
from .storage import LocalArtifactStore, StorageError, StorageLayout, StoredBlob, parse_artifact_id

ANNOTATION_SCHEMA_VERSION = 1
ANNOTATION_CATALOG_SCHEMA_VERSION = 1
ANNOTATION_KIND = "commander-gym.annotation"
ANNOTATION_TARGET_RUN = "run"
ANNOTATION_TARGET_DECISION = "decision"
ANNOTATION_TARGET_KINDS = frozenset({ANNOTATION_TARGET_RUN, ANNOTATION_TARGET_DECISION})


class AnnotationError(EvidenceError):
    """Raised when an annotation cannot be joined or stored without ambiguity."""


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise AnnotationError(f"{label} must be a non-empty string")
    return value


@dataclass(frozen=True)
class AnnotationRecord:
    """One immutable, independently versioned judgment over raw evidence."""

    annotation_id: str
    revision: str
    created_at: str
    source_evidence_artifact_id: str
    target_kind: str
    target_id: str
    annotation_type: str
    annotator: Mapping[str, Any]
    payload: Mapping[str, Any]
    supersedes_artifact_id: str | None = None
    schema_version: int = ANNOTATION_SCHEMA_VERSION
    kind: str = ANNOTATION_KIND

    def validate(self) -> None:
        if self.schema_version != ANNOTATION_SCHEMA_VERSION:
            raise AnnotationError(
                f"unsupported annotation schema_version {self.schema_version!r}; "
                f"expected {ANNOTATION_SCHEMA_VERSION}"
            )
        if self.kind != ANNOTATION_KIND:
            raise AnnotationError(f"annotation kind must be {ANNOTATION_KIND!r}")
        _require_string(self.annotation_id, "annotation_id")
        _require_string(self.revision, "annotation revision")
        _require_string(self.created_at, "annotation created_at")
        try:
            parse_artifact_id(self.source_evidence_artifact_id)
        except StorageError as exc:
            raise AnnotationError("source_evidence_artifact_id must be a valid artifact ID") from exc
        if self.target_kind not in ANNOTATION_TARGET_KINDS:
            raise AnnotationError(
                f"annotation target_kind must be one of {sorted(ANNOTATION_TARGET_KINDS)!r}"
            )
        _require_string(self.target_id, "annotation target_id")
        _require_string(self.annotation_type, "annotation_type")
        if not isinstance(self.annotator, Mapping) or not self.annotator:
            raise AnnotationError("annotator must be a non-empty object")
        _require_string(self.annotator.get("source"), "annotator.source")
        if not isinstance(self.payload, Mapping):
            raise AnnotationError("annotation payload must be an object")
        if self.supersedes_artifact_id is not None:
            try:
                parse_artifact_id(self.supersedes_artifact_id)
            except StorageError as exc:
                raise AnnotationError("supersedes_artifact_id must be a valid artifact ID") from exc

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        result: dict[str, Any] = {
            "annotation_schema_version": self.schema_version,
            "kind": self.kind,
            "annotation_id": self.annotation_id,
            "revision": self.revision,
            "created_at": self.created_at,
            "source_evidence_artifact_id": self.source_evidence_artifact_id,
            "target": {
                "kind": self.target_kind,
                "id": self.target_id,
            },
            "annotation_type": self.annotation_type,
            "annotator": dict(self.annotator),
            "payload": dict(self.payload),
        }
        if self.supersedes_artifact_id is not None:
            result["supersedes_artifact_id"] = self.supersedes_artifact_id
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AnnotationRecord":
        if not isinstance(value, Mapping):
            raise AnnotationError("annotation record must be an object")
        target = value.get("target")
        if not isinstance(target, Mapping):
            raise AnnotationError("annotation target must be an object")
        annotator = value.get("annotator")
        payload = value.get("payload")
        record = cls(
            annotation_id=value.get("annotation_id"),
            revision=value.get("revision"),
            created_at=value.get("created_at"),
            source_evidence_artifact_id=value.get("source_evidence_artifact_id"),
            target_kind=target.get("kind"),
            target_id=target.get("id"),
            annotation_type=value.get("annotation_type"),
            annotator=annotator if isinstance(annotator, Mapping) else annotator,
            payload=payload if isinstance(payload, Mapping) else payload,
            supersedes_artifact_id=value.get("supersedes_artifact_id"),
            schema_version=value.get("annotation_schema_version"),
            kind=value.get("kind"),
        )
        record.validate()
        return record


@dataclass(frozen=True)
class AnnotationWriteResult:
    artifact: StoredBlob
    catalog_path: Path


class AnnotationStore:
    """Append-only annotation storage on the #74 content-addressed substrate."""

    def __init__(self, layout: StorageLayout):
        if not isinstance(layout, StorageLayout):
            raise AnnotationError("layout must be a StorageLayout")
        self.layout = layout
        self.artifacts = LocalArtifactStore(layout)

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _catalog_path(self, record: AnnotationRecord) -> Path:
        target_digest = self._digest(record.target_id)
        annotation_digest = self._digest(record.annotation_id)
        revision_digest = self._digest(record.revision)
        return (
            self.layout.path("annotations")
            / record.target_kind
            / target_digest[:2]
            / target_digest
            / annotation_digest
            / f"{revision_digest}.json"
        )

    def _catalog_payload(
        self,
        *,
        record: AnnotationRecord,
        artifact: StoredBlob,
    ) -> dict[str, Any]:
        return {
            "catalog_schema_version": ANNOTATION_CATALOG_SCHEMA_VERSION,
            "artifact_id": artifact.artifact_id,
            "artifact_size_bytes": artifact.size_bytes,
            "annotation_schema_version": record.schema_version,
            "annotation_id": record.annotation_id,
            "revision": record.revision,
            "source_evidence_artifact_id": record.source_evidence_artifact_id,
            "target": {"kind": record.target_kind, "id": record.target_id},
            "annotation_type": record.annotation_type,
            "supersedes_artifact_id": record.supersedes_artifact_id,
        }

    def _write_catalog_entry(self, path: Path, payload: Mapping[str, Any]) -> None:
        data = _canonical_json_bytes(payload)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != data:
                raise AnnotationError(
                    "annotation_id/revision is already cataloged to different immutable content"
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
                raise AnnotationError("concurrent annotation catalog write was inconsistent")
        finally:
            if temporary.exists():
                temporary.unlink()

    def _load_raw_evidence(self, artifact_id: str) -> Mapping[str, Any]:
        try:
            payload = self.artifacts.read_bytes(artifact_id)
        except StorageError as exc:
            raise AnnotationError(f"source raw evidence artifact not found: {artifact_id}") from exc
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AnnotationError("source evidence artifact is not UTF-8 JSON") from exc
        if not isinstance(value, Mapping):
            raise AnnotationError("source evidence artifact must decode to an object")
        try:
            validate_raw_evidence_envelope(value)
        except EvidenceError as exc:
            raise AnnotationError("source artifact is not valid Commander Gym raw evidence") from exc
        return value

    def _validate_target_join(
        self,
        record: AnnotationRecord,
        evidence: Mapping[str, Any],
    ) -> None:
        if record.target_kind == ANNOTATION_TARGET_RUN:
            run = evidence.get("run")
            if not isinstance(run, Mapping) or run.get("run_id") != record.target_id:
                raise AnnotationError(
                    f"annotation target run {record.target_id!r} is not present in source evidence"
                )
            return

        decisions = evidence.get("decisions")
        if not isinstance(decisions, list):
            raise AnnotationError("source evidence decisions must be an array")
        for decision in decisions:
            if not isinstance(decision, Mapping):
                continue
            provenance = decision.get("provenance")
            if isinstance(provenance, Mapping) and provenance.get("decision_id") == record.target_id:
                return
        raise AnnotationError(
            f"annotation target decision {record.target_id!r} is not present in source evidence"
        )

    def _validate_supersedes(self, record: AnnotationRecord) -> None:
        if record.supersedes_artifact_id is None:
            return
        if record.supersedes_artifact_id == record.source_evidence_artifact_id:
            raise AnnotationError("an annotation cannot supersede its source raw evidence")
        previous = self.read_artifact(record.supersedes_artifact_id)
        if previous.annotation_id != record.annotation_id:
            raise AnnotationError("superseded annotation must have the same annotation_id")
        if previous.revision == record.revision:
            raise AnnotationError("a new annotation revision cannot supersede the same revision")
        if previous.source_evidence_artifact_id != record.source_evidence_artifact_id:
            raise AnnotationError("superseded annotation must reference the same raw evidence")
        if previous.target_kind != record.target_kind or previous.target_id != record.target_id:
            raise AnnotationError("superseded annotation must have the same evidence target")

    def write(self, record: AnnotationRecord) -> AnnotationWriteResult:
        record.validate()
        evidence = self._load_raw_evidence(record.source_evidence_artifact_id)
        self._validate_target_join(record, evidence)
        self._validate_supersedes(record)

        payload = _canonical_json_bytes(record.to_dict())
        artifact = self.artifacts.put_bytes(payload)
        catalog_path = self._catalog_path(record)
        self._write_catalog_entry(
            catalog_path,
            self._catalog_payload(record=record, artifact=artifact),
        )
        return AnnotationWriteResult(artifact=artifact, catalog_path=catalog_path)

    def read_artifact(self, artifact_id: str) -> AnnotationRecord:
        try:
            payload = self.artifacts.read_bytes(artifact_id)
        except StorageError as exc:
            raise AnnotationError(f"annotation artifact not found: {artifact_id}") from exc
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AnnotationError("annotation artifact is not UTF-8 JSON") from exc
        if not isinstance(value, Mapping):
            raise AnnotationError("annotation artifact must decode to an object")
        record = AnnotationRecord.from_dict(value)
        expected = f"sha256:{hashlib.sha256(payload).hexdigest()}"
        if artifact_id != expected:
            raise AnnotationError("annotation artifact identity mismatch")
        return record

    def read(
        self,
        *,
        target_kind: str,
        target_id: str,
        annotation_id: str,
        revision: str,
    ) -> AnnotationRecord:
        probe = AnnotationRecord(
            annotation_id=annotation_id,
            revision=revision,
            created_at="catalog-probe",
            source_evidence_artifact_id="sha256:" + "0" * 64,
            target_kind=target_kind,
            target_id=target_id,
            annotation_type="catalog-probe",
            annotator={"source": "catalog-probe"},
            payload={},
        )
        path = self._catalog_path(probe)
        try:
            catalog = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise AnnotationError(
                f"annotation not found: {target_kind}:{target_id}:{annotation_id}:{revision}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise AnnotationError("annotation catalog entry is not valid JSON") from exc
        if not isinstance(catalog, Mapping):
            raise AnnotationError("annotation catalog entry must be an object")
        if catalog.get("catalog_schema_version") != ANNOTATION_CATALOG_SCHEMA_VERSION:
            raise AnnotationError("unsupported annotation catalog schema version")
        target = catalog.get("target")
        if not isinstance(target, Mapping):
            raise AnnotationError("annotation catalog target must be an object")
        if (
            catalog.get("annotation_id") != annotation_id
            or catalog.get("revision") != revision
            or target.get("kind") != target_kind
            or target.get("id") != target_id
        ):
            raise AnnotationError("annotation catalog identity mismatch")
        artifact_id = _require_string(catalog.get("artifact_id"), "annotation catalog artifact_id")
        record = self.read_artifact(artifact_id)
        if (
            record.annotation_id != annotation_id
            or record.revision != revision
            or record.target_kind != target_kind
            or record.target_id != target_id
        ):
            raise AnnotationError("annotation artifact identity does not match catalog")
        return record
