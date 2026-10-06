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
MAX_THEMES = 50
MAX_AVERAGE_DECK_CARDS = 100
MAX_COUNT = 1_000_000_000_000
MAX_METRIC_ABS = 1_000_000_000
_SLUG = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*\Z')


def _slug(value: str, field: str) -> str:
    if not isinstance(value, str) or len(value) > 100 or not _SLUG.fullmatch(value):
        raise CatalogError(f'{field} must be a bounded lowercase slug')
    return value


def _count(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_COUNT:
        raise CatalogError(f'{field} must be a bounded nonnegative integer or null')
    return value


def _number(value: Any, field: str) -> float | None:
    if value is None:
        return None
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or
            not -MAX_METRIC_ABS <= value <= MAX_METRIC_ABS):
        raise CatalogError(f'{field} must be a bounded finite number or null')
    return float(value)


def _text(value: Any, field: str, max_length: int) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise CatalogError(f'invalid {field}')
    try:
        value.encode('utf-8')
    except UnicodeError as exc:
        raise CatalogError(f'invalid {field}') from exc
    return value


def parse_context(body: bytes, commander_slug: str, theme_slug: str | None, *,
                  allow_loopback_for_tests: bool = False) -> dict[str, Any]:
    """Validate a narrow projection, without silently filling absent values.

    This is the internal adapter envelope, not a claim about EDHREC's live JSON
    shape. Source-specific projection needs a separately reviewed fixture.
    """
    if not isinstance(body, bytes) or len(body) > MAX_BODY_BYTES:
        raise CatalogError('EDHREC context exceeds byte limit')
    try:
        data = json.loads(body)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise CatalogError('invalid EDHREC context JSON') from exc
    if not isinstance(data, dict) or data.get('commander_slug') != commander_slug or data.get('theme_slug') != theme_slug:
        raise CatalogError('EDHREC context identity mismatch')
    commander_name = _text(data.get('commander_name'), 'EDHREC commander name', 150)
    url = _text(data.get('source_url'), 'EDHREC source URL', 500)
    try:
        parsed = urlsplit(url)
        allowed_origin = ((parsed.scheme == 'https' and
                           parsed.hostname in ('edhrec.com', 'json.edhrec.com') and
                           not parsed.port) or
                          (allow_loopback_for_tests and parsed.scheme == 'http' and
                           parsed.hostname == '127.0.0.1' and parsed.port))
        valid_url = (allowed_origin and not parsed.username and not parsed.password and
                     parsed.path.startswith('/') and
                     commander_slug in [segment.removesuffix('.json')
                                        for segment in parsed.path.split('/')])
    except ValueError:
        valid_url = False
    if not valid_url:
        raise CatalogError('invalid EDHREC source URL')
    retrieved = data.get('retrieved_at')
    try:
        _text(retrieved, 'EDHREC retrieval time', 60)
        if datetime.fromisoformat(retrieved.replace('Z', '+00:00')).tzinfo is None:
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
        name = _text(row.get('name'), 'EDHREC card name', 150)
        if not name.strip():
            raise CatalogError('invalid EDHREC card name')
        oracle_id = row.get('oracle_id')
        if oracle_id is not None:
            _text(oracle_id, 'EDHREC Oracle ID', 64)
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
    themes = data.get('themes')
    if themes is not None:
        if not isinstance(themes, list) or len(themes) > MAX_THEMES:
            raise CatalogError('EDHREC theme list exceeds limit')
        themes = [{'name': _text(item.get('name'), 'EDHREC theme name', 100),
                   'slug': _slug(item.get('slug'), 'EDHREC theme slug'),
                   'deck_count': _count(item.get('deck_count'), 'deck_count')}
                  for item in themes if isinstance(item, dict)]
        if len(themes) != len(data['themes']):
            raise CatalogError('invalid EDHREC theme row')
    average_deck = data.get('average_deck')
    if average_deck is not None:
        if not isinstance(average_deck, list) or len(average_deck) > MAX_AVERAGE_DECK_CARDS:
            raise CatalogError('EDHREC average deck exceeds limit')
        deck = []
        for item in average_deck:
            if not isinstance(item, dict):
                raise CatalogError('invalid EDHREC average deck row')
            quantity = _number(item.get('quantity'), 'average deck quantity')
            if quantity is None or quantity < 0:
                raise CatalogError('average deck quantity must be nonnegative')
            deck.append({'name': _text(item.get('name'), 'average deck card name', 150),
                         'quantity': quantity})
        average_deck = deck
    return {'commander_slug': commander_slug, 'commander_name': commander_name,
            'theme_slug': theme_slug,
            'source_url': url, 'retrieved_at': retrieved, 'cards': cards,
            'themes': themes, 'average_deck': average_deck}


class ContextReader:
    """Serial, spaced, no-retry access through an explicitly injected source."""

    def __init__(self, fetch: Callable[[str, str | None], bytes], *,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 allow_loopback_for_tests: bool = False):
        self.fetch, self.clock, self.sleep = fetch, clock, sleep
        self.allow_loopback_for_tests = allow_loopback_for_tests
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
            return parse_context(body, commander_slug, theme_slug,
                                 allow_loopback_for_tests=self.allow_loopback_for_tests)


class SourceHTTPError(Exception):
    def __init__(self, status: int):
        self.status = status
        super().__init__(f'HTTP {status}')


def compare_contexts(reader: ContextReader, commander_slug: str,
                     theme_a: str | None, theme_b: str | None,
                     resolve: Callable[[str, str | None], str | None], *,
                     commander_oracle_id: str,
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
    if any(resolve(context['commander_name'], None) != commander_oracle_id
           for context in contexts):
        raise CatalogError('EDHREC commander identity does not match catalog Oracle ID')
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
        if context['average_deck'] is not None:
            resolved_deck = []
            unresolved_deck = 0
            for item in context['average_deck']:
                oracle_id = resolve(item['name'], None)
                if oracle_id is None:
                    unresolved_deck += 1
                else:
                    resolved_deck.append({**item, 'oracle_id': oracle_id})
            context['average_deck'] = resolved_deck
            context['unresolved_average_deck_count'] = unresolved_deck
    results = []
    for oracle_id in observed[0].keys() | observed[1].keys():
        sides = [items.get(oracle_id) for items in observed]
        rates = [None if row is None or row['inclusion_count'] is None or not row['potential_decks']
                 else 100 * row['inclusion_count'] / row['potential_decks'] for row in sides]
        if any(rate is not None and not math.isfinite(rate) for rate in rates):
            raise CatalogError('EDHREC inclusion rate is not finite')
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
            'unresolved_count': len(unresolved),
            'truncated': len(results) > limit,
            'interpretation': 'Observed contexts may overlap; overall includes themed decks. No complement is inferred.',
            'source_data_untrusted': True,
            'rules_authority': 'Argentum'}
