"""Load an instance-supplied canonical Binding catalog for runtime use.

The catalog is intentionally only an index. Canonical Deck, DeckKnowledge, Pilot,
and Binding manifests remain ordinary public Commander Gym v1 identity payloads,
and the playable deck payload remains a separate artifact. Paths are resolved below
an explicit instance root so owner-specific manifests never need to live in the
public repository.
"""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .binding_resolver import BindingResolutionError, BindingResolver, PilotFactory
from .deck_package import ArtifactRef
from .identity import Binding, Deck, DeckKnowledge, IdentityError, Pilot


BINDING_CATALOG_SCHEMA_VERSION = 1


class BindingCatalogError(BindingResolutionError):
    """Raised when an instance Binding catalog cannot be loaded exactly."""


@dataclass(frozen=True)
class LoadedBindingCatalog:
    """One validated catalog plus the resolver built from its exact artifacts."""

    resolver: BindingResolver
    binding_ids: tuple[str, ...]


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BindingCatalogError(f"{label} must be an object")
    return value


def _require_sequence(value: Any, label: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise BindingCatalogError(f"{label} must be an array")
    return value


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BindingCatalogError(f"{label} must be a non-empty string")
    return value.strip()


def _read_json(path: Path, label: str) -> Mapping[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise BindingCatalogError(f"cannot read {label}: {path}") from exc
    except json.JSONDecodeError as exc:
        raise BindingCatalogError(f"{label} is not valid JSON: {path}") from exc
    return _require_mapping(raw, label)


def _inside_root(root: Path, raw_path: Any, label: str) -> Path:
    relative = Path(_require_string(raw_path, label))
    if relative.is_absolute():
        raise BindingCatalogError(f"{label} must be relative to the instance root")
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise BindingCatalogError(f"{label} escapes the instance root") from exc
    return candidate


def _load_manifests(
    root: Path,
    entries: Any,
    *,
    label: str,
    loader: Any,
) -> list[Any]:
    loaded: list[Any] = []
    for index, raw_path in enumerate(_require_sequence(entries, label)):
        path = _inside_root(root, raw_path, f"{label}[{index}]")
        try:
            loaded.append(loader(_read_json(path, f"{label}[{index}]")))
        except (IdentityError, ValueError) as exc:
            raise BindingCatalogError(f"invalid {label}[{index}]: {exc}") from exc
    return loaded


def _artifact_key(ref: ArtifactRef) -> tuple[str, str, str, str | None]:
    ref.validate()
    return (ref.kind, ref.artifact_id, ref.version, ref.digest)


def _read_verified_artifact(path: Path, ref: ArtifactRef, label: str) -> bytes:
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise BindingCatalogError(f"cannot read {label}: {path}") from exc
    digest = ref.digest or ""
    if digest.startswith("sha256:"):
        actual = "sha256:" + hashlib.sha256(content).hexdigest()
    elif digest.startswith("git-sha1:"):
        actual = "git-sha1:" + hashlib.sha1(
            b"blob " + str(len(content)).encode("ascii") + b"\0" + content
        ).hexdigest()
    else:
        raise BindingCatalogError(f"{label} requires a supported content digest")
    if actual != digest:
        raise BindingCatalogError(f"{label} digest does not match: {path}")
    return content


def load_binding_catalog(
    catalog_path: str | Path,
    *,
    pilot_factory: PilotFactory,
    instance_root: str | Path | None = None,
) -> LoadedBindingCatalog:
    """Load one instance catalog through the canonical identity/resolver path.

    Catalog v1 is deliberately small and path-based::

        {
          "schema_version": 1,
          "bindings": ["bindings/seat-a.json"],
          "decks": [
            {
              "manifest": "decks/a/deck.json",
              "payload": "decks/a/argentum-deck.json"
            }
          ],
          "pilots": ["pilots/a/pilot.json"],
          "deck_knowledge": [
            {
              "manifest": "decks/a/knowledge/knowledge.json",
              "payload": "decks/a/knowledge/primer.md"
            }
          ],
          "active_bindings": ["seat-a"]
        }

    The catalog contains no private implementation hooks. ``pilot_factory`` is the
    public runtime adapter for canonical Pilot manifests, while exact deck payloads
    are ordinary JSON mappings resolved by their Deck ``ArtifactRef``.
    """

    if not callable(pilot_factory):
        raise BindingCatalogError("pilot_factory must be callable")

    catalog = Path(catalog_path).expanduser().resolve()
    root = (
        Path(instance_root).expanduser().resolve()
        if instance_root is not None
        else catalog.parent
    )
    if not root.is_dir():
        raise BindingCatalogError(f"instance root does not exist: {root}")
    try:
        catalog.relative_to(root)
    except ValueError as exc:
        raise BindingCatalogError("catalog must be located below the instance root") from exc

    raw = _read_json(catalog, "Binding catalog")
    try:
        schema_version = int(raw.get("schema_version", 0))
    except (TypeError, ValueError) as exc:
        raise BindingCatalogError("Binding catalog schema_version must be an integer") from exc
    if schema_version != BINDING_CATALOG_SCHEMA_VERSION:
        raise BindingCatalogError(
            f"unsupported Binding catalog schema_version={schema_version}; "
            f"expected {BINDING_CATALOG_SCHEMA_VERSION}"
        )

    bindings = _load_manifests(
        root,
        raw.get("bindings", ()),
        label="bindings",
        loader=Binding.from_dict,
    )
    pilots = _load_manifests(
        root,
        raw.get("pilots", ()),
        label="pilots",
        loader=Pilot.from_dict,
    )
    knowledge: list[DeckKnowledge] = []
    knowledge_payloads: dict[tuple[str, str, str, str | None], str] = {}
    for index, entry in enumerate(
        _require_sequence(raw.get("deck_knowledge", ()), "deck_knowledge")
    ):
        item = _require_mapping(entry, f"deck_knowledge[{index}]")
        manifest_path = _inside_root(
            root, item.get("manifest"), f"deck_knowledge[{index}].manifest"
        )
        payload_path = _inside_root(
            root, item.get("payload"), f"deck_knowledge[{index}].payload"
        )
        try:
            artifact = DeckKnowledge.from_dict(
                _read_json(manifest_path, f"deck_knowledge[{index}].manifest")
            )
        except (IdentityError, ValueError) as exc:
            raise BindingCatalogError(f"invalid deck_knowledge[{index}]: {exc}") from exc
        content = _read_verified_artifact(
            payload_path, artifact.content, f"deck_knowledge[{index}].payload"
        )
        try:
            decoded = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BindingCatalogError("DeckKnowledge content must be UTF-8") from exc
        key = _artifact_key(artifact.content)
        if key in knowledge_payloads:
            raise BindingCatalogError("duplicate DeckKnowledge content artifact")
        knowledge.append(artifact)
        knowledge_payloads[key] = decoded

    deck_entries = _require_sequence(raw.get("decks", ()), "decks")
    decks: list[Deck] = []
    payloads: dict[tuple[str, str, str, str | None], Mapping[str, Any]] = {}
    for index, entry in enumerate(deck_entries):
        item = _require_mapping(entry, f"decks[{index}]")
        manifest_path = _inside_root(
            root,
            item.get("manifest"),
            f"decks[{index}].manifest",
        )
        payload_path = _inside_root(
            root,
            item.get("payload"),
            f"decks[{index}].payload",
        )
        try:
            deck = Deck.from_dict(_read_json(manifest_path, f"decks[{index}].manifest"))
        except (IdentityError, ValueError) as exc:
            raise BindingCatalogError(f"invalid decks[{index}].manifest: {exc}") from exc
        payload = _read_json(payload_path, f"decks[{index}].payload")
        _read_verified_artifact(payload_path, deck.deck_artifact, f"decks[{index}].payload")
        key = _artifact_key(deck.deck_artifact)
        if key in payloads:
            raise BindingCatalogError(
                "duplicate playable payload for Deck artifact "
                f"{deck.deck_artifact.artifact_id!r}@{deck.deck_artifact.version!r}"
            )
        decks.append(deck)
        payloads[key] = dict(payload)

    active_bindings = tuple(
        _require_string(value, f"active_bindings[{index}]")
        for index, value in enumerate(
            _require_sequence(raw.get("active_bindings", ()), "active_bindings")
        )
    )
    if not active_bindings:
        raise BindingCatalogError("active_bindings must not be empty")
    if len(set(active_bindings)) != len(active_bindings):
        raise BindingCatalogError("active_bindings must not contain duplicates")

    def deck_payload_loader(ref: ArtifactRef) -> Mapping[str, Any]:
        key = _artifact_key(ref)
        try:
            return dict(payloads[key])
        except KeyError as exc:
            raise BindingCatalogError(
                "catalog is missing playable payload for Deck artifact "
                f"{ref.artifact_id!r}@{ref.version!r}"
            ) from exc

    def knowledge_payload_loader(ref: ArtifactRef) -> str:
        try:
            return knowledge_payloads[_artifact_key(ref)]
        except KeyError as exc:
            raise BindingCatalogError("catalog is missing exact DeckKnowledge content") from exc

    resolver = BindingResolver(
        bindings=bindings,
        decks=decks,
        pilots=pilots,
        deck_knowledge=knowledge,
        deck_payload_loader=deck_payload_loader,
        pilot_factory=pilot_factory,
        knowledge_payload_loader=knowledge_payload_loader,
    )

    # Resolve advertised Bindings eagerly. Startup must fail rather than publish a
    # stale profile that can only fail after a human selects it in Argentum.
    for binding_id in active_bindings:
        try:
            resolver.resolve(binding_id)
        except BindingResolutionError as exc:
            raise BindingCatalogError(
                f"active Binding {binding_id!r} does not resolve: {exc}"
            ) from exc

    return LoadedBindingCatalog(resolver=resolver, binding_ids=active_bindings)
