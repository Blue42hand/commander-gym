"""Versioned deck/pilot package identity for Commander Gym.

DeckPackage is format-aware but keeps format-specific requirements outside the
core schema. Argentum remains authoritative for legality and rules capability.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Any, Dict, Mapping, Optional, Tuple

DECK_PACKAGE_SCHEMA_VERSION = 2


class DeckPackageError(ValueError):
    """Raised when a DeckPackage payload is malformed or unsupported."""


def _nonempty(value: str, field_name: str) -> str:
    value = str(value or "").strip()
    if not value:
        raise DeckPackageError(f"{field_name} must be non-empty")
    return value


def _mapping(value: Any, field_name: str) -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise DeckPackageError(f"{field_name} must be a mapping")
    return dict(value)


@dataclass(frozen=True)
class ArtifactRef:
    """Stable provider-neutral identity for a package artifact."""

    kind: str
    artifact_id: str
    version: str
    digest: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        _nonempty(self.kind, "artifact.kind")
        _nonempty(self.artifact_id, "artifact.artifact_id")
        _nonempty(self.version, "artifact.version")
        if not isinstance(self.metadata, Mapping):
            raise DeckPackageError("artifact.metadata must be a mapping")

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ArtifactRef":
        if not isinstance(raw, Mapping):
            raise DeckPackageError("artifact must be a mapping")
        ref = cls(
            kind=str(raw.get("kind", "")),
            artifact_id=str(raw.get("artifact_id", "")),
            version=str(raw.get("version", "")),
            digest=(str(raw["digest"]) if raw.get("digest") is not None else None),
            metadata=_mapping(raw.get("metadata"), "artifact.metadata"),
        )
        ref.validate()
        return ref


@dataclass(frozen=True)
class DeckPackage:
    """Reproducible identity for a deck plus the policy context used to pilot it."""

    package_id: str
    format_id: str
    deck: ArtifactRef
    format_metadata: Mapping[str, Any] = field(default_factory=dict)
    deck_knowledge: Optional[ArtifactRef] = None
    deterministic_policy: Optional[ArtifactRef] = None
    generalist_base: Optional[ArtifactRef] = None
    specialist: Optional[ArtifactRef] = None
    training_generation: Optional[str] = None
    training_provenance: Tuple[str, ...] = ()
    evaluation_refs: Tuple[str, ...] = ()
    metapool_refs: Tuple[str, ...] = ()
    compatibility: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = DECK_PACKAGE_SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != DECK_PACKAGE_SCHEMA_VERSION:
            raise DeckPackageError(
                f"unsupported DeckPackage schema_version={self.schema_version}; "
                f"expected {DECK_PACKAGE_SCHEMA_VERSION}"
            )
        _nonempty(self.package_id, "package_id")
        _nonempty(self.format_id, "format_id")
        if not isinstance(self.format_metadata, Mapping):
            raise DeckPackageError("format_metadata must be a mapping")
        if not isinstance(self.compatibility, Mapping):
            raise DeckPackageError("compatibility must be a mapping")
        if not isinstance(self.metadata, Mapping):
            raise DeckPackageError("metadata must be a mapping")
        self.deck.validate()
        for ref in (
            self.deck_knowledge,
            self.deterministic_policy,
            self.generalist_base,
            self.specialist,
        ):
            if ref is not None:
                ref.validate()

    def canonical_payload(self) -> Dict[str, Any]:
        """Return deterministic semantic identity data, excluding package_id."""
        self.validate()
        raw = asdict(self)
        raw.pop("package_id", None)
        return raw

    def fingerprint(self) -> str:
        """Return a SHA-256 fingerprint for the exact deck/pilot package identity."""
        blob = json.dumps(
            self.canonical_payload(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()

    def same_experiment(self, other: "DeckPackage") -> bool:
        return isinstance(other, DeckPackage) and self.fingerprint() == other.fingerprint()

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        result = asdict(self)
        result["fingerprint"] = self.fingerprint()
        return result

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "DeckPackage":
        if not isinstance(raw, Mapping):
            raise DeckPackageError("DeckPackage payload must be a mapping")
        version = int(raw.get("schema_version", DECK_PACKAGE_SCHEMA_VERSION))
        if version != DECK_PACKAGE_SCHEMA_VERSION:
            raise DeckPackageError(
                f"unsupported DeckPackage schema_version={version}; "
                f"expected {DECK_PACKAGE_SCHEMA_VERSION}"
            )

        package = cls(
            schema_version=version,
            package_id=str(raw.get("package_id", "")),
            format_id=str(raw.get("format_id", "")),
            deck=ArtifactRef.from_dict(raw.get("deck") or {}),
            format_metadata=_mapping(raw.get("format_metadata"), "format_metadata"),
            deck_knowledge=(
                ArtifactRef.from_dict(raw["deck_knowledge"])
                if raw.get("deck_knowledge") is not None
                else None
            ),
            deterministic_policy=(
                ArtifactRef.from_dict(raw["deterministic_policy"])
                if raw.get("deterministic_policy") is not None
                else None
            ),
            generalist_base=(
                ArtifactRef.from_dict(raw["generalist_base"])
                if raw.get("generalist_base") is not None
                else None
            ),
            specialist=(
                ArtifactRef.from_dict(raw["specialist"])
                if raw.get("specialist") is not None
                else None
            ),
            training_generation=(
                str(raw["training_generation"])
                if raw.get("training_generation") is not None
                else None
            ),
            training_provenance=tuple(str(v) for v in raw.get("training_provenance") or ()),
            evaluation_refs=tuple(str(v) for v in raw.get("evaluation_refs") or ()),
            metapool_refs=tuple(str(v) for v in raw.get("metapool_refs") or ()),
            compatibility=_mapping(raw.get("compatibility"), "compatibility"),
            metadata=_mapping(raw.get("metadata"), "metadata"),
        )
        package.validate()
        supplied_fingerprint = raw.get("fingerprint")
        if supplied_fingerprint is not None and str(supplied_fingerprint) != package.fingerprint():
            raise DeckPackageError("DeckPackage fingerprint does not match payload")
        return package


def validate_commander_package(package: DeckPackage) -> None:
    """Validate Commander metadata without making it a core-schema requirement."""

    package.validate()
    if package.format_id.strip().lower() != "commander":
        raise DeckPackageError("Commander validation requires format_id='commander'")
    commander = package.format_metadata.get("commander")
    if not isinstance(commander, str) or not commander.strip():
        raise DeckPackageError(
            "Commander DeckPackage requires non-empty format_metadata['commander']"
        )
