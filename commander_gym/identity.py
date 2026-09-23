"""Canonical versioned identities for Commander Gym seat configuration.

This module defines the long-term Deck / DeckKnowledge / Pilot / Binding split.
The legacy :mod:`commander_gym.deck_package` v2 schema remains readable only
through an explicit compatibility migration boundary.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Any, Dict, Mapping, Optional, Tuple

from .deck_package import ArtifactRef, DeckPackage, DECK_PACKAGE_SCHEMA_VERSION

IDENTITY_SCHEMA_VERSION = 1


class IdentityError(ValueError):
    """Raised when a canonical identity payload is malformed or unsupported."""


def _nonempty(value: str, field_name: str) -> str:
    value = str(value or "").strip()
    if not value:
        raise IdentityError(f"{field_name} must be non-empty")
    return value


def _mapping(value: Any, field_name: str) -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise IdentityError(f"{field_name} must be a mapping")
    return dict(value)


def _fingerprint(payload: Mapping[str, Any]) -> str:
    blob = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _verify_fingerprint(raw: Mapping[str, Any], actual: str, label: str) -> None:
    supplied = raw.get("fingerprint")
    if supplied is not None and str(supplied) != actual:
        raise IdentityError(f"{label} fingerprint does not match payload")


@dataclass(frozen=True)
class IdentityRef:
    """Immutable reference to one exact canonical artifact revision."""

    artifact_type: str
    artifact_id: str
    revision: str
    fingerprint: str

    def validate(self) -> None:
        _nonempty(self.artifact_type, "identity_ref.artifact_type")
        _nonempty(self.artifact_id, "identity_ref.artifact_id")
        _nonempty(self.revision, "identity_ref.revision")
        fingerprint = _nonempty(self.fingerprint, "identity_ref.fingerprint")
        if len(fingerprint) != 64 or any(
            c not in "0123456789abcdef" for c in fingerprint.lower()
        ):
            raise IdentityError(
                "identity_ref.fingerprint must be a SHA-256 hex digest"
            )

    def to_dict(self) -> Dict[str, str]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "IdentityRef":
        if not isinstance(raw, Mapping):
            raise IdentityError("identity_ref must be a mapping")
        ref = cls(
            artifact_type=str(raw.get("artifact_type", "")),
            artifact_id=str(raw.get("artifact_id", "")),
            revision=str(raw.get("revision", "")),
            fingerprint=str(raw.get("fingerprint", "")),
        )
        ref.validate()
        return ref


@dataclass(frozen=True)
class Deck:
    """One exact playable deck revision, independent of any pilot."""

    deck_id: str
    revision: str
    format_id: str
    deck_artifact: ArtifactRef
    format_metadata: Mapping[str, Any] = field(default_factory=dict)
    source_metadata: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = IDENTITY_SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != IDENTITY_SCHEMA_VERSION:
            raise IdentityError(
                f"unsupported Deck schema_version={self.schema_version}; "
                f"expected {IDENTITY_SCHEMA_VERSION}"
            )
        _nonempty(self.deck_id, "deck_id")
        _nonempty(self.revision, "revision")
        _nonempty(self.format_id, "format_id")
        self.deck_artifact.validate()
        _mapping(self.format_metadata, "format_metadata")
        _mapping(self.source_metadata, "source_metadata")
        _mapping(self.metadata, "metadata")

    def canonical_payload(self) -> Dict[str, Any]:
        self.validate()
        return asdict(self)

    def fingerprint(self) -> str:
        return _fingerprint(self.canonical_payload())

    def ref(self) -> IdentityRef:
        return IdentityRef("deck", self.deck_id, self.revision, self.fingerprint())

    def to_dict(self) -> Dict[str, Any]:
        result = self.canonical_payload()
        result["fingerprint"] = self.fingerprint()
        return result

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Deck":
        if not isinstance(raw, Mapping):
            raise IdentityError("Deck payload must be a mapping")
        deck = cls(
            schema_version=int(raw.get("schema_version", IDENTITY_SCHEMA_VERSION)),
            deck_id=str(raw.get("deck_id", "")),
            revision=str(raw.get("revision", "")),
            format_id=str(raw.get("format_id", "")),
            deck_artifact=ArtifactRef.from_dict(raw.get("deck_artifact") or {}),
            format_metadata=_mapping(raw.get("format_metadata"), "format_metadata"),
            source_metadata=_mapping(raw.get("source_metadata"), "source_metadata"),
            metadata=_mapping(raw.get("metadata"), "metadata"),
        )
        deck.validate()
        _verify_fingerprint(raw, deck.fingerprint(), "Deck")
        return deck


@dataclass(frozen=True)
class DeckKnowledge:
    """Versioned knowledge about one deck lineage or compatible revisions."""

    knowledge_id: str
    revision: str
    content: ArtifactRef
    compatibility: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = IDENTITY_SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != IDENTITY_SCHEMA_VERSION:
            raise IdentityError(
                f"unsupported DeckKnowledge schema_version={self.schema_version}; "
                f"expected {IDENTITY_SCHEMA_VERSION}"
            )
        _nonempty(self.knowledge_id, "knowledge_id")
        _nonempty(self.revision, "revision")
        self.content.validate()
        _mapping(self.compatibility, "compatibility")
        _mapping(self.metadata, "metadata")

    def canonical_payload(self) -> Dict[str, Any]:
        self.validate()
        return asdict(self)

    def fingerprint(self) -> str:
        return _fingerprint(self.canonical_payload())

    def ref(self) -> IdentityRef:
        return IdentityRef(
            "deck_knowledge", self.knowledge_id, self.revision, self.fingerprint()
        )

    def to_dict(self) -> Dict[str, Any]:
        result = self.canonical_payload()
        result["fingerprint"] = self.fingerprint()
        return result

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "DeckKnowledge":
        if not isinstance(raw, Mapping):
            raise IdentityError("DeckKnowledge payload must be a mapping")
        knowledge = cls(
            schema_version=int(raw.get("schema_version", IDENTITY_SCHEMA_VERSION)),
            knowledge_id=str(raw.get("knowledge_id", "")),
            revision=str(raw.get("revision", "")),
            content=ArtifactRef.from_dict(raw.get("content") or {}),
            compatibility=_mapping(raw.get("compatibility"), "compatibility"),
            metadata=_mapping(raw.get("metadata"), "metadata"),
        )
        knowledge.validate()
        _verify_fingerprint(raw, knowledge.fingerprint(), "DeckKnowledge")
        return knowledge


@dataclass(frozen=True)
class Pilot:
    """The complete versioned decision-making system that can occupy a seat."""

    pilot_id: str
    revision: str
    deterministic_policy: Optional[ArtifactRef] = None
    skill_set: Optional[ArtifactRef] = None
    generalist_base: Optional[ArtifactRef] = None
    specialists: Tuple[ArtifactRef, ...] = ()
    escalation_provider: Optional[ArtifactRef] = None
    routing_config: Mapping[str, Any] = field(default_factory=dict)
    compatibility: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = IDENTITY_SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != IDENTITY_SCHEMA_VERSION:
            raise IdentityError(
                f"unsupported Pilot schema_version={self.schema_version}; "
                f"expected {IDENTITY_SCHEMA_VERSION}"
            )
        _nonempty(self.pilot_id, "pilot_id")
        _nonempty(self.revision, "revision")
        for ref in (
            self.deterministic_policy,
            self.skill_set,
            self.generalist_base,
            self.escalation_provider,
        ):
            if ref is not None:
                ref.validate()
        for ref in self.specialists:
            if not isinstance(ref, ArtifactRef):
                raise IdentityError("specialists must contain ArtifactRef values")
            ref.validate()
        _mapping(self.routing_config, "routing_config")
        _mapping(self.compatibility, "compatibility")
        _mapping(self.metadata, "metadata")

    def canonical_payload(self) -> Dict[str, Any]:
        self.validate()
        return asdict(self)

    def fingerprint(self) -> str:
        return _fingerprint(self.canonical_payload())

    def ref(self) -> IdentityRef:
        return IdentityRef("pilot", self.pilot_id, self.revision, self.fingerprint())

    def to_dict(self) -> Dict[str, Any]:
        result = self.canonical_payload()
        result["fingerprint"] = self.fingerprint()
        return result

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Pilot":
        if not isinstance(raw, Mapping):
            raise IdentityError("Pilot payload must be a mapping")

        def optional_ref(name: str) -> Optional[ArtifactRef]:
            value = raw.get(name)
            return ArtifactRef.from_dict(value) if value is not None else None

        pilot = cls(
            schema_version=int(raw.get("schema_version", IDENTITY_SCHEMA_VERSION)),
            pilot_id=str(raw.get("pilot_id", "")),
            revision=str(raw.get("revision", "")),
            deterministic_policy=optional_ref("deterministic_policy"),
            skill_set=optional_ref("skill_set"),
            generalist_base=optional_ref("generalist_base"),
            specialists=tuple(
                ArtifactRef.from_dict(value)
                for value in raw.get("specialists") or ()
            ),
            escalation_provider=optional_ref("escalation_provider"),
            routing_config=_mapping(raw.get("routing_config"), "routing_config"),
            compatibility=_mapping(raw.get("compatibility"), "compatibility"),
            metadata=_mapping(raw.get("metadata"), "metadata"),
        )
        pilot.validate()
        _verify_fingerprint(raw, pilot.fingerprint(), "Pilot")
        return pilot


@dataclass(frozen=True)
class Binding:
    """Immutable exact Deck + DeckKnowledge + Pilot combination for one seat."""

    binding_id: str
    revision: str
    deck: IdentityRef
    pilot: IdentityRef
    deck_knowledge: Optional[IdentityRef] = None
    overrides: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = IDENTITY_SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != IDENTITY_SCHEMA_VERSION:
            raise IdentityError(
                f"unsupported Binding schema_version={self.schema_version}; "
                f"expected {IDENTITY_SCHEMA_VERSION}"
            )
        _nonempty(self.binding_id, "binding_id")
        _nonempty(self.revision, "revision")
        self.deck.validate()
        self.pilot.validate()
        if self.deck.artifact_type != "deck":
            raise IdentityError("binding.deck must reference artifact_type='deck'")
        if self.pilot.artifact_type != "pilot":
            raise IdentityError("binding.pilot must reference artifact_type='pilot'")
        if self.deck_knowledge is not None:
            self.deck_knowledge.validate()
            if self.deck_knowledge.artifact_type != "deck_knowledge":
                raise IdentityError(
                    "binding.deck_knowledge must reference "
                    "artifact_type='deck_knowledge'"
                )
        _mapping(self.overrides, "overrides")
        _mapping(self.metadata, "metadata")

    def canonical_payload(self) -> Dict[str, Any]:
        self.validate()
        raw = asdict(self)
        # binding_id is a human/lineage label; the fingerprint identifies the
        # exact versioned component combination and binding overrides.
        raw.pop("binding_id", None)
        return raw

    def fingerprint(self) -> str:
        return _fingerprint(self.canonical_payload())

    def ref(self) -> IdentityRef:
        return IdentityRef(
            "binding", self.binding_id, self.revision, self.fingerprint()
        )

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["fingerprint"] = self.fingerprint()
        return result

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Binding":
        if not isinstance(raw, Mapping):
            raise IdentityError("Binding payload must be a mapping")
        knowledge = raw.get("deck_knowledge")
        binding = cls(
            schema_version=int(raw.get("schema_version", IDENTITY_SCHEMA_VERSION)),
            binding_id=str(raw.get("binding_id", "")),
            revision=str(raw.get("revision", "")),
            deck=IdentityRef.from_dict(raw.get("deck") or {}),
            pilot=IdentityRef.from_dict(raw.get("pilot") or {}),
            deck_knowledge=(
                IdentityRef.from_dict(knowledge) if knowledge is not None else None
            ),
            overrides=_mapping(raw.get("overrides"), "overrides"),
            metadata=_mapping(raw.get("metadata"), "metadata"),
        )
        binding.validate()
        _verify_fingerprint(raw, binding.fingerprint(), "Binding")
        return binding


@dataclass(frozen=True)
class LegacyDeckPackageMigration:
    """Explicit compatibility result for one legacy DeckPackage v2 payload."""

    deck: Deck
    deck_knowledge: Optional[DeckKnowledge]
    pilot: Pilot
    binding: Binding


def migrate_deck_package_v2(package: DeckPackage) -> LegacyDeckPackageMigration:
    """Split one legacy DeckPackage v2 into canonical identities.

    This reader is intentionally bounded to schema v2. It does not infer
    cross-package lineage that the old mixed schema never represented.
    New evidence should be authored directly with the canonical identities.
    """

    if not isinstance(package, DeckPackage):
        raise IdentityError("legacy migration requires a DeckPackage")
    package.validate()
    if package.schema_version != DECK_PACKAGE_SCHEMA_VERSION:
        raise IdentityError(
            f"legacy migration only supports DeckPackage "
            f"v{DECK_PACKAGE_SCHEMA_VERSION}"
        )

    deck = Deck(
        deck_id=package.deck.artifact_id,
        revision=package.deck.version,
        format_id=package.format_id,
        deck_artifact=package.deck,
        format_metadata=dict(package.format_metadata),
        source_metadata={"migration_source": "deck-package-v2"},
    )

    knowledge = None
    if package.deck_knowledge is not None:
        knowledge = DeckKnowledge(
            knowledge_id=package.deck_knowledge.artifact_id,
            revision=package.deck_knowledge.version,
            content=package.deck_knowledge,
        )

    pilot_component_payload = {
        "deterministic_policy": (
            asdict(package.deterministic_policy)
            if package.deterministic_policy is not None
            else None
        ),
        "generalist_base": (
            asdict(package.generalist_base)
            if package.generalist_base is not None
            else None
        ),
        "specialist": (
            asdict(package.specialist) if package.specialist is not None else None
        ),
        "compatibility": dict(package.compatibility),
    }
    legacy_pilot_key = _fingerprint(pilot_component_payload)
    pilot = Pilot(
        pilot_id=f"legacy-pilot-{legacy_pilot_key[:16]}",
        revision=package.training_generation or "deck-package-v2",
        deterministic_policy=package.deterministic_policy,
        generalist_base=package.generalist_base,
        specialists=((package.specialist,) if package.specialist is not None else ()),
        compatibility=dict(package.compatibility),
        metadata={"migration_source": "deck-package-v2"},
    )

    binding = Binding(
        binding_id=package.package_id,
        revision="deck-package-v2",
        deck=deck.ref(),
        deck_knowledge=(knowledge.ref() if knowledge is not None else None),
        pilot=pilot.ref(),
        metadata={
            "migration_source": "deck-package-v2",
            "legacy_fingerprint": package.fingerprint(),
        },
    )

    return LegacyDeckPackageMigration(
        deck=deck,
        deck_knowledge=knowledge,
        pilot=pilot,
        binding=binding,
    )
