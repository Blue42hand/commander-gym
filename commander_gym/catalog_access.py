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
from .engine_coverage import load_evidence, lookup, metadata


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

    def search_cards(self, snapshot_id: str, *, registered_in: str | None = None,
                     **filters: Any) -> dict[str, Any]:
        if '_registry_filter' in filters:
            raise CatalogError('registry filter must use a pinned coverage_id')
        catalog = self._catalog(snapshot_id)
        payload = None
        if registered_in is not None:
            payload = load_evidence(self.root, registered_in, catalog)
            filters['_registry_filter'] = (registered_in, tuple(
                key for key, entry in payload['cards'].items()
                if entry['registry_presence'] == 'present'))
        result = catalog.search_cards(**filters)
        if payload is not None:
            result['engine_coverage'] = metadata(registered_in, payload)
        return result

    def get_engine_coverage(self, snapshot_id: str, oracle_id: str,
                            coverage_id: str | None = None) -> dict[str, Any]:
        catalog = self._catalog(snapshot_id)
        payload = None if coverage_id is None else load_evidence(self.root, coverage_id, catalog)
        return lookup(catalog, oracle_id, coverage_id, payload)

    def list_engine_coverage(self, snapshot_id: str) -> dict[str, Any]:
        """Discover immutable evidence IDs without choosing a preferred revision."""
        catalog = self._catalog(snapshot_id)
        directory = self.root / 'engine-coverage'
        if directory.is_symlink():
            raise CatalogError('coverage directory unavailable')
        paths = sorted(directory.glob('*.json')) if directory.is_dir() else []
        if len(paths) > 50:
            raise CatalogError('too many coverage artifacts; operator retention review required')
        evidence = []
        for path in paths:
            # A snapshot mismatch is expected when older evidence is retained.
            from .engine_coverage import EVIDENCE_ID, _json, _read
            if not EVIDENCE_ID.fullmatch(path.stem):
                raise CatalogError('invalid coverage artifact name')
            artifact = _json(_read(path))
            if not isinstance(artifact.get('evidence'), dict):
                raise CatalogError('invalid coverage artifact')
            if artifact['evidence'].get('snapshot_id') == snapshot_id:
                evidence.append(metadata(path.stem, load_evidence(self.root, path.stem, catalog)))
        return {'snapshot_id': snapshot_id, 'engine_coverage': evidence,
                'selection_required': True}

    def get_card(self, snapshot_id: str, *, oracle_id: str | None = None,
                 printing_id: str | None = None) -> dict[str, Any] | None:
        return self._catalog(snapshot_id).get_card(oracle_id=oracle_id, printing_id=printing_id)
