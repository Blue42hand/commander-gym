"""Deterministic dataset manifests over immutable Commander Gym evidence.

A dataset is identified by a versioned manifest that selects exact decisions from
content-addressed raw-evidence artifacts, records the annotation revisions and
transforms required to derive training/evaluation rows, and freezes split membership.
Physical storage placement is delegated to :mod:`commander_gym.storage`.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from .annotations import ANNOTATION_TARGET_DECISION, AnnotationError, AnnotationStore
from .evidence import EvidenceError, validate_raw_evidence_envelope
from .external_provenance import ExternalProvenanceError, ExternalProvenanceStore
from .storage import LocalArtifactStore, StorageError, StorageLayout, StoredBlob, parse_artifact_id

DATASET_MANIFEST_SCHEMA_VERSION = 1
DATASET_CATALOG_SCHEMA_VERSION = 1
DATASET_MANIFEST_KIND = "commander-gym.dataset-manifest"
DATASET_SPLIT_ROLES = frozenset({"train", "validation", "frozen_test", "diagnostic"})


class DatasetManifestError(EvidenceError):
    """Raised when a dataset manifest cannot be validated or stored safely."""


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise DatasetManifestError(f"{label} must be a non-empty string")
    return value


def _artifact_id(value: Any, label: str) -> str:
    candidate = _require_string(value, label)
    try:
        parse_artifact_id(candidate)
    except StorageError as exc:
        raise DatasetManifestError(f"{label} must be a valid artifact ID") from exc
    return candidate


@dataclass(frozen=True)
class DatasetEvidenceSelection:
    """Exact decision membership selected from one immutable raw-evidence artifact."""

    evidence_artifact_id: str
    decision_ids: tuple[str, ...]

    def validate(self) -> None:
        _artifact_id(self.evidence_artifact_id, "evidence_artifact_id")
        if not isinstance(self.decision_ids, tuple) or not self.decision_ids:
            raise DatasetManifestError("dataset evidence selection requires decision_ids")
        if any(not isinstance(item, str) or not item for item in self.decision_ids):
            raise DatasetManifestError("dataset selection decision_ids must be non-empty strings")
        if len(self.decision_ids) != len(set(self.decision_ids)):
            raise DatasetManifestError("dataset selection decision_ids must be unique")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "evidence_artifact_id": self.evidence_artifact_id,
            "decision_ids": sorted(self.decision_ids),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DatasetEvidenceSelection":
        if not isinstance(value, Mapping):
            raise DatasetManifestError("dataset evidence selection must be an object")
        raw_ids = value.get("decision_ids")
        if not isinstance(raw_ids, list):
            raise DatasetManifestError("dataset selection decision_ids must be an array")
        selection = cls(
            evidence_artifact_id=value.get("evidence_artifact_id"),
            decision_ids=tuple(raw_ids),
        )
        selection.validate()
        return selection


@dataclass(frozen=True)
class ExternalDatasetSelection:
    """Exact record membership selected from one immutable external-source manifest."""

    source_manifest_artifact_id: str
    record_ids: tuple[str, ...]

    def validate(self) -> None:
        _artifact_id(self.source_manifest_artifact_id, "external source manifest artifact ID")
        if not isinstance(self.record_ids, tuple) or not self.record_ids:
            raise DatasetManifestError("external dataset selection requires record_ids")
        if any(not isinstance(item, str) or not item for item in self.record_ids):
            raise DatasetManifestError(
                "external dataset selection record_ids must be non-empty strings"
            )
        if len(self.record_ids) != len(set(self.record_ids)):
            raise DatasetManifestError("external dataset selection record_ids must be unique")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "source_manifest_artifact_id": self.source_manifest_artifact_id,
            "record_ids": sorted(self.record_ids),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExternalDatasetSelection":
        if not isinstance(value, Mapping):
            raise DatasetManifestError("external dataset selection must be an object")
        raw_ids = value.get("record_ids")
        if not isinstance(raw_ids, list):
            raise DatasetManifestError("external dataset selection record_ids must be an array")
        selection = cls(
            source_manifest_artifact_id=value.get("source_manifest_artifact_id"),
            record_ids=tuple(raw_ids),
        )
        selection.validate()
        return selection


@dataclass(frozen=True)
class DatasetSplit:
    """Frozen membership and qualification policy for one dataset split."""

    name: str
    role: str
    selections: tuple[DatasetEvidenceSelection, ...]
    external_selections: tuple[ExternalDatasetSelection, ...] = ()
    allow_diagnostic_evidence: bool = False
    diagnostic_justification: str | None = None

    def validate(self) -> None:
        _require_string(self.name, "dataset split name")
        if self.role not in DATASET_SPLIT_ROLES:
            raise DatasetManifestError(
                f"dataset split role must be one of {sorted(DATASET_SPLIT_ROLES)!r}"
            )
        if not isinstance(self.selections, tuple):
            raise DatasetManifestError("dataset split selections must be a tuple")
        if not isinstance(self.external_selections, tuple):
            raise DatasetManifestError("dataset split external_selections must be a tuple")
        if not self.selections and not self.external_selections:
            raise DatasetManifestError(
                "dataset split requires native evidence or external source selections"
            )
        seen_sources: set[str] = set()
        seen_decisions: set[str] = set()
        for selection in self.selections:
            if not isinstance(selection, DatasetEvidenceSelection):
                raise DatasetManifestError("dataset split selections must be DatasetEvidenceSelection")
            selection.validate()
            if selection.evidence_artifact_id in seen_sources:
                raise DatasetManifestError(
                    "each raw-evidence artifact may appear only once per dataset split"
                )
            overlap = seen_decisions & set(selection.decision_ids)
            if overlap:
                raise DatasetManifestError(
                    "dataset split contains duplicate decision membership: "
                    + ", ".join(sorted(overlap))
                )
            seen_sources.add(selection.evidence_artifact_id)
            seen_decisions.update(selection.decision_ids)

        seen_external_sources: set[str] = set()
        for selection in self.external_selections:
            if not isinstance(selection, ExternalDatasetSelection):
                raise DatasetManifestError(
                    "dataset split external_selections must be ExternalDatasetSelection"
                )
            selection.validate()
            if selection.source_manifest_artifact_id in seen_external_sources:
                raise DatasetManifestError(
                    "each external source manifest may appear only once per dataset split"
                )
            seen_external_sources.add(selection.source_manifest_artifact_id)
        if type(self.allow_diagnostic_evidence) is not bool:
            raise DatasetManifestError("allow_diagnostic_evidence must be a boolean")
        if self.allow_diagnostic_evidence and self.role != "diagnostic":
            _require_string(
                self.diagnostic_justification,
                "diagnostic_justification when diagnostic evidence is explicitly selected",
            )
        elif self.diagnostic_justification is not None:
            _require_string(self.diagnostic_justification, "diagnostic_justification")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        result: dict[str, Any] = {
            "name": self.name,
            "role": self.role,
            "allow_diagnostic_evidence": self.allow_diagnostic_evidence,
            "selections": [
                item.to_dict()
                for item in sorted(
                    self.selections,
                    key=lambda item: (item.evidence_artifact_id, tuple(sorted(item.decision_ids))),
                )
            ],
        }
        if self.external_selections:
            result["external_selections"] = [
                item.to_dict()
                for item in sorted(
                    self.external_selections,
                    key=lambda item: (
                        item.source_manifest_artifact_id,
                        tuple(sorted(item.record_ids)),
                    ),
                )
            ]
        if self.diagnostic_justification is not None:
            result["diagnostic_justification"] = self.diagnostic_justification
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DatasetSplit":
        if not isinstance(value, Mapping):
            raise DatasetManifestError("dataset split must be an object")
        selections = value.get("selections", [])
        external_selections = value.get("external_selections", [])
        if not isinstance(selections, list):
            raise DatasetManifestError("dataset split selections must be an array")
        if not isinstance(external_selections, list):
            raise DatasetManifestError("dataset split external_selections must be an array")
        split = cls(
            name=value.get("name"),
            role=value.get("role"),
            selections=tuple(DatasetEvidenceSelection.from_dict(item) for item in selections),
            external_selections=tuple(
                ExternalDatasetSelection.from_dict(item) for item in external_selections
            ),
            allow_diagnostic_evidence=value.get("allow_diagnostic_evidence", False),
            diagnostic_justification=value.get("diagnostic_justification"),
        )
        split.validate()
        return split

    @property
    def decision_ids(self) -> frozenset[str]:
        return frozenset(
            decision_id
            for selection in self.selections
            for decision_id in selection.decision_ids
        )

    @property
    def external_record_keys(self) -> frozenset[tuple[str, str]]:
        return frozenset(
            (selection.source_manifest_artifact_id, record_id)
            for selection in self.external_selections
            for record_id in selection.record_ids
        )


@dataclass(frozen=True)
class DatasetManifest:
    """Versioned logical identity for one reproducible dataset selection."""

    dataset_id: str
    version: str
    purpose: str
    created_at: str
    source_population: str
    source_query: Mapping[str, Any]
    selection_rules: Mapping[str, Any]
    exclusion_rules: Mapping[str, Any]
    required_annotation_artifact_ids: tuple[str, ...]
    transformations: tuple[Mapping[str, Any], ...]
    splits: tuple[DatasetSplit, ...]
    generator_revision: str
    schema_version: int = DATASET_MANIFEST_SCHEMA_VERSION
    kind: str = DATASET_MANIFEST_KIND

    def validate(self) -> None:
        if self.schema_version != DATASET_MANIFEST_SCHEMA_VERSION:
            raise DatasetManifestError(
                f"unsupported dataset manifest schema_version {self.schema_version!r}; "
                f"expected {DATASET_MANIFEST_SCHEMA_VERSION}"
            )
        if self.kind != DATASET_MANIFEST_KIND:
            raise DatasetManifestError(f"dataset manifest kind must be {DATASET_MANIFEST_KIND!r}")
        _require_string(self.dataset_id, "dataset_id")
        _require_string(self.version, "dataset version")
        _require_string(self.purpose, "dataset purpose")
        _require_string(self.created_at, "dataset created_at")
        _require_string(self.source_population, "dataset source_population")
        _require_string(self.generator_revision, "dataset generator_revision")
        for label, value in (
            ("source_query", self.source_query),
            ("selection_rules", self.selection_rules),
            ("exclusion_rules", self.exclusion_rules),
        ):
            if not isinstance(value, Mapping):
                raise DatasetManifestError(f"dataset {label} must be an object")
        if not isinstance(self.required_annotation_artifact_ids, tuple):
            raise DatasetManifestError("required_annotation_artifact_ids must be a tuple")
        if len(self.required_annotation_artifact_ids) != len(
            set(self.required_annotation_artifact_ids)
        ):
            raise DatasetManifestError("required annotation artifact IDs must be unique")
        for artifact_id in self.required_annotation_artifact_ids:
            _artifact_id(artifact_id, "required annotation artifact ID")
        if not isinstance(self.transformations, tuple):
            raise DatasetManifestError("dataset transformations must be a tuple")
        if any(not isinstance(item, Mapping) for item in self.transformations):
            raise DatasetManifestError("each dataset transformation must be an object")
        if not isinstance(self.splits, tuple) or not self.splits:
            raise DatasetManifestError("dataset manifest requires at least one split")

        names: list[str] = []
        seen_decisions: dict[str, str] = {}
        seen_external_records: dict[tuple[str, str], str] = {}
        for split in self.splits:
            if not isinstance(split, DatasetSplit):
                raise DatasetManifestError("dataset splits must be DatasetSplit values")
            split.validate()
            names.append(split.name)
            for decision_id in split.decision_ids:
                previous = seen_decisions.get(decision_id)
                if previous is not None:
                    raise DatasetManifestError(
                        f"decision {decision_id!r} appears in both {previous!r} and {split.name!r}"
                    )
                seen_decisions[decision_id] = split.name
            for record_key in split.external_record_keys:
                previous = seen_external_records.get(record_key)
                if previous is not None:
                    raise DatasetManifestError(
                        "external record "
                        f"{record_key[1]!r} appears in both {previous!r} and {split.name!r}"
                    )
                seen_external_records[record_key] = split.name
        if len(names) != len(set(names)):
            raise DatasetManifestError("dataset split names must be unique")

    def _digest_payload(self) -> dict[str, Any]:
        self.validate()
        return {
            "dataset_manifest_schema_version": self.schema_version,
            "kind": self.kind,
            "dataset_id": self.dataset_id,
            "version": self.version,
            "purpose": self.purpose,
            "created_at": self.created_at,
            "source_population": self.source_population,
            "source_query": dict(self.source_query),
            "selection_rules": dict(self.selection_rules),
            "exclusion_rules": dict(self.exclusion_rules),
            "required_annotation_artifact_ids": sorted(self.required_annotation_artifact_ids),
            "transformations": [dict(item) for item in self.transformations],
            "splits": [
                split.to_dict() for split in sorted(self.splits, key=lambda item: item.name)
            ],
            "generator_revision": self.generator_revision,
        }

    @property
    def dataset_digest(self) -> str:
        return "sha256:" + hashlib.sha256(_canonical_json_bytes(self._digest_payload())).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        result = self._digest_payload()
        result["dataset_digest"] = self.dataset_digest
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DatasetManifest":
        if not isinstance(value, Mapping):
            raise DatasetManifestError("dataset manifest must be an object")
        raw_annotations = value.get("required_annotation_artifact_ids", [])
        raw_transformations = value.get("transformations", [])
        raw_splits = value.get("splits")
        if not isinstance(raw_annotations, list):
            raise DatasetManifestError("required_annotation_artifact_ids must be an array")
        if not isinstance(raw_transformations, list):
            raise DatasetManifestError("dataset transformations must be an array")
        if not isinstance(raw_splits, list):
            raise DatasetManifestError("dataset splits must be an array")
        manifest = cls(
            dataset_id=value.get("dataset_id"),
            version=value.get("version"),
            purpose=value.get("purpose"),
            created_at=value.get("created_at"),
            source_population=value.get("source_population"),
            source_query=value.get("source_query"),
            selection_rules=value.get("selection_rules"),
            exclusion_rules=value.get("exclusion_rules"),
            required_annotation_artifact_ids=tuple(raw_annotations),
            transformations=tuple(raw_transformations),
            splits=tuple(DatasetSplit.from_dict(item) for item in raw_splits),
            generator_revision=value.get("generator_revision"),
            schema_version=value.get("dataset_manifest_schema_version"),
            kind=value.get("kind"),
        )
        manifest.validate()
        expected_digest = _artifact_id(value.get("dataset_digest"), "dataset_digest")
        if manifest.dataset_digest != expected_digest:
            raise DatasetManifestError("dataset_digest does not match canonical manifest content")
        return manifest

    def split(self, name: str) -> DatasetSplit:
        for split in self.splits:
            if split.name == name:
                return split
        raise DatasetManifestError(f"unknown dataset split: {name}")


@dataclass(frozen=True)
class DatasetManifestWriteResult:
    artifact: StoredBlob
    catalog_path: Path
    dataset_digest: str


class DatasetManifestStore:
    """Validate and store dataset manifests through the #74 storage substrate."""

    def __init__(self, layout: StorageLayout):
        if not isinstance(layout, StorageLayout):
            raise DatasetManifestError("layout must be a StorageLayout")
        self.layout = layout
        self.artifacts = LocalArtifactStore(layout)
        self.annotations = AnnotationStore(layout)
        self.external = ExternalProvenanceStore(layout)

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _catalog_path(self, manifest: DatasetManifest) -> Path:
        dataset_digest = self._digest(manifest.dataset_id)
        version_digest = self._digest(manifest.version)
        return (
            self.layout.path("datasets")
            / dataset_digest[:2]
            / dataset_digest
            / f"{version_digest}.json"
        )

    def _load_evidence(self, artifact_id: str) -> Mapping[str, Any]:
        try:
            payload = self.artifacts.read_bytes(artifact_id)
        except StorageError as exc:
            raise DatasetManifestError(f"source evidence artifact not found: {artifact_id}") from exc
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DatasetManifestError("source evidence artifact is not UTF-8 JSON") from exc
        if not isinstance(value, Mapping):
            raise DatasetManifestError("source evidence artifact must decode to an object")
        try:
            validate_raw_evidence_envelope(value)
        except EvidenceError as exc:
            raise DatasetManifestError("dataset source is not valid raw evidence") from exc
        return value

    @staticmethod
    def _evidence_decision_ids(evidence: Mapping[str, Any]) -> frozenset[str]:
        decisions = evidence.get("decisions")
        if not isinstance(decisions, list):
            raise DatasetManifestError("raw evidence decisions must be an array")
        result: set[str] = set()
        for decision in decisions:
            if not isinstance(decision, Mapping):
                raise DatasetManifestError("raw evidence decision must be an object")
            provenance = decision.get("provenance")
            if not isinstance(provenance, Mapping):
                raise DatasetManifestError("raw evidence decision provenance must be an object")
            result.add(_require_string(provenance.get("decision_id"), "raw evidence decision_id"))
        return frozenset(result)

    def _validate_sources(self, manifest: DatasetManifest) -> None:
        evidence_cache: dict[str, Mapping[str, Any]] = {}
        selected_decisions: set[str] = set()
        selected_sources: set[str] = set()
        external_index_cache: dict[str, tuple[str, Mapping[str, str]]] = {}
        leakage_groups: dict[tuple[str, str], str] = {}

        for split in manifest.splits:
            for selection in split.selections:
                evidence = evidence_cache.get(selection.evidence_artifact_id)
                if evidence is None:
                    evidence = self._load_evidence(selection.evidence_artifact_id)
                    evidence_cache[selection.evidence_artifact_id] = evidence
                selected_sources.add(selection.evidence_artifact_id)
                available = self._evidence_decision_ids(evidence)
                missing = sorted(set(selection.decision_ids) - available)
                if missing:
                    raise DatasetManifestError(
                        f"dataset split {split.name!r} selects decisions missing from source "
                        f"{selection.evidence_artifact_id}: {', '.join(missing)}"
                    )
                qualification = evidence.get("qualification")
                if not isinstance(qualification, Mapping):
                    raise DatasetManifestError("raw evidence qualification must be an object")
                if qualification.get("diagnostic_only") is True and split.role != "diagnostic":
                    if not split.allow_diagnostic_evidence:
                        raise DatasetManifestError(
                            f"diagnostic-only evidence cannot enter {split.role!r} split "
                            f"{split.name!r} without an explicit safe-subset justification"
                        )
                selected_decisions.update(selection.decision_ids)

            for selection in split.external_selections:
                cached = external_index_cache.get(selection.source_manifest_artifact_id)
                if cached is None:
                    try:
                        external_manifest = self.external.read_data_source_artifact(
                            selection.source_manifest_artifact_id
                        )
                        record_index = self.external.read_record_index(
                            external_manifest.record_index_artifact_id
                        )
                    except ExternalProvenanceError as exc:
                        raise DatasetManifestError(
                            "external dataset source manifest/index is invalid: "
                            + selection.source_manifest_artifact_id
                        ) from exc
                    if split.role != "diagnostic":
                        if external_manifest.acting_player_information_boundary != "seat_visible":
                            raise DatasetManifestError(
                                "non-diagnostic external data must be explicitly seat_visible"
                            )
                        if external_manifest.uncertainty.get(
                            "contains_privileged_or_future_information"
                        ) is not False:
                            raise DatasetManifestError(
                                "external data must explicitly exclude privileged/future information"
                            )
                        if (
                            split.role == "train"
                            and external_manifest.license.training_allowed is not True
                        ):
                            raise DatasetManifestError(
                                "external training data requires explicit training permission"
                            )
                    cached = (external_manifest.deduplication_identity, record_index)
                    external_index_cache[selection.source_manifest_artifact_id] = cached
                deduplication_identity, record_index = cached
                missing = sorted(set(selection.record_ids) - set(record_index))
                if missing:
                    raise DatasetManifestError(
                        f"dataset split {split.name!r} selects records missing from external "
                        f"source {selection.source_manifest_artifact_id}: {', '.join(missing)}"
                    )
                for record_id in selection.record_ids:
                    group_key = (deduplication_identity, record_index[record_id])
                    previous = leakage_groups.get(group_key)
                    if previous is not None and previous != split.name:
                        raise DatasetManifestError(
                            "external leakage group crosses dataset splits: "
                            f"{group_key[1]!r} in {previous!r} and {split.name!r}"
                        )
                    leakage_groups[group_key] = split.name

        for annotation_artifact_id in manifest.required_annotation_artifact_ids:
            try:
                annotation = self.annotations.read_artifact(annotation_artifact_id)
            except AnnotationError as exc:
                raise DatasetManifestError(
                    f"required annotation artifact is invalid: {annotation_artifact_id}"
                ) from exc
            if annotation.source_evidence_artifact_id not in selected_sources:
                raise DatasetManifestError(
                    "required annotation must join to evidence selected by this dataset"
                )
            if (
                annotation.target_kind == ANNOTATION_TARGET_DECISION
                and annotation.target_id not in selected_decisions
            ):
                raise DatasetManifestError(
                    "required decision annotation must target a decision selected by this dataset"
                )

    def _write_catalog_entry(self, path: Path, payload: Mapping[str, Any]) -> None:
        data = _canonical_json_bytes(payload)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != data:
                raise DatasetManifestError(
                    "dataset_id/version is already cataloged to different immutable content"
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
                raise DatasetManifestError("concurrent dataset catalog write was inconsistent")
        finally:
            if temporary.exists():
                temporary.unlink()

    def write(self, manifest: DatasetManifest) -> DatasetManifestWriteResult:
        if not isinstance(manifest, DatasetManifest):
            raise DatasetManifestError("manifest must be a DatasetManifest")
        manifest.validate()
        self._validate_sources(manifest)
        payload = _canonical_json_bytes(manifest.to_dict())
        artifact = self.artifacts.put_bytes(payload)
        catalog_path = self._catalog_path(manifest)
        self._write_catalog_entry(
            catalog_path,
            {
                "catalog_schema_version": DATASET_CATALOG_SCHEMA_VERSION,
                "dataset_id": manifest.dataset_id,
                "version": manifest.version,
                "purpose": manifest.purpose,
                "dataset_digest": manifest.dataset_digest,
                "artifact_id": artifact.artifact_id,
                "artifact_size_bytes": artifact.size_bytes,
                "generator_revision": manifest.generator_revision,
            },
        )
        return DatasetManifestWriteResult(
            artifact=artifact,
            catalog_path=catalog_path,
            dataset_digest=manifest.dataset_digest,
        )

    def read_artifact(self, artifact_id: str) -> DatasetManifest:
        try:
            payload = self.artifacts.read_bytes(artifact_id)
        except StorageError as exc:
            raise DatasetManifestError(f"dataset manifest artifact not found: {artifact_id}") from exc
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DatasetManifestError("dataset manifest artifact is not UTF-8 JSON") from exc
        if not isinstance(value, Mapping):
            raise DatasetManifestError("dataset manifest artifact must decode to an object")
        manifest = DatasetManifest.from_dict(value)
        expected = "sha256:" + hashlib.sha256(payload).hexdigest()
        if artifact_id != expected:
            raise DatasetManifestError("dataset manifest artifact identity mismatch")
        return manifest

    def read(self, *, dataset_id: str, version: str) -> DatasetManifest:
        probe = DatasetManifest(
            dataset_id=dataset_id,
            version=version,
            purpose="catalog-probe",
            created_at="catalog-probe",
            source_population="catalog-probe",
            source_query={},
            selection_rules={},
            exclusion_rules={},
            required_annotation_artifact_ids=(),
            transformations=(),
            splits=(
                DatasetSplit(
                    name="catalog-probe",
                    role="diagnostic",
                    selections=(
                        DatasetEvidenceSelection(
                            evidence_artifact_id="sha256:" + "0" * 64,
                            decision_ids=("catalog-probe",),
                        ),
                    ),
                ),
            ),
            generator_revision="catalog-probe",
        )
        path = self._catalog_path(probe)
        try:
            catalog = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise DatasetManifestError(
                f"dataset manifest not found: {dataset_id}:{version}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise DatasetManifestError("dataset catalog entry is not valid JSON") from exc
        if not isinstance(catalog, Mapping):
            raise DatasetManifestError("dataset catalog entry must be an object")
        if catalog.get("catalog_schema_version") != DATASET_CATALOG_SCHEMA_VERSION:
            raise DatasetManifestError("unsupported dataset catalog schema version")
        if catalog.get("dataset_id") != dataset_id or catalog.get("version") != version:
            raise DatasetManifestError("dataset catalog identity mismatch")
        artifact_id = _artifact_id(catalog.get("artifact_id"), "dataset catalog artifact_id")
        manifest = self.read_artifact(artifact_id)
        if manifest.dataset_id != dataset_id or manifest.version != version:
            raise DatasetManifestError("dataset manifest identity does not match catalog")
        if manifest.dataset_digest != catalog.get("dataset_digest"):
            raise DatasetManifestError("dataset manifest digest does not match catalog")
        return manifest


def validate_export_selection(
    manifest: DatasetManifest,
    split_name: str,
    decision_ids: Iterable[str],
) -> None:
    """Fail closed if an export escapes manifest membership or crosses a frozen split."""

    if not isinstance(manifest, DatasetManifest):
        raise DatasetManifestError("manifest must be a DatasetManifest")
    manifest.validate()
    split = manifest.split(split_name)
    requested = list(decision_ids)
    if any(not isinstance(item, str) or not item for item in requested):
        raise DatasetManifestError("export decision_ids must be non-empty strings")
    if len(requested) != len(set(requested)):
        raise DatasetManifestError("export decision_ids must be unique")
    outside = sorted(set(requested) - set(split.decision_ids))
    if outside:
        raise DatasetManifestError(
            f"export selects decisions outside split {split_name!r}: {', '.join(outside)}"
        )
    if split.role in {"train", "validation"}:
        frozen = {
            decision_id
            for candidate in manifest.splits
            if candidate.role == "frozen_test"
            for decision_id in candidate.decision_ids
        }
        leaked = sorted(set(requested) & frozen)
        if leaked:
            raise DatasetManifestError(
                "frozen-test decisions cannot be re-ingested into train/validation export: "
                + ", ".join(leaked)
            )


def validate_external_export_selection(
    manifest: DatasetManifest,
    split_name: str,
    record_keys: Iterable[tuple[str, str]],
) -> None:
    """Fail closed if an external-record export escapes frozen manifest membership."""

    if not isinstance(manifest, DatasetManifest):
        raise DatasetManifestError("manifest must be a DatasetManifest")
    manifest.validate()
    split = manifest.split(split_name)
    requested = list(record_keys)
    if any(
        not isinstance(item, tuple)
        or len(item) != 2
        or any(not isinstance(part, str) or not part for part in item)
        for item in requested
    ):
        raise DatasetManifestError(
            "external export record keys must be (source_manifest_artifact_id, record_id)"
        )
    if len(requested) != len(set(requested)):
        raise DatasetManifestError("external export record keys must be unique")
    outside = sorted(set(requested) - set(split.external_record_keys))
    if outside:
        rendered = ", ".join(f"{source}:{record}" for source, record in outside)
        raise DatasetManifestError(
            f"external export selects records outside split {split_name!r}: {rendered}"
        )
    if split.role in {"train", "validation"}:
        frozen = {
            record_key
            for candidate in manifest.splits
            if candidate.role == "frozen_test"
            for record_key in candidate.external_record_keys
        }
        if set(requested) & frozen:
            raise DatasetManifestError(
                "frozen-test external records cannot be re-ingested into "
                "train/validation export"
            )
