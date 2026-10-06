"""Constrained access to immutable public Scryfall catalog snapshots.

This boundary accepts a configured catalog root, never a caller-supplied path.
It intentionally has no deck, game, experiment, or network access.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from .card_catalog import CardCatalog, CatalogError


_SNAPSHOT_ID = re.compile(r"[0-9a-f]{24}\Z")


class CatalogAccess:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve(strict=True)

    def _current_id(self) -> str:
        pointer = self.root / "current.json"
        if pointer.is_symlink() or not pointer.is_file():
            raise CatalogError("catalog pointer unavailable")
        try:
            data = json.loads(pointer.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CatalogError("catalog pointer unavailable") from exc
        snapshot_id = data.get("snapshot_id") if isinstance(data, dict) else None
        if not isinstance(snapshot_id, str) or not _SNAPSHOT_ID.fullmatch(snapshot_id):
            raise CatalogError("invalid catalog pointer")
        return snapshot_id

    def _catalog(self, snapshot_id: str) -> CardCatalog:
        if not isinstance(snapshot_id, str) or not _SNAPSHOT_ID.fullmatch(snapshot_id):
            raise CatalogError("invalid snapshot_id")
        snapshots = self.root / "snapshots"
        if snapshots.is_symlink() or not snapshots.is_dir():
            raise CatalogError("catalog snapshots unavailable")
        database = snapshots / f"{snapshot_id}.sqlite"
        if database.is_symlink() or not database.is_file():
            raise CatalogError("snapshot unavailable")
        return CardCatalog(database, snapshot_id)

    def catalog_status(self, snapshot_id: str | None = None) -> dict[str, Any]:
        current = self._current_id()
        selected = snapshot_id if snapshot_id is not None else current
        result = self._catalog(selected).catalog_status()
        result["current_snapshot_id"] = current
        result["is_current"] = selected == current
        result["rules_authority"] = "Argentum; catalog data is discovery and implementation evidence"
        return result

    def search_tags(self, snapshot_id: str, query: str, *, kind: str = "oracle",
                    limit: int = 20, cursor: str | None = None) -> dict[str, Any]:
        return self._catalog(snapshot_id).search_tags(query, kind=kind, limit=limit, cursor=cursor)

    def search_cards(self, snapshot_id: str, **filters: Any) -> dict[str, Any]:
        return self._catalog(snapshot_id).search_cards(**filters)

    def get_card(self, snapshot_id: str, *, oracle_id: str | None = None,
                 printing_id: str | None = None) -> dict[str, Any] | None:
        return self._catalog(snapshot_id).get_card(oracle_id=oracle_id, printing_id=printing_id)

    def resolve_exact_name(self, snapshot_id: str, name: str,
                           oracle_id: str | None = None) -> str | None:
        return self._catalog(snapshot_id).resolve_exact_name(name, oracle_id)
