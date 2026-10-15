"""Validate and render a browsable, instance-owned canonical deck collection.

The collection catalog extends Binding catalog v1 with explicit ``collection``
entries. It is a selection index, never a replacement identity or a legality /
engine-coverage assertion. No providers, credentials, or games are invoked.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping

from .binding_catalog import load_binding_catalog, _inside_root, _read_verified_artifact
from .identity import Binding, Deck, DeckKnowledge, Pilot
from .binding_resolver import BindingResolver
from .game_server_bindings import GameServerBindingRegistry


class CollectionError(ValueError):
    """The collection does not describe exact, consistent artifacts."""


class _ValidationPilot:
    name = "collection-validator"
    version = "1"

    def choose(self, observation: Mapping[str, Any]):
        raise CollectionError("collection validation must never request a decision")


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CollectionError(f"expected a JSON object: {path}")
    return value


def parse_card_list(text: str) -> dict[str, int]:
    """Read plain names or quantity-prefixed lists with optional category tags.

    This explicit migration reader preserves full split-card names and combines
    repeated names. It does not resolve aliases, fix legality, or add cards.
    """
    cards: Counter[str] = Counter()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        match = re.fullmatch(r"(?:(\d+)\s+)?(.+?)(?:\s+\[[^\[\]]+\])?", line)
        if match is None:
            raise CollectionError(f"invalid card-list line: {line!r}")
        count = int(match[1] or 1)
        name = match[2].strip()
        if count < 1 or not name:
            raise CollectionError(f"invalid card-list line: {line!r}")
        cards[name] += count
    return dict(cards)


def render_card_list(payload: Mapping[str, Any]) -> str:
    commander = payload["commander"]
    cards = payload["cards"]
    lines = [f"{cards[commander]} {commander} [Commander{{top}}]"]
    lines.extend(f"{cards[name]} {name} [Main]" for name in sorted(cards) if name != commander)
    return "\n".join(lines) + "\n"


def _relative(target: str, directory: str) -> str:
    return os.path.relpath(target, directory).replace(os.sep, "/")


def _label(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _link(label: str, target: str, directory: str) -> str:
    return f"[{_label(label)}]({_relative(target, directory)})"


def _check_payload(payload: dict[str, Any], deck: Deck) -> None:
    cards = payload.get("cards")
    if not isinstance(cards, dict) or not cards:
        raise CollectionError(f"{deck.deck_id}: cards must be a non-empty mapping")
    if any(not isinstance(n, str) or not n.strip() or type(q) is not int or q < 1
           for n, q in cards.items()):
        raise CollectionError(f"{deck.deck_id}: invalid card name or quantity")
    if sum(cards.values()) != 100:
        raise CollectionError(f"{deck.deck_id}: expected 100 Commander slots")
    commanders = deck.format_metadata.get("commanders")
    if (deck.format_id != "commander" or not isinstance(commanders, list)
            or len(commanders) != 1 or payload.get("commander") != commanders[0]
            or cards.get(commanders[0]) != 1 or payload.get("deck_id") != deck.deck_id
            or payload.get("schema_version") != 1):
        raise CollectionError(f"{deck.deck_id}: inconsistent single-commander payload")


def _check_knowledge(knowledge: DeckKnowledge, deck: Deck) -> None:
    compatibility = knowledge.compatibility
    revisions = compatibility.get("deck_revisions")
    if (compatibility.get("deck_id") != deck.deck_id
            or not isinstance(revisions, list) or deck.revision not in revisions):
        raise CollectionError(f"{deck.deck_id}: primer does not explicitly cover this revision")


def collection_views(root: Path, raw: dict[str, Any]) -> dict[str, str]:
    """Produce deterministic navigation from explicit catalog selections."""
    views: dict[str, str] = {}
    rows = ["# Deck collection", "", "Every row selects an exact Deck, DeckKnowledge and Binding revision.",
            "Card coverage and game qualification are separate from artifact validity.", "",
            "| Deck | Commander | Collection | Cards | Primer | Binding | Coverage | Qualification |",
            "|---|---|---|---|---|---|---|---|"]
    for entry in sorted(raw["collection"], key=lambda e: e["name"].casefold()):
        directory = entry["directory"]
        deck = Deck.from_dict(read_json(root / entry["deck"]["manifest"]))
        knowledge = DeckKnowledge.from_dict(read_json(root / entry["deck_knowledge"]["manifest"]))
        binding = Binding.from_dict(read_json(root / entry["binding"]))
        payload = read_json(root / entry["deck"]["payload"])
        card_list = render_card_list(payload)
        views[entry["deck"]["card_list"]] = card_list
        index = {"schema_version": 1, "name": entry["name"], "commander": payload["commander"],
                 "collection_status": entry["status"], "selected_deck": deck.ref().to_dict(),
                 "selected_knowledge": knowledge.ref().to_dict(), "selected_binding": binding.ref().to_dict(),
                 "paths": {"deck": entry["deck"], "deck_knowledge": entry["deck_knowledge"],
                           "binding": entry["binding"]}, "coverage": entry["coverage"],
                 "qualification": entry["qualification"], "sources": entry["sources"],
                 "alternate_knowledge": entry.get("alternate_knowledge", [])}
        views[f"{directory}/index.json"] = json.dumps(index, indent=2, ensure_ascii=False) + "\n"
        readme = [f"# {entry['name']}", "", f"Commander: **{payload['commander']}**. Collection: **{entry['status']}**.", "",
                  f"{_link('Card list', entry['deck']['card_list'], directory)} · "
                  f"{_link('Primer', entry['deck_knowledge']['payload'], directory)} · "
                  f"{_link('Binding', entry['binding'], directory)} · "
                  f"{_link('All decks', 'decks/README.md', directory)}", "",
                  f"Deck: `{deck.deck_id}@{deck.revision}` (100 cards including commander).",
                  f"Primer: `{knowledge.knowledge_id}@{knowledge.revision}`.",
                  f"Pilot: `{binding.pilot.artifact_id}@{binding.pilot.revision}`.", "",
                  f"Coverage: **{entry['coverage']['status']}**. {entry['coverage']['detail']}", "",
                  f"Qualification: **{entry['qualification']['status']}**. {entry['qualification']['detail']}", "",
                  "## Exact artifacts", "",
                  f"- {_link('Deck manifest', entry['deck']['manifest'], directory)}",
                  f"- {_link('Deck payload', entry['deck']['payload'], directory)}",
                  f"- {_link('Knowledge manifest', entry['deck_knowledge']['manifest'], directory)}", "",
                  "## Sources and history", ""]
        readme.extend(f"- {_link(path, path, directory)}" for path in entry["sources"])
        for alternative in entry.get("alternate_knowledge", []):
            readme.append(f"- {_link('Alternate primer: ' + alternative['reason'], alternative['payload'], directory)}")
        readme += ["", "Selections are explicit in the collection catalog; file dates and directory names do not select revisions.",
                   "The active roster is selected separately under `rosters/`. A binding does not establish engine coverage or gameplay qualification.", ""]
        views[f"{directory}/README.md"] = "\n".join(readme)
        rows.append("| " + " | ".join([
            _link(entry["name"], f"{directory}/README.md", "decks"), _label(payload["commander"]), entry["status"],
            _link("Cards", entry["deck"]["card_list"], "decks"),
            _link("Primer", entry["deck_knowledge"]["payload"], "decks"),
            _link(binding.binding_id, entry["binding"], "decks"),
            entry["coverage"]["status"], entry["qualification"]["status"]])+" |")
    rows += ["", "The collection catalog is `catalogs/deck-collection.json`. Its advertised bindings are the collection's selections,",
             "not the active instance roster. The active roster is `rosters/foundation-active-four-seat.json`.", "",
             "Generated by `python -m commander_gym.deck_collection --instance-root INSTANCE --write`.", ""]
    views["decks/README.md"] = "\n".join(rows)
    return views


def validate_collection(instance_root: str | Path, *, catalog: str = "catalogs/deck-collection.json",
                        write: bool = False) -> dict[str, int]:
    root = Path(instance_root).resolve()
    catalog_path = _inside_root(root, catalog, "catalog")
    raw = read_json(catalog_path)
    if raw.get("collection_schema_version") != 1 or not isinstance(raw.get("collection"), list) or not raw["collection"]:
        raise CollectionError("expected non-empty collection schema v1")
    loaded = load_binding_catalog(catalog_path, instance_root=root,
                                  pilot_factory=lambda pilot, binding: _ValidationPilot())
    # The production Argentum profile path must accept every advertised payload.
    GameServerBindingRegistry(loaded.resolver, loaded.binding_ids)
    seen_ids: set[str] = set()
    seen_directories: set[str] = set()
    selected_bindings: set[str] = set()
    selected_decks: list[dict[str, str]] = []
    selected_knowledge: list[dict[str, str]] = []
    selected_binding_paths: list[str] = []
    canonical_paths: set[Path] = set()
    for entry in raw["collection"]:
        directory = entry["directory"]
        directory_path = _inside_root(root, directory, "deck directory")
        if directory_path.parent != root / "decks" or directory in seen_directories:
            raise CollectionError("deck directories must be unique direct children of decks/")
        seen_directories.add(directory)
        if entry["status"] not in {"active", "historical", "imported"}:
            raise CollectionError("invalid collection status")
        for key in ("coverage", "qualification"):
            if not entry[key].get("status") or not entry[key].get("detail"):
                raise CollectionError(f"missing {key} status/detail")
        for key in ("deck", "deck_knowledge"):
            for path in entry[key].values():
                candidate = _inside_root(root, path, key)
                if not candidate.is_relative_to(directory_path):
                    raise CollectionError(f"{key} must live in its deck directory")
        deck = Deck.from_dict(read_json(root / entry["deck"]["manifest"]))
        expected_prefix = f"{directory}/revisions/{deck.revision}"
        if entry["deck"] != {"manifest": expected_prefix + "/manifest.json",
                             "payload": expected_prefix + "/deck.json",
                             "card_list": expected_prefix + "/decklist.txt"}:
            raise CollectionError("deck must use the versioned directory convention")
        if deck.deck_id in seen_ids:
            raise CollectionError("duplicate deck lineage selection")
        seen_ids.add(deck.deck_id)
        _check_payload(read_json(root / entry["deck"]["payload"]), deck)
        knowledge = DeckKnowledge.from_dict(read_json(root / entry["deck_knowledge"]["manifest"]))
        knowledge_prefix = f"{directory}/knowledge/{knowledge.revision}"
        if entry["deck_knowledge"] != {"manifest": knowledge_prefix + "/knowledge.json",
                                      "payload": knowledge_prefix + "/primer.md"}:
            raise CollectionError("primer must use the versioned directory convention")
        _check_knowledge(knowledge, deck)
        binding_path = _inside_root(root, entry["binding"], "binding")
        binding = Binding.from_dict(read_json(binding_path))
        if binding.deck != deck.ref() or binding.deck_knowledge != knowledge.ref():
            raise CollectionError("selected binding differs from the selected deck/primer")
        if binding.binding_id in selected_bindings:
            raise CollectionError("duplicate binding selection")
        if entry["binding"] != f"bindings/{binding.binding_id}/revisions/{binding.revision}/binding.json":
            raise CollectionError("binding must use the versioned directory convention")
        selected_bindings.add(binding.binding_id)
        selected_binding_paths.append(entry["binding"])
        selected_decks.append({k: entry["deck"][k] for k in ("manifest", "payload")})
        selected_knowledge.append(entry["deck_knowledge"])
        canonical_paths.add((root / entry["deck"]["manifest"]).resolve())
        canonical_paths.add((root / entry["deck_knowledge"]["manifest"]).resolve())
        for path in entry["sources"]:
            if not _inside_root(root, path, "source").exists():
                raise CollectionError(f"missing source: {path}")
        for alternate in entry.get("alternate_knowledge", []):
            manifest_path = _inside_root(root, alternate["manifest"], "alternate knowledge")
            alternate_knowledge = DeckKnowledge.from_dict(read_json(manifest_path))
            _check_knowledge(alternate_knowledge, deck)
            _read_verified_artifact(_inside_root(root, alternate["payload"], "alternate primer"),
                                    alternate_knowledge.content, "alternate primer")
            canonical_paths.add(manifest_path)
    if (set(raw["active_bindings"]) != selected_bindings
            or sorted(raw["bindings"]) != sorted(selected_binding_paths)
            or sorted(raw["decks"], key=lambda e: e["manifest"]) != sorted(selected_decks, key=lambda e: e["manifest"])
            or sorted(raw["deck_knowledge"], key=lambda e: e["manifest"]) != sorted(selected_knowledge, key=lambda e: e["manifest"])):
        raise CollectionError("runtime catalog and collection selections differ")
    # Every canonical deck/knowledge revision outside preserved source archives
    # is verified, even when it is not currently selected.
    all_decks = []
    for path in (root / "decks").glob("*/revisions/*/manifest.json"):
        deck = Deck.from_dict(read_json(path))
        if path.parent.name != deck.revision:
            raise CollectionError("deck revision directory differs from identity")
        payload_path = path.with_name("deck.json")
        _read_verified_artifact(payload_path, deck.deck_artifact, "deck revision")
        _check_payload(read_json(payload_path), deck)
        canonical_paths.add(path.resolve())
        all_decks.append(deck)
    by_revision = {(deck.deck_id, deck.revision): deck for deck in all_decks}
    all_knowledge = []
    for path in (root / "decks").glob("*/knowledge/*/knowledge.json"):
        knowledge = DeckKnowledge.from_dict(read_json(path))
        if path.parent.name != knowledge.revision:
            raise CollectionError("primer revision directory differs from identity")
        _read_verified_artifact(path.with_name("primer.md"), knowledge.content, "primer revision")
        revisions = knowledge.compatibility.get("deck_revisions")
        if not isinstance(revisions, list) or not revisions:
            raise CollectionError("stored primer needs exact deck revision compatibility")
        for revision in revisions:
            try:
                compatible_deck = by_revision[(knowledge.compatibility["deck_id"], revision)]
            except KeyError as exc:
                raise CollectionError("stored primer references an unavailable deck revision") from exc
            _check_knowledge(knowledge, compatible_deck)
        canonical_paths.add(path.resolve())
        all_knowledge.append(knowledge)
    all_bindings = []
    for path in (root / "bindings").glob("*/revisions/*/binding.json"):
        binding = Binding.from_dict(read_json(path))
        if str(path.relative_to(root)) != f"bindings/{binding.binding_id}/revisions/{binding.revision}/binding.json":
            raise CollectionError("stored binding path differs from identity")
        all_bindings.append(binding)
    resolver = BindingResolver(bindings=all_bindings, decks=all_decks,
                               pilots=[Pilot.from_dict(read_json(path)) for path in (root / "pilots").glob("*/revisions/*/pilot.json")],
                               deck_knowledge=all_knowledge, deck_payload_loader=lambda ref: {},
                               pilot_factory=lambda pilot, binding: _ValidationPilot())
    for binding in all_bindings:
        resolver.resolve(binding.binding_id, revision=binding.revision)
        if binding.deck_knowledge is not None:
            knowledge = next(k for k in all_knowledge if k.ref() == binding.deck_knowledge)
            _check_knowledge(knowledge, by_revision[(binding.deck.artifact_id, binding.deck.revision)])
    # Reject accidentally introduced parallel top-level layouts or orphan decks.
    actual_directories = {str(p.relative_to(root)) for p in (root / "decks").iterdir() if p.is_dir()}
    if actual_directories != seen_directories:
        raise CollectionError("unindexed deck directories")
    views = collection_views(root, raw)
    for relative, content in views.items():
        path = _inside_root(root, relative, "generated view")
        if write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        elif not path.is_file() or path.read_bytes() != content.encode("utf-8"):
            raise CollectionError(f"stale generated view: {relative}; regenerate with --write")
    # Optional migration ledger proves exact preservation and card equality.
    if raw.get("migration"):
        ledger = read_json(_inside_root(root, raw["migration"], "migration ledger"))
        for item in ledger["preserved_files"]:
            data = _inside_root(root, item["path"], "preserved artifact").read_bytes()
            actual = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
            if actual != item["git_blob"]:
                raise CollectionError(f"changed preserved source: {item['path']}")
        for entry in raw["collection"]:
            source_path = _inside_root(root, entry["card_source"]["path"], "card source")
            if entry["card_source"]["format"] == "text":
                original = parse_card_list(source_path.read_text(encoding="utf-8"))
            elif entry["card_source"]["format"] == "argentum-json":
                original = read_json(source_path)["cards"]
            elif entry["card_source"]["format"] == "archidekt-snapshot":
                original_counts: Counter[str] = Counter()
                for card in read_json(source_path)["entries"]:
                    if card["role"] in {"commander", "main"}:
                        original_counts[card["name"]] += card["count"]
                original = dict(original_counts)
            else:
                raise CollectionError("unknown declared card-source format")
            if original != read_json(root / entry["deck"]["payload"])["cards"]:
                raise CollectionError(f"migration changed card quantities: {entry['name']}")
    rosters = 0
    for path in (root / "rosters").glob("*.json"):
        roster = load_binding_catalog(path, instance_root=root,
                                      pilot_factory=lambda pilot, binding: _ValidationPilot())
        GameServerBindingRegistry(roster.resolver, roster.binding_ids)
        rosters += 1
    return {"decks": len(seen_ids), "bindings": len(selected_bindings),
            "verified_manifests": len(canonical_paths), "generated_views": len(views), "rosters": rosters}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-root", required=True)
    parser.add_argument("--catalog", default="catalogs/deck-collection.json")
    parser.add_argument("--write", action="store_true", help="regenerate card lists and navigation")
    args = parser.parse_args()
    print(json.dumps(validate_collection(args.instance_root, catalog=args.catalog, write=args.write), sort_keys=True))


if __name__ == "__main__":
    main()
