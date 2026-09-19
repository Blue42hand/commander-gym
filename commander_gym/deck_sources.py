"""Pure deck-source normalization and provenance for Commander Gym.

This module deliberately performs no network I/O, source writes, deck legality
checks, or rules evaluation. Providers can feed snapshots into these helpers;
Argentum remains authoritative for legality and game semantics.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import re
from typing import Any, Dict, Mapping, Optional, Tuple

from .deck_package import ArtifactRef

DECK_SOURCE_SCHEMA_VERSION = 1
_ARCHIDEKT_ID = re.compile(r"[1-9][0-9]*")
_NONPLAY_ZONES = {"maybeboard", "sideboard", "companion", "excluded"}
_KNOWN_ZONES = {"main", "commander", *_NONPLAY_ZONES}
_UNSAFE_CARD_NAME = re.compile(r"[\r\n\[\]|\x00]")


class DeckSourceError(ValueError):
    """Raised when a source snapshot is malformed or ambiguous."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DeckSourceError(message)


def _nonempty(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise DeckSourceError(f"{field_name} must be non-empty")
    return text


def _optional_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def _canonical_blob(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise DeckSourceError(f"source metadata is not canonical JSON: {exc}") from exc


@dataclass(frozen=True)
class DeckSourceEntry:
    """One provider relation in a normalized deck-source snapshot."""

    relation_id: str
    name: str
    count: int
    zone: str
    tags: Tuple[str, ...] = ()
    provider_card_id: Optional[str] = None
    oracle_id: Optional[str] = None
    printing_id: Optional[str] = None
    set_code: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        _nonempty(self.relation_id, "entry.relation_id")
        name = _nonempty(self.name, "entry.name")
        if _UNSAFE_CARD_NAME.search(name):
            raise DeckSourceError("entry.name contains unsafe deck-export characters")
        if type(self.count) is not int or self.count <= 0:
            raise DeckSourceError("entry.count must be a positive integer")
        if self.zone not in _KNOWN_ZONES:
            raise DeckSourceError(f"unsupported entry.zone={self.zone!r}")
        if not isinstance(self.tags, tuple):
            raise DeckSourceError("entry.tags must be a tuple")
        if not isinstance(self.metadata, Mapping):
            raise DeckSourceError("entry.metadata must be a mapping")
        _canonical_blob(dict(self.metadata))

    def canonical_payload(self) -> Dict[str, Any]:
        self.validate()
        raw = asdict(self)
        raw["tags"] = sorted(set(str(tag) for tag in self.tags))
        return raw


@dataclass(frozen=True)
class DeckSourceSnapshot:
    """Provider-neutral, immutable identity for one observed deck-source revision."""

    provider: str
    source_id: str
    source_url: str
    name: str
    format_id: str
    entries: Tuple[DeckSourceEntry, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = DECK_SOURCE_SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != DECK_SOURCE_SCHEMA_VERSION:
            raise DeckSourceError(
                f"unsupported DeckSourceSnapshot schema_version={self.schema_version}; "
                f"expected {DECK_SOURCE_SCHEMA_VERSION}"
            )
        _nonempty(self.provider, "provider")
        _nonempty(self.source_id, "source_id")
        _nonempty(self.source_url, "source_url")
        _nonempty(self.name, "name")
        _nonempty(self.format_id, "format_id")
        if not isinstance(self.entries, tuple) or not self.entries:
            raise DeckSourceError("entries must be a non-empty tuple")
        if not isinstance(self.metadata, Mapping):
            raise DeckSourceError("metadata must be a mapping")
        _canonical_blob(dict(self.metadata))
        seen = set()
        for entry in self.entries:
            entry.validate()
            if entry.relation_id in seen:
                raise DeckSourceError(f"duplicate relation_id={entry.relation_id!r}")
            seen.add(entry.relation_id)

    def canonical_payload(self) -> Dict[str, Any]:
        """Return semantic source identity, insensitive to provider row ordering."""
        self.validate()
        return {
            "schema_version": self.schema_version,
            "provider": self.provider,
            "source_id": self.source_id,
            "source_url": self.source_url,
            "name": self.name,
            "format_id": self.format_id,
            "entries": [
                entry.canonical_payload()
                for entry in sorted(self.entries, key=lambda row: row.relation_id)
            ],
            "metadata": dict(self.metadata),
        }

    def fingerprint(self) -> str:
        return hashlib.sha256(_canonical_blob(self.canonical_payload())).hexdigest()

    def to_artifact_ref(self) -> ArtifactRef:
        """Return a DeckPackage-compatible reference to this exact source revision."""
        digest = f"sha256:{self.fingerprint()}"
        ref = ArtifactRef(
            kind="deck",
            artifact_id=f"{self.provider}:{self.source_id}",
            version=digest,
            digest=digest,
            metadata={
                "source_provider": self.provider,
                "source_url": self.source_url,
                "format_id": self.format_id,
                "name": self.name,
                "deck_source_schema": self.schema_version,
            },
        )
        ref.validate()
        return ref


def _archidekt_format(value: Any) -> str:
    normalized = str(value if value is not None else "unknown").strip().casefold()
    if normalized in {"3", "edh", "commander"}:
        return "commander"
    return normalized or "unknown"


def normalize_archidekt_snapshot(
    payload: Mapping[str, Any], *, source_id: str, source_url: str
) -> DeckSourceSnapshot:
    """Normalize an already-fetched Archidekt API or MCP snapshot.

    The caller owns fetching/authentication. This function is intentionally pure
    so private source registries and credentials do not belong in the public repo.
    """

    if not isinstance(payload, Mapping):
        raise DeckSourceError("Archidekt payload must be a mapping")
    source_id = _nonempty(source_id, "source_id")
    _require(bool(_ARCHIDEKT_ID.fullmatch(source_id)), "invalid Archidekt source_id")
    source_url = _nonempty(source_url, "source_url")
    _require(
        source_url.startswith(f"https://archidekt.com/decks/{source_id}/"),
        "Archidekt source_url does not match source_id",
    )

    is_mcp = "remoteId" in payload
    returned_id = payload.get("remoteId" if is_mcp else "id")
    _require(str(returned_id) == source_id, "returned Archidekt deck ID mismatch")

    categories = payload.get("categories")
    _require(isinstance(categories, list), "missing Archidekt categories")
    by_name: Dict[str, Mapping[str, Any]] = {}
    by_id: Dict[str, str] = {}
    for category in categories:
        _require(
            isinstance(category, Mapping) and isinstance(category.get("name"), str),
            "invalid Archidekt category",
        )
        name = str(category["name"])
        key = name.casefold()
        _require(key not in by_name, "ambiguous Archidekt category names")
        by_name[key] = category
        provider_id = category.get("providerCategoryId", category.get("id", f"name:{name}"))
        by_id[str(provider_id)] = name

    rows = payload.get("entries" if is_mcp else "cards")
    _require(isinstance(rows, list) and bool(rows), "missing Archidekt card relations")
    entries = []
    seen_relations = set()
    for row in rows:
        _require(isinstance(row, Mapping), "invalid Archidekt card relation")
        relation = row.get("providerRelationId") if is_mcp else row.get("deckRelationId", row.get("id"))
        _require(relation is not None, "missing Archidekt relation ID")
        relation_id = str(relation)
        _require(relation_id not in seen_relations, "duplicate Archidekt relation ID")
        seen_relations.add(relation_id)

        quantity = row.get("quantity")
        _require(type(quantity) is int and quantity > 0, "invalid Archidekt card quantity")

        raw_categories = row.get("categoryNames" if is_mcp else "categories", [])
        _require(isinstance(raw_categories, list), "invalid Archidekt category membership")
        resolved = []
        for raw_category in raw_categories:
            candidate = (
                raw_category.get("name", raw_category.get("id"))
                if isinstance(raw_category, Mapping)
                else raw_category
            )
            _require(
                isinstance(candidate, (str, int)) and not isinstance(candidate, bool),
                "unknown Archidekt category reference",
            )
            resolved_name = by_id.get(str(candidate), str(candidate))
            resolved_key = resolved_name.casefold()
            _require(
                resolved_key in by_name or resolved_key in {"commander", *_NONPLAY_ZONES},
                "undefined Archidekt category",
            )
            if resolved_name not in resolved:
                resolved.append(resolved_name)

        lower = {name.casefold() for name in resolved}
        exclusions = lower & _NONPLAY_ZONES
        commander = "commander" in lower or any(
            by_name[name].get("isPremier") is True for name in lower if name in by_name
        )
        inclusion_flags = [
            by_name[name].get("includedInDeck") for name in lower if name in by_name
        ]
        _require(not (commander and exclusions), "commander is also in a nonplaying zone")
        _require(len(exclusions) <= 1, "conflicting Archidekt nonplaying zones")
        if commander:
            zone = "commander"
        elif exclusions:
            zone = next(iter(exclusions))
        elif False in inclusion_flags:
            _require(True not in inclusion_flags, "ambiguous Archidekt inclusion flags")
            zone = "excluded"
        else:
            zone = "main"

        if row.get("companion") is True:
            _require(not commander, "companion is also the commander")
            zone = "companion"
        if row.get("deletedAt") is not None:
            _require(not commander, "deleted source relation is designated commander")
            zone = "excluded"

        if is_mcp:
            explicit_zone = row.get("zone")
            _require(explicit_zone in _KNOWN_ZONES, "unknown MCP source zone")
            if explicit_zone in _NONPLAY_ZONES:
                _require(not commander, "MCP commander has a nonplaying zone")
                zone = str(explicit_zone)
            elif explicit_zone == "commander":
                _require(not exclusions, "MCP commander has an excluded category")
                zone = "commander"
            card = row
            oracle = row
            card_name = row.get("cardName")
            printing_id = row.get("printingId")
            oracle_id = row.get("oracleId")
            set_code = row.get("setCode")
            provider_card_id = row.get("providerCardId")
        else:
            card = row.get("card") or {}
            _require(isinstance(card, Mapping), "invalid Archidekt card payload")
            oracle = card.get("oracleCard") or {}
            _require(isinstance(oracle, Mapping), "invalid Archidekt oracle payload")
            card_name = oracle.get("name")
            printing_id = card.get("uid")
            oracle_id = oracle.get("uid")
            set_code = card.get("setCode")
            edition = card.get("edition") or {}
            if not set_code and isinstance(edition, Mapping):
                set_code = edition.get("editioncode")
            provider_card_id = card.get("id")

        card_name = _nonempty(card_name, "Archidekt card name")
        _require(not _UNSAFE_CARD_NAME.search(card_name), "unsafe Archidekt card name")
        entry_metadata = {
            "collector_number": card.get("collectorNumber"),
            "finish": row.get("finish" if is_mcp else "modifier"),
            "language": row.get("language", card.get("language")),
            "primary_category": row.get("primaryCategoryName") if is_mcp else None,
            "notes": row.get("notes"),
            "label": row.get("label"),
            "custom_cmc": row.get("customCmc"),
            "flipped_default": row.get("flippedDefault"),
            "source_deleted": row.get("deletedAt") is not None,
        }
        entries.append(
            DeckSourceEntry(
                relation_id=relation_id,
                name=card_name,
                count=quantity,
                zone=zone,
                tags=tuple(resolved),
                provider_card_id=_optional_text(provider_card_id),
                oracle_id=_optional_text(oracle_id),
                printing_id=_optional_text(printing_id),
                set_code=_optional_text(set_code),
                metadata=entry_metadata,
            )
        )

    name = _nonempty(payload.get("name"), "Archidekt deck name")
    description = payload.get("description") or ""
    _require(isinstance(description, str), "invalid Archidekt deck description")
    snapshot = DeckSourceSnapshot(
        provider="archidekt",
        source_id=source_id,
        source_url=source_url,
        name=name,
        format_id=_archidekt_format(payload.get("format" if is_mcp else "deckFormat")),
        entries=tuple(entries),
        metadata={
            "description": description,
            "source_tags": payload.get("tags") or [],
            "origin": "external-deck-source",
        },
    )
    snapshot.validate()
    return snapshot
