"""Ephemeral, fixture-testable EDHREC context comparisons.

No network client is provided here. A future authorized source may implement
``fetch(commander_slug, theme_slug) -> bytes``; the caller owns its transport
timeout and byte cap as well as the reader's in-memory cap below.
"""

from __future__ import annotations

from datetime import datetime
import json
import math
import re
import threading
import time
from typing import Any, Callable
from urllib.parse import urlsplit

from .card_catalog import CatalogError


MAX_BODY_BYTES = 2_000_000
MAX_SOURCE_CARDS = 2_000
MAX_RESULT_CARDS = 50
_SLUG = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*\Z')


def _slug(value: str, field: str) -> str:
    if not isinstance(value, str) or len(value) > 100 or not _SLUG.fullmatch(value):
        raise CatalogError(f'{field} must be a bounded lowercase slug')
    return value


def _count(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CatalogError(f'{field} must be a nonnegative integer or null')
    return value


def _number(value: Any, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise CatalogError(f'{field} must be a finite number or null')
    return float(value)


def parse_context(body: bytes, commander_slug: str, theme_slug: str | None) -> dict[str, Any]:
    """Validate a narrow projection, without silently filling absent values.

    This is the internal adapter envelope, not a claim about EDHREC's live JSON
    shape. Source-specific projection needs a separately reviewed fixture.
    """
    if not isinstance(body, bytes) or len(body) > MAX_BODY_BYTES:
        raise CatalogError('EDHREC context exceeds byte limit')
    try:
        data = json.loads(body)
    except (UnicodeError, ValueError) as exc:
        raise CatalogError('invalid EDHREC context JSON') from exc
    if not isinstance(data, dict) or data.get('commander_slug') != commander_slug or data.get('theme_slug') != theme_slug:
        raise CatalogError('EDHREC context identity mismatch')
    url = data.get('source_url')
    if not isinstance(url, str) or len(url) > 500:
        raise CatalogError('EDHREC source URL unavailable')
    try:
        parsed = urlsplit(url)
        valid_url = (parsed.scheme == 'https' and
                     parsed.hostname in ('edhrec.com', 'json.edhrec.com') and
                     not parsed.username and not parsed.password and not parsed.port and
                     parsed.path.startswith('/'))
    except ValueError:
        valid_url = False
    if not valid_url:
        raise CatalogError('invalid EDHREC source URL')
    retrieved = data.get('retrieved_at')
    try:
        if not isinstance(retrieved, str) or datetime.fromisoformat(retrieved.replace('Z', '+00:00')).tzinfo is None:
            raise ValueError
    except ValueError as exc:
        raise CatalogError('EDHREC retrieval time unavailable') from exc
    rows = data.get('cards')
    if not isinstance(rows, list) or len(rows) > MAX_SOURCE_CARDS:
        raise CatalogError('EDHREC card list exceeds limit')
    cards = []
    for row in rows:
        if not isinstance(row, dict):
            raise CatalogError('invalid EDHREC card row')
        name = row.get('name')
        if not isinstance(name, str) or not name.strip() or len(name) > 150:
            raise CatalogError('invalid EDHREC card name')
        oracle_id = row.get('oracle_id')
        if oracle_id is not None and (not isinstance(oracle_id, str) or len(oracle_id) > 64):
            raise CatalogError('invalid EDHREC Oracle ID')
        inclusion = _count(row.get('inclusion_count'), 'inclusion_count')
        potential = _count(row.get('potential_decks'), 'potential_decks')
        if inclusion is not None and potential is not None and inclusion > potential:
            raise CatalogError('EDHREC inclusion count exceeds denominator')
        quantity = _number(row.get('average_quantity'), 'average_quantity')
        if quantity is not None and quantity < 0:
            raise CatalogError('average_quantity must be nonnegative')
        lift = _number(row.get('lift_ratio'), 'lift_ratio')
        if lift is not None and lift < 0:
            raise CatalogError('lift_ratio must be nonnegative')
        cards.append({'name': name, 'oracle_id': oracle_id,
                      'inclusion_count': inclusion, 'potential_decks': potential,
                      'average_quantity': quantity,
                      'lift_ratio': lift,
                      'synergy_percent': _number(row.get('synergy_percent'), 'synergy_percent')})
    return {'commander_slug': commander_slug, 'theme_slug': theme_slug,
            'source_url': url, 'retrieved_at': retrieved, 'cards': cards}


class ContextReader:
    """Serial, spaced, no-retry access through an explicitly injected source."""

    def __init__(self, fetch: Callable[[str, str | None], bytes], *,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.fetch, self.clock, self.sleep = fetch, clock, sleep
        self._lock = threading.Lock()
        self._next_at = 0.0
        self._stopped = False

    def read(self, commander_slug: str, theme_slug: str | None) -> dict[str, Any]:
        _slug(commander_slug, 'commander_slug')
        if theme_slug is not None:
            _slug(theme_slug, 'theme_slug')
        with self._lock:
            if self._stopped:
                raise CatalogError('EDHREC access stopped after restriction response')
            delay = max(0.0, self._next_at - self.clock())
            if delay:
                self.sleep(delay)
            self._next_at = self.clock() + 2.0
            try:
                body = self.fetch(commander_slug, theme_slug)
            except SourceHTTPError as exc:
                if exc.status in (403, 429):
                    self._stopped = True
                    raise CatalogError('EDHREC access stopped after restriction response') from exc
                raise CatalogError('EDHREC context unavailable') from exc
            except Exception as exc:
                raise CatalogError('EDHREC context unavailable') from exc
            return parse_context(body, commander_slug, theme_slug)


class SourceHTTPError(Exception):
    def __init__(self, status: int):
        self.status = status
        super().__init__(f'HTTP {status}')


def compare_contexts(reader: ContextReader, commander_slug: str,
                     theme_a: str | None, theme_b: str | None,
                     resolve: Callable[[str, str | None], str | None], *,
                     limit: int = 20) -> dict[str, Any]:
    """Compare two observed populations; never infer theme complements."""
    _slug(commander_slug, 'commander_slug')
    for label, theme in (('theme_a', theme_a), ('theme_b', theme_b)):
        if theme is not None:
            _slug(theme, label)
    if theme_a == theme_b:
        raise CatalogError('comparison contexts must differ')
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_RESULT_CARDS:
        raise CatalogError('limit must be an integer from 1 to 50')
    contexts = [reader.read(commander_slug, theme) for theme in (theme_a, theme_b)]
    observed = []
    unresolved = []
    for context in contexts:
        indexed: dict[str, dict[str, Any]] = {}
        for card in context['cards']:
            oracle_id = resolve(card['name'], card['oracle_id'])
            if oracle_id is None:
                unresolved.append(card['name'])
                continue
            if oracle_id in indexed and indexed[oracle_id] != card:
                raise CatalogError('conflicting EDHREC card rows')
            indexed[oracle_id] = card
        observed.append(indexed)
    results = []
    for oracle_id in observed[0].keys() | observed[1].keys():
        sides = [items.get(oracle_id) for items in observed]
        rates = [None if row is None or row['inclusion_count'] is None or not row['potential_decks']
                 else 100.0 * row['inclusion_count'] / row['potential_decks'] for row in sides]
        results.append({'oracle_id': oracle_id, 'name': next(row['name'] for row in sides if row),
                        'contexts': [{**row, 'inclusion_percent': rate} if row else None
                                     for row, rate in zip(sides, rates)],
                        'difference_percentage_points': None if None in rates else rates[0] - rates[1]})
    results.sort(key=lambda r: (r['difference_percentage_points'] is None,
                                -abs(r['difference_percentage_points'] or 0), r['name'], r['oracle_id']))
    coverage = {'both_observed': sum(r['contexts'][0] is not None and r['contexts'][1] is not None
                                     for r in results),
                'only_a_observed': sum(r['contexts'][0] is not None and r['contexts'][1] is None
                                       for r in results),
                'only_b_observed': sum(r['contexts'][0] is None and r['contexts'][1] is not None
                                       for r in results),
                'comparable_rates': sum(r['difference_percentage_points'] is not None
                                        for r in results)}
    return {'commander_slug': commander_slug,
            'contexts': [{key: value for key, value in context.items() if key != 'cards'} for context in contexts],
            'cards': results[:limit], 'total_resolved_cards': len(results),
            'coverage': coverage,
            'unresolved_count': len(unresolved), 'unresolved_names': sorted(set(unresolved))[:20],
            'truncated': len(results) > limit,
            'interpretation': 'Observed contexts may overlap; overall includes themed decks. No complement is inferred.',
            'rules_authority': 'Argentum'}
