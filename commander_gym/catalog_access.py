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

    def coverage_status(self, snapshot_id: str, coverage_id: str | None = None,
                        expected_engine_sha: str | None = None,
                        expected_gym_sha: str | None = None) -> dict[str, Any]:
        """Compare caller pins; never infer the host's current deployment."""
        from .engine_coverage import HEX_SHA
        for pin in (expected_engine_sha, expected_gym_sha):
            if pin is not None and (not isinstance(pin, str) or not HEX_SHA.fullmatch(pin)):
                raise CatalogError('expected revision must be a full lowercase commit SHA')
        status = self.catalog_status(snapshot_id)
        available = self.list_engine_coverage(snapshot_id)
        result = {'snapshot_id': snapshot_id, 'catalog_is_current': status['is_current'],
                  'available_coverage': available['engine_coverage'],
                  'availability': 'available' if available['engine_coverage'] else 'unavailable',
                  'selection_required': coverage_id is None, 'selected_evidence': None,
                  'engine_pin_match': None, 'gym_pin_match': None,
                  'freshness': 'unknown', 'current_deployed_support': 'unknown',
                  'tested_card_behavior': 'unknown', 'upstream_presence': 'unknown'}
        if coverage_id is not None:
            payload = load_evidence(self.root, coverage_id, self._catalog(snapshot_id))
            result['selected_evidence'] = metadata(coverage_id, payload)
            gym_sha = payload.get('commander_gym', {}).get('commit')
            result['engine_pin_match'] = (None if expected_engine_sha is None else
                                          payload['engine_sha'] == expected_engine_sha)
            result['gym_pin_match'] = (None if expected_gym_sha is None or gym_sha is None else
                                       gym_sha == expected_gym_sha)
            if not status['is_current'] or False in (result['engine_pin_match'], result['gym_pin_match']):
                result['freshness'] = 'stale_for_requested_context'
            elif result['engine_pin_match'] is True and result['gym_pin_match'] is True:
                result['freshness'] = 'matches_requested_pins'
        return result

    def get_deck_coverage(self, snapshot_id: str, oracle_ids: list[str],
                          coverage_id: str | None = None) -> dict[str, Any]:
        """Aggregate caller-supplied slots without accessing or retaining decks."""
        if (not isinstance(oracle_ids, list) or not 1 <= len(oracle_ids) <= 1000 or
                any(not isinstance(key, str) or not 1 <= len(key) <= 128 for key in oracle_ids)):
            raise CatalogError('supply between 1 and 1000 bounded Oracle-ID slots')
        catalog = self._catalog(snapshot_id)
        payload = None if coverage_id is None else load_evidence(self.root, coverage_id, catalog)
        counts = {'present': 0, 'absent': 0, 'unknown': 0}
        legality = {'legal': 0, 'not_legal': 0, 'unknown': 0}
        for key in oracle_ids:
            card = lookup(catalog, key, coverage_id, payload)
            counts[card['registry_presence']] += 1
            legal = card['scryfall_commander_legal']
            legality['unknown' if legal is None else 'legal' if legal else 'not_legal'] += 1
        return {'snapshot_id': snapshot_id, 'coverage_id': coverage_id,
                'total_slots': len(oracle_ids), 'registry_slots': counts,
                'scryfall_commander_legality_slots': legality,
                'registered_slot_percent': 100 * counts['present'] / len(oracle_ids),
                'evidence': None if payload is None else metadata(coverage_id, payload),
                'tested_card_behavior': 'unknown', 'upstream_presence': 'unknown',
                'current_deployed_support': 'unknown',
                'note': 'Counts cover submitted slots, including duplicates. This is registry coverage, '
                        'not deck legality, gameplay testing, or current deployment certification.'}

    def get_card(self, snapshot_id: str, *, oracle_id: str | None = None,
                 printing_id: str | None = None) -> dict[str, Any] | None:
        return self._catalog(snapshot_id).get_card(oracle_id=oracle_id, printing_id=printing_id)
