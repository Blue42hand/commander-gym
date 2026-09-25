"""Immutable provenance contracts for imported models and external Magic datasets.

This extends Commander Gym's #53 lineage without converting external material into
native Argentum evidence. Imported bytes remain content addressed by the #74 storage
substrate while manifests preserve source, license, information-boundary, and
transformation metadata needed for later training/evaluation qualification.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from .evidence import EvidenceError
from .storage import LocalArtifactStore, StorageError, StorageLayout, StoredBlob, parse_artifact_id

EXTERNAL_PROVENANCE_SCHEMA_VERSION = 1
EXTERNAL_DATA_SOURCE_KIND = "commander-gym.external-data-source"
EXTERNAL_MODEL_KIND = "commander-gym.external-model"
EXTERNAL_PROVENANCE_CATALOG_SCHEMA_VERSION = 1

EXTERNAL_SOURCE_CLASSES = frozenset(
    {
        "external_observation_action_record",
        "reconstructed_decision",
        "expert_recommendation",
        "metagame_result",
        "strategic_reference",
    }
)
INFORMATION_COMPLETENESS = frozenset({"complete", "partial", "aggregate", "unknown"})
POLICY_INPUT_INFORMATION_BOUNDARIES = frozenset(
    {"seat_visible", "privileged", "future_derived", "ambiguous", "unqualified"}
)
PRIVILEGED_INFORMATION_CHANNELS = frozenset(
    {
        "critic_input",
        "value_target",
        "outcome_target",
        "adjudication_target",
        "search_teacher",
        "training_target",
    }
)
SEAT_SAFE_QUALIFICATIONS = frozenset(
    {"unreviewed", "qualified", "rejected", "training_only_privileged"}
)


class ExternalProvenanceError(EvidenceError):
    """Raised when imported-source provenance is incomplete or inconsistent."""


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ExternalProvenanceError(f"{label} must be a non-empty string")
    return value


def _artifact_id(value: Any, label: str) -> str:
    candidate = _require_string(value, label)
    try:
        parse_artifact_id(candidate)
    except StorageError as exc:
        raise ExternalProvenanceError(f"{label} must be a valid artifact ID") from exc
    return candidate


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ExternalProvenanceError(f"{label} must be an object")
    return value


def _require_string_tuple(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise ExternalProvenanceError(f"{label} must be a tuple")
    if any(not isinstance(item, str) or not item for item in value):
        raise ExternalProvenanceError(f"{label} must contain non-empty strings")
    if len(value) != len(set(value)):
        raise ExternalProvenanceError(f"{label} must not contain duplicates")
    return value


@dataclass(frozen=True)
class ExternalLicense:
    """License/terms snapshot attached to an imported artifact."""

    license_id: str
    terms_reference: str
    training_allowed: bool | None
    redistribution_allowed: bool | None
    notes: str | None = None

    def validate(self) -> None:
        _require_string(self.license_id, "license_id")
        _require_string(self.terms_reference, "terms_reference")
        for label, value in (
            ("training_allowed", self.training_allowed),
            ("redistribution_allowed", self.redistribution_allowed),
        ):
            if value is not None and type(value) is not bool:
                raise ExternalProvenanceError(f"{label} must be boolean or null")
        if self.notes is not None:
            _require_string(self.notes, "license notes")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        result = {
            "license_id": self.license_id,
            "terms_reference": self.terms_reference,
            "training_allowed": self.training_allowed,
            "redistribution_allowed": self.redistribution_allowed,
        }
        if self.notes is not None:
            result["notes"] = self.notes
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExternalLicense":
        _require_mapping(value, "license")
        result = cls(
            license_id=value.get("license_id"),
            terms_reference=value.get("terms_reference"),
            training_allowed=value.get("training_allowed"),
            redistribution_allowed=value.get("redistribution_allowed"),
            notes=value.get("notes"),
        )
        result.validate()
        return result


@dataclass(frozen=True)
class ExternalDataSourceManifest:
    """Immutable description of one imported external evidence source."""

    source_id: str
    version: str
    retrieved_at: str
    source_class: str
    source_reference: str
    source_revision: str
    payload_artifact_id: str
    record_index_artifact_id: str
    license: ExternalLicense
    information_completeness: str
    acting_player_information_boundary: str
    privileged_information_channels: tuple[str, ...]
    missing_fields: tuple[str, ...]
    reconstruction_transforms: tuple[Mapping[str, Any], ...]
    uncertainty: Mapping[str, Any]
    deduplication_identity: str
    record_schema: Mapping[str, Any]
    schema_version: int = EXTERNAL_PROVENANCE_SCHEMA_VERSION
    kind: str = EXTERNAL_DATA_SOURCE_KIND

    def validate(self) -> None:
        if self.schema_version != EXTERNAL_PROVENANCE_SCHEMA_VERSION:
            raise ExternalProvenanceError(
                f"unsupported external provenance schema_version {self.schema_version!r}"
            )
        if self.kind != EXTERNAL_DATA_SOURCE_KIND:
            raise ExternalProvenanceError(
                f"external data source kind must be {EXTERNAL_DATA_SOURCE_KIND!r}"
            )
        for label, value in (
            ("source_id", self.source_id),
            ("version", self.version),
            ("retrieved_at", self.retrieved_at),
            ("source_reference", self.source_reference),
            ("source_revision", self.source_revision),
            ("acting_player_information_boundary", self.acting_player_information_boundary),
            ("deduplication_identity", self.deduplication_identity),
        ):
            _require_string(value, label)
        if self.source_class not in EXTERNAL_SOURCE_CLASSES:
            raise ExternalProvenanceError(
                f"source_class must be one of {sorted(EXTERNAL_SOURCE_CLASSES)!r}"
            )
        if self.acting_player_information_boundary not in POLICY_INPUT_INFORMATION_BOUNDARIES:
            raise ExternalProvenanceError(
                "acting_player_information_boundary describes the policy-input channel and "
                "must be one of "
                + repr(sorted(POLICY_INPUT_INFORMATION_BOUNDARIES))
            )
        _require_string_tuple(
            self.privileged_information_channels, "privileged_information_channels"
        )
        unknown_channels = sorted(
            set(self.privileged_information_channels) - PRIVILEGED_INFORMATION_CHANNELS
        )
        if unknown_channels:
            raise ExternalProvenanceError(
                "privileged_information_channels contains unsupported channels: "
                + ", ".join(unknown_channels)
            )
        _artifact_id(self.payload_artifact_id, "payload_artifact_id")
        _artifact_id(self.record_index_artifact_id, "record_index_artifact_id")
        if not isinstance(self.license, ExternalLicense):
            raise ExternalProvenanceError("license must be ExternalLicense")
        self.license.validate()
        if self.information_completeness not in INFORMATION_COMPLETENESS:
            raise ExternalProvenanceError(
                "information_completeness must be one of "
                + repr(sorted(INFORMATION_COMPLETENESS))
            )
        _require_string_tuple(self.missing_fields, "missing_fields")
        if not isinstance(self.reconstruction_transforms, tuple):
            raise ExternalProvenanceError("reconstruction_transforms must be a tuple")
        if any(not isinstance(item, Mapping) for item in self.reconstruction_transforms):
            raise ExternalProvenanceError("each reconstruction transform must be an object")
        _require_mapping(self.uncertainty, "uncertainty")
        if "contains_privileged_or_future_information" in self.uncertainty:
            raise ExternalProvenanceError(
                "source-wide contains_privileged_or_future_information is ambiguous; "
                "qualify policy inputs with acting_player_information_boundary and record "
                "privileged target/critic/search provenance in privileged_information_channels"
            )
        _require_mapping(self.record_schema, "record_schema")

    def _digest_payload(self) -> dict[str, Any]:
        self.validate()
        return {
            "external_provenance_schema_version": self.schema_version,
            "kind": self.kind,
            "source_id": self.source_id,
            "version": self.version,
            "retrieved_at": self.retrieved_at,
            "source_class": self.source_class,
            "source_reference": self.source_reference,
            "source_revision": self.source_revision,
            "payload_artifact_id": self.payload_artifact_id,
            "record_index_artifact_id": self.record_index_artifact_id,
            "license": self.license.to_dict(),
            "information_completeness": self.information_completeness,
            "acting_player_information_boundary": self.acting_player_information_boundary,
            "privileged_information_channels": sorted(self.privileged_information_channels),
            "missing_fields": sorted(self.missing_fields),
            "reconstruction_transforms": [
                dict(item) for item in self.reconstruction_transforms
            ],
            "uncertainty": dict(self.uncertainty),
            "deduplication_identity": self.deduplication_identity,
            "record_schema": dict(self.record_schema),
        }

    @property
    def source_digest(self) -> str:
        return "sha256:" + hashlib.sha256(_canonical_json_bytes(self._digest_payload())).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        result = self._digest_payload()
        result["source_digest"] = self.source_digest
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExternalDataSourceManifest":
        _require_mapping(value, "external data source manifest")
        raw_missing = value.get("missing_fields", [])
        raw_transforms = value.get("reconstruction_transforms", [])
        raw_privileged_channels = value.get("privileged_information_channels")
        if not isinstance(raw_missing, list):
            raise ExternalProvenanceError("missing_fields must be an array")
        if not isinstance(raw_transforms, list):
            raise ExternalProvenanceError("reconstruction_transforms must be an array")
        if not isinstance(raw_privileged_channels, list):
            raise ExternalProvenanceError(
                "privileged_information_channels must be an explicit array"
            )
        result = cls(
            source_id=value.get("source_id"),
            version=value.get("version"),
            retrieved_at=value.get("retrieved_at"),
            source_class=value.get("source_class"),
            source_reference=value.get("source_reference"),
            source_revision=value.get("source_revision"),
            payload_artifact_id=value.get("payload_artifact_id"),
            record_index_artifact_id=value.get("record_index_artifact_id"),
            license=ExternalLicense.from_dict(value.get("license")),
            information_completeness=value.get("information_completeness"),
            acting_player_information_boundary=value.get(
                "acting_player_information_boundary"
            ),
            privileged_information_channels=tuple(raw_privileged_channels),
            missing_fields=tuple(raw_missing),
            reconstruction_transforms=tuple(raw_transforms),
            uncertainty=value.get("uncertainty"),
            deduplication_identity=value.get("deduplication_identity"),
            record_schema=value.get("record_schema"),
            schema_version=value.get("external_provenance_schema_version"),
            kind=value.get("kind"),
        )
        result.validate()
        if result.source_digest != _artifact_id(value.get("source_digest"), "source_digest"):
            raise ExternalProvenanceError(
                "source_digest does not match canonical external source content"
            )
        return result


@dataclass(frozen=True)
class ExternalModelManifest:
    """Immutable provenance for an imported pretrained model/checkpoint."""

    model_id: str
    version: str
    retrieved_at: str
    source_project: str
    source_reference: str
    source_revision: str
    checkpoint_artifact_id: str
    license: ExternalLicense
    framework: str
    architecture: str
    training_provenance: Mapping[str, Any]
    observation_schema: Mapping[str, Any]
    action_schema: Mapping[str, Any] | None
    value_schema: Mapping[str, Any] | None
    formats: tuple[str, ...]
    decks: tuple[str, ...]
    opponent_population: Mapping[str, Any]
    privileged_information_exposure: Mapping[str, Any]
    policy_input_information_boundary: str
    privileged_training_channels: tuple[str, ...]
    evaluation_caveats: tuple[str, ...]
    adapter_history: tuple[Mapping[str, Any], ...]
    seat_safe_qualification: str
    schema_version: int = EXTERNAL_PROVENANCE_SCHEMA_VERSION
    kind: str = EXTERNAL_MODEL_KIND

    def validate(self) -> None:
        if self.schema_version != EXTERNAL_PROVENANCE_SCHEMA_VERSION:
            raise ExternalProvenanceError(
                f"unsupported external provenance schema_version {self.schema_version!r}"
            )
        if self.kind != EXTERNAL_MODEL_KIND:
            raise ExternalProvenanceError(
                f"external model kind must be {EXTERNAL_MODEL_KIND!r}"
            )
        for label, value in (
            ("model_id", self.model_id),
            ("version", self.version),
            ("retrieved_at", self.retrieved_at),
            ("source_project", self.source_project),
            ("source_reference", self.source_reference),
            ("source_revision", self.source_revision),
            ("framework", self.framework),
            ("architecture", self.architecture),
        ):
            _require_string(value, label)
        _artifact_id(self.checkpoint_artifact_id, "checkpoint_artifact_id")
        if not isinstance(self.license, ExternalLicense):
            raise ExternalProvenanceError("license must be ExternalLicense")
        self.license.validate()
        _require_mapping(self.training_provenance, "training_provenance")
        _require_mapping(self.observation_schema, "observation_schema")
        if self.action_schema is not None:
            _require_mapping(self.action_schema, "action_schema")
        if self.value_schema is not None:
            _require_mapping(self.value_schema, "value_schema")
        _require_string_tuple(self.formats, "formats")
        _require_string_tuple(self.decks, "decks")
        _require_mapping(self.opponent_population, "opponent_population")
        _require_mapping(
            self.privileged_information_exposure, "privileged_information_exposure"
        )
        if self.policy_input_information_boundary not in POLICY_INPUT_INFORMATION_BOUNDARIES:
            raise ExternalProvenanceError(
                "policy_input_information_boundary must be one of "
                + repr(sorted(POLICY_INPUT_INFORMATION_BOUNDARIES))
            )
        _require_string_tuple(
            self.privileged_training_channels, "privileged_training_channels"
        )
        unknown_channels = sorted(
            set(self.privileged_training_channels) - PRIVILEGED_INFORMATION_CHANNELS
        )
        if unknown_channels:
            raise ExternalProvenanceError(
                "privileged_training_channels contains unsupported channels: "
                + ", ".join(unknown_channels)
            )
        _require_string_tuple(self.evaluation_caveats, "evaluation_caveats")
        if not isinstance(self.adapter_history, tuple):
            raise ExternalProvenanceError("adapter_history must be a tuple")
        if any(not isinstance(item, Mapping) for item in self.adapter_history):
            raise ExternalProvenanceError("each adapter_history item must be an object")
        if self.seat_safe_qualification not in SEAT_SAFE_QUALIFICATIONS:
            raise ExternalProvenanceError(
                "seat_safe_qualification must be one of "
                + repr(sorted(SEAT_SAFE_QUALIFICATIONS))
            )
        if (
            self.seat_safe_qualification == "qualified"
            and self.policy_input_information_boundary != "seat_visible"
        ):
            raise ExternalProvenanceError(
                "qualified external models require explicitly seat_visible policy inputs"
            )

    def _digest_payload(self) -> dict[str, Any]:
        self.validate()
        result: dict[str, Any] = {
            "external_provenance_schema_version": self.schema_version,
            "kind": self.kind,
            "model_id": self.model_id,
            "version": self.version,
            "retrieved_at": self.retrieved_at,
            "source_project": self.source_project,
            "source_reference": self.source_reference,
            "source_revision": self.source_revision,
            "checkpoint_artifact_id": self.checkpoint_artifact_id,
            "checkpoint_digest": self.checkpoint_artifact_id,
            "license": self.license.to_dict(),
            "framework": self.framework,
            "architecture": self.architecture,
            "training_provenance": dict(self.training_provenance),
            "observation_schema": dict(self.observation_schema),
            "formats": sorted(self.formats),
            "decks": sorted(self.decks),
            "opponent_population": dict(self.opponent_population),
            "privileged_information_exposure": dict(
                self.privileged_information_exposure
            ),
            "policy_input_information_boundary": self.policy_input_information_boundary,
            "privileged_training_channels": sorted(self.privileged_training_channels),
            "evaluation_caveats": sorted(self.evaluation_caveats),
            "adapter_history": [dict(item) for item in self.adapter_history],
            "seat_safe_qualification": self.seat_safe_qualification,
        }
        if self.action_schema is not None:
            result["action_schema"] = dict(self.action_schema)
        if self.value_schema is not None:
            result["value_schema"] = dict(self.value_schema)
        return result

    @property
    def model_digest(self) -> str:
        return "sha256:" + hashlib.sha256(_canonical_json_bytes(self._digest_payload())).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        result = self._digest_payload()
        result["model_digest"] = self.model_digest
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExternalModelManifest":
        _require_mapping(value, "external model manifest")
        raw_formats = value.get("formats", [])
        raw_decks = value.get("decks", [])
        raw_caveats = value.get("evaluation_caveats", [])
        raw_history = value.get("adapter_history", [])
        raw_privileged_channels = value.get("privileged_training_channels")
        for label, raw in (
            ("formats", raw_formats),
            ("decks", raw_decks),
            ("evaluation_caveats", raw_caveats),
            ("adapter_history", raw_history),
        ):
            if not isinstance(raw, list):
                raise ExternalProvenanceError(f"{label} must be an array")
        if not isinstance(raw_privileged_channels, list):
            raise ExternalProvenanceError(
                "privileged_training_channels must be an explicit array"
            )
        checkpoint_artifact_id = value.get("checkpoint_artifact_id")
        if value.get("checkpoint_digest") != checkpoint_artifact_id:
            raise ExternalProvenanceError(
                "checkpoint_digest must equal the content-addressed checkpoint artifact ID"
            )
        result = cls(
            model_id=value.get("model_id"),
            version=value.get("version"),
            retrieved_at=value.get("retrieved_at"),
            source_project=value.get("source_project"),
            source_reference=value.get("source_reference"),
            source_revision=value.get("source_revision"),
            checkpoint_artifact_id=checkpoint_artifact_id,
            license=ExternalLicense.from_dict(value.get("license")),
            framework=value.get("framework"),
            architecture=value.get("architecture"),
            training_provenance=value.get("training_provenance"),
            observation_schema=value.get("observation_schema"),
            action_schema=value.get("action_schema"),
            value_schema=value.get("value_schema"),
            formats=tuple(raw_formats),
            decks=tuple(raw_decks),
            opponent_population=value.get("opponent_population"),
            privileged_information_exposure=value.get(
                "privileged_information_exposure"
            ),
            policy_input_information_boundary=value.get(
                "policy_input_information_boundary"
            ),
            privileged_training_channels=tuple(raw_privileged_channels),
            evaluation_caveats=tuple(raw_caveats),
            adapter_history=tuple(raw_history),
            seat_safe_qualification=value.get("seat_safe_qualification"),
            schema_version=value.get("external_provenance_schema_version"),
            kind=value.get("kind"),
        )
        result.validate()
        if result.model_digest != _artifact_id(value.get("model_digest"), "model_digest"):
            raise ExternalProvenanceError(
                "model_digest does not match canonical external model content"
            )
        return result


@dataclass(frozen=True)
class ExternalProvenanceWriteResult:
    artifact: StoredBlob
    catalog_path: Path
    provenance_digest: str


class ExternalProvenanceStore:
    """Catalog imported data/model provenance through content-addressed storage."""

    def __init__(self, layout: StorageLayout):
        if not isinstance(layout, StorageLayout):
            raise ExternalProvenanceError("layout must be a StorageLayout")
        self.layout = layout
        self.artifacts = LocalArtifactStore(layout)

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _catalog_path(self, *, family: str, identity: str, version: str) -> Path:
        identity_digest = self._digest(_require_string(identity, "catalog identity"))
        revision = self._digest(_require_string(version, "catalog version"))
        route = "datasets" if family == "data" else "models"
        return (
            self.layout.path(route)
            / "external"
            / identity_digest[:2]
            / identity_digest
            / f"{revision}.json"
        )

    def _assert_artifact_exists(self, artifact_id: str, label: str) -> None:
        try:
            self.artifacts.read_bytes(artifact_id)
        except StorageError as exc:
            raise ExternalProvenanceError(f"{label} not found: {artifact_id}") from exc

    def _write_catalog_entry(self, path: Path, payload: Mapping[str, Any]) -> None:
        data = _canonical_json_bytes(payload)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != data:
                raise ExternalProvenanceError(
                    "external identity/version already maps to different immutable content"
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
                raise ExternalProvenanceError(
                    "concurrent external provenance catalog write was inconsistent"
                )
        finally:
            if temporary.exists():
                temporary.unlink()

    def write_data_source(
        self, manifest: ExternalDataSourceManifest
    ) -> ExternalProvenanceWriteResult:
        if not isinstance(manifest, ExternalDataSourceManifest):
            raise ExternalProvenanceError(
                "manifest must be an ExternalDataSourceManifest"
            )
        manifest.validate()
        self._assert_artifact_exists(manifest.payload_artifact_id, "external payload artifact")
        self._assert_artifact_exists(
            manifest.record_index_artifact_id, "external record index artifact"
        )
        self.read_record_index(manifest.record_index_artifact_id)
        artifact = self.artifacts.put_bytes(_canonical_json_bytes(manifest.to_dict()))
        path = self._catalog_path(
            family="data", identity=manifest.source_id, version=manifest.version
        )
        self._write_catalog_entry(
            path,
            {
                "catalog_schema_version": EXTERNAL_PROVENANCE_CATALOG_SCHEMA_VERSION,
                "kind": manifest.kind,
                "source_id": manifest.source_id,
                "version": manifest.version,
                "source_class": manifest.source_class,
                "source_digest": manifest.source_digest,
                "artifact_id": artifact.artifact_id,
            },
        )
        return ExternalProvenanceWriteResult(
            artifact=artifact,
            catalog_path=path,
            provenance_digest=manifest.source_digest,
        )

    def write_model(
        self, manifest: ExternalModelManifest
    ) -> ExternalProvenanceWriteResult:
        if not isinstance(manifest, ExternalModelManifest):
            raise ExternalProvenanceError("manifest must be an ExternalModelManifest")
        manifest.validate()
        self._assert_artifact_exists(
            manifest.checkpoint_artifact_id, "external checkpoint artifact"
        )
        artifact = self.artifacts.put_bytes(_canonical_json_bytes(manifest.to_dict()))
        path = self._catalog_path(
            family="model", identity=manifest.model_id, version=manifest.version
        )
        self._write_catalog_entry(
            path,
            {
                "catalog_schema_version": EXTERNAL_PROVENANCE_CATALOG_SCHEMA_VERSION,
                "kind": manifest.kind,
                "model_id": manifest.model_id,
                "version": manifest.version,
                "model_digest": manifest.model_digest,
                "checkpoint_artifact_id": manifest.checkpoint_artifact_id,
                "artifact_id": artifact.artifact_id,
            },
        )
        return ExternalProvenanceWriteResult(
            artifact=artifact,
            catalog_path=path,
            provenance_digest=manifest.model_digest,
        )

    def _read_manifest_payload(self, artifact_id: str) -> Mapping[str, Any]:
        try:
            payload = self.artifacts.read_bytes(artifact_id)
        except StorageError as exc:
            raise ExternalProvenanceError(
                f"external provenance artifact not found: {artifact_id}"
            ) from exc
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExternalProvenanceError(
                "external provenance artifact is not UTF-8 JSON"
            ) from exc
        if not isinstance(value, Mapping):
            raise ExternalProvenanceError(
                "external provenance artifact must decode to an object"
            )
        expected = "sha256:" + hashlib.sha256(payload).hexdigest()
        if expected != artifact_id:
            raise ExternalProvenanceError("external provenance artifact identity mismatch")
        return value

    def read_data_source_artifact(
        self, artifact_id: str
    ) -> ExternalDataSourceManifest:
        return ExternalDataSourceManifest.from_dict(
            self._read_manifest_payload(artifact_id)
        )

    def read_model_artifact(self, artifact_id: str) -> ExternalModelManifest:
        return ExternalModelManifest.from_dict(self._read_manifest_payload(artifact_id))

    def read_record_index(self, artifact_id: str) -> Mapping[str, str]:
        """Read immutable record_id -> leakage_group_id membership."""

        self._assert_artifact_exists(artifact_id, "external record index artifact")
        try:
            payload = self.artifacts.read_bytes(artifact_id)
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExternalProvenanceError(
                "external record index must be UTF-8 JSON"
            ) from exc
        if not isinstance(value, Mapping):
            raise ExternalProvenanceError("external record index must be an object")
        records = value.get("records")
        if not isinstance(records, list) or not records:
            raise ExternalProvenanceError(
                "external record index requires a non-empty records array"
            )
        result: dict[str, str] = {}
        for item in records:
            if not isinstance(item, Mapping):
                raise ExternalProvenanceError(
                    "external record index entries must be objects"
                )
            record_id = _require_string(item.get("record_id"), "record_id")
            leakage_group_id = _require_string(
                item.get("leakage_group_id"), "leakage_group_id"
            )
            if record_id in result:
                raise ExternalProvenanceError(
                    f"duplicate external record_id in index: {record_id}"
                )
            result[record_id] = leakage_group_id
        return result
