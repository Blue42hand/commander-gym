"""Bounded, read-only queries over a locally prepared Scryfall catalog.

This module deliberately does not download data, decide game rules, or open a
network listener. A separate, explicitly scoped loader can populate the schema.
"""

from __future__ import annotations

import base64
import binascii
from contextlib import contextmanager
import hashlib
import json
import math
import sqlite3
from pathlib import Path
from typing import Any


MAX_PAGE_SIZE = 50
MAX_QUERY_LENGTH = 120
SCHEMA_VERSION = 3
_SORT = "c.name COLLATE NOCASE, c.oracle_id"
AUTHORING_FIELDS = ('mana_cost', 'rarity', 'artist', 'flavor_text', 'image_uris',
                    'colors', 'color_indicator', 'power', 'toughness', 'loyalty',
                    'defense', 'layout', 'keywords', 'produced_mana', 'reserved')
RANK_FIELDS = ('edhrec_rank', 'penny_rank')


class CatalogError(ValueError):
    pass


def normalize_oracle_tag(record: dict[str, Any]) -> tuple[dict[str, str], tuple[str, ...]]:
    """Project an official oracle_tags JSONL record into tag and membership IDs.

    The record shape was checked against a bounded official sample. The caller
    remains responsible for joining memberships to a complete card snapshot.
    """
    if not isinstance(record, dict) or record.get('object') != 'tag' or record.get('type') != 'oracle':
        raise CatalogError('expected an oracle tag record')
    tag_id = _required_term(record.get('id'), 'tag id')
    label = _required_term(record.get('label'), 'tag label')
    taggings = record.get('taggings')
    if not isinstance(taggings, list):
        raise CatalogError('taggings must be an array')
    ids: set[str] = set()
    for tagging in taggings:
        if not isinstance(tagging, dict):
            raise CatalogError('tagging must be an object')
        ids.add(_required_term(tagging.get('oracle_id'), 'oracle_id'))
    return {'tag_id': tag_id, 'label': label}, tuple(sorted(ids))


def create_schema(connection: sqlite3.Connection) -> None:
    """Create the storage contract; intended for an offline loader or fixtures."""
    connection.executescript("""
        CREATE TABLE snapshots (
            snapshot_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            cards_updated_at TEXT,
            oracle_tags_updated_at TEXT,
            art_tags_updated_at TEXT,
            card_source TEXT NOT NULL,
            tag_source TEXT NOT NULL
        );
        CREATE TABLE dataset_sources (
            snapshot_id TEXT NOT NULL REFERENCES snapshots(snapshot_id),
            kind TEXT NOT NULL,
            url TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            compressed_size INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            downloaded_at TEXT NOT NULL,
            PRIMARY KEY(snapshot_id, kind)
        );
        CREATE TABLE cards (
            snapshot_id TEXT NOT NULL REFERENCES snapshots(snapshot_id),
            oracle_id TEXT NOT NULL,
            printing_id TEXT NOT NULL,
            name TEXT NOT NULL,
            type_line TEXT NOT NULL,
            oracle_text TEXT NOT NULL,
            mana_value REAL,
            color_identity TEXT NOT NULL,
            commander_legal INTEGER,
            price_usd TEXT,
            scryfall_uri TEXT,
            edhrec_rank INTEGER,
            penny_rank INTEGER,
            PRIMARY KEY (snapshot_id, oracle_id)
        );
        CREATE INDEX cards_name ON cards(snapshot_id, name COLLATE NOCASE, oracle_id);
        CREATE INDEX cards_printing ON cards(snapshot_id, printing_id);
        CREATE TABLE printings (
            snapshot_id TEXT NOT NULL,
            printing_id TEXT NOT NULL,
            oracle_id TEXT NOT NULL,
            face_index INTEGER NOT NULL,
            name TEXT NOT NULL,
            type_line TEXT NOT NULL,
            oracle_text TEXT NOT NULL,
            lang TEXT NOT NULL,
            digital INTEGER NOT NULL,
            released_at TEXT NOT NULL,
            set_code TEXT NOT NULL,
            collector_number TEXT NOT NULL,
            price_usd TEXT,
            scryfall_uri TEXT,
            faces_json TEXT NOT NULL,
            authoring_json TEXT NOT NULL,
            edhrec_rank INTEGER,
            penny_rank INTEGER,
            PRIMARY KEY (snapshot_id, printing_id, oracle_id),
            FOREIGN KEY(snapshot_id, oracle_id) REFERENCES cards(snapshot_id, oracle_id)
        );
        CREATE INDEX printings_oracle ON printings(snapshot_id, oracle_id);
        CREATE TABLE card_faces (
            snapshot_id TEXT NOT NULL,
            oracle_id TEXT NOT NULL,
            face_index INTEGER NOT NULL,
            face_oracle_id TEXT,
            mana_value REAL,
            name TEXT NOT NULL,
            type_line TEXT NOT NULL,
            oracle_text TEXT NOT NULL,
            PRIMARY KEY(snapshot_id, oracle_id, face_index),
            FOREIGN KEY(snapshot_id, oracle_id) REFERENCES cards(snapshot_id, oracle_id)
        );
        CREATE TABLE rulings (
            snapshot_id TEXT NOT NULL,
            oracle_id TEXT NOT NULL,
            source TEXT NOT NULL,
            published_at TEXT NOT NULL,
            comment TEXT NOT NULL,
            PRIMARY KEY(snapshot_id, oracle_id, source, published_at, comment),
            FOREIGN KEY(snapshot_id, oracle_id) REFERENCES cards(snapshot_id, oracle_id)
        );
        CREATE TABLE tags (
            snapshot_id TEXT NOT NULL REFERENCES snapshots(snapshot_id),
            kind TEXT NOT NULL CHECK(kind IN ('oracle', 'art')),
            tag_id TEXT NOT NULL,
            label TEXT NOT NULL,
            PRIMARY KEY(snapshot_id, kind, tag_id)
        );
        CREATE INDEX tags_label ON tags(snapshot_id, kind, label COLLATE NOCASE);
        CREATE TABLE card_tags (
            snapshot_id TEXT NOT NULL,
            oracle_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            tag_id TEXT NOT NULL,
            PRIMARY KEY(snapshot_id, oracle_id, kind, tag_id),
            FOREIGN KEY(snapshot_id, oracle_id) REFERENCES cards(snapshot_id, oracle_id),
            FOREIGN KEY(snapshot_id, kind, tag_id) REFERENCES tags(snapshot_id, kind, tag_id)
        );
        CREATE INDEX card_tags_lookup ON card_tags(snapshot_id, kind, tag_id, oracle_id);
    """)


def _limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_PAGE_SIZE:
        raise CatalogError(f"limit must be an integer from 1 to {MAX_PAGE_SIZE}")
    return value


def _term(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= MAX_QUERY_LENGTH:
        raise CatalogError(f"{field} must contain 1–{MAX_QUERY_LENGTH} characters")
    return value.strip()


def _required_term(value: Any, field: str) -> str:
    result = _term(value, field)
    if result is None:
        raise CatalogError(f'{field} is required')
    return result


def _like(value: str) -> str:
    return '%' + value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'


def _scope(operation: str, filters: dict[str, Any]) -> str:
    raw = json.dumps([operation, filters], sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(raw).hexdigest()


def _cursor_encode(snapshot: str, scope: str, name: str, key: str) -> str:
    raw = json.dumps([snapshot, scope, name, key], separators=(',', ':')).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def _cursor_decode(value: str | None, snapshot: str, scope: str) -> tuple[str, str] | None:
    if value is None:
        return None
    try:
        if not isinstance(value, str) or len(value) > 1024:
            raise ValueError()
        decoded = base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))
        parts = json.loads(decoded)
        if not isinstance(parts, list) or len(parts) != 4 or any(not isinstance(x, str) for x in parts):
            raise ValueError()
        if parts[0] != snapshot:
            raise CatalogError('cursor belongs to another snapshot')
        if parts[1] != scope:
            raise CatalogError('cursor belongs to another query')
        return parts[2], parts[3]
    except CatalogError:
        raise
    except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise CatalogError('invalid cursor') from exc


def _rank_cursor_encode(snapshot: str, scope: str, rank: int | None, name: str, key: str) -> str:
    raw = json.dumps([snapshot, scope, rank, name, key], separators=(',', ':')).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def _rank_cursor_decode(value: str | None, snapshot: str, scope: str) -> tuple[int | None, str, str] | None:
    if value is None:
        return None
    try:
        if not isinstance(value, str) or len(value) > 1024:
            raise ValueError()
        parts = json.loads(base64.urlsafe_b64decode(value + '=' * (-len(value) % 4)))
        if (not isinstance(parts, list) or len(parts) != 5 or
                (parts[2] is not None and (isinstance(parts[2], bool) or
                                           not isinstance(parts[2], int) or
                                           not 1 <= parts[2] <= 2**63 - 1)) or
                not isinstance(parts[3], str) or len(parts[3]) > MAX_QUERY_LENGTH * 4 or
                not isinstance(parts[4], str) or len(parts[4]) > MAX_QUERY_LENGTH):
            raise ValueError()
        if parts[0] != snapshot:
            raise CatalogError('cursor belongs to another snapshot')
        if parts[1] != scope:
            raise CatalogError('cursor belongs to another query')
        return parts[2], parts[3], parts[4]
    except CatalogError:
        raise
    except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise CatalogError('invalid cursor') from exc


class CardCatalog:
    """Query a pinned SQLite snapshot. The database is always opened read-only."""

    def __init__(self, path: str | Path, snapshot_id: str):
        self.path = Path(path)
        self.snapshot_id = _required_term(snapshot_id, 'snapshot_id')

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA query_only=ON')
            if connection.execute('SELECT 1 FROM snapshots WHERE snapshot_id=?', (self.snapshot_id,)).fetchone() is None:
                raise CatalogError('snapshot not found')
            yield connection
        finally:
            connection.close()

    def catalog_status(self) -> dict[str, Any]:
        with self._connect() as db:
            row = dict(db.execute('SELECT * FROM snapshots WHERE snapshot_id=?', (self.snapshot_id,)).fetchone())
            row['card_count'] = db.execute('SELECT count(*) FROM cards WHERE snapshot_id=?', (self.snapshot_id,)).fetchone()[0]
            row['printing_count'] = db.execute('SELECT count(DISTINCT printing_id) FROM printings WHERE snapshot_id=?', (self.snapshot_id,)).fetchone()[0]
            row['ruling_count'] = db.execute('SELECT count(*) FROM rulings WHERE snapshot_id=?', (self.snapshot_id,)).fetchone()[0]
            row['oracle_tag_count'] = db.execute("SELECT count(*) FROM tags WHERE snapshot_id=? AND kind='oracle'", (self.snapshot_id,)).fetchone()[0]
            row['art_tag_count'] = db.execute("SELECT count(*) FROM tags WHERE snapshot_id=? AND kind='art'", (self.snapshot_id,)).fetchone()[0]
            row['authoring_fields_available'] = any(
                column['name'] == 'authoring_json' for column in db.execute("PRAGMA table_info('printings')")
            )
            row['rank_fields_available'] = self._has_ranks(db)
            row['expanded_authoring_fields_available'] = row['rank_fields_available']
            row['schema_version'] = (3 if row['rank_fields_available'] else
                                     2 if row['authoring_fields_available'] else 1)
            row['rank_note'] = ('Lower numeric rank means more popular within that source. '
                                'A null rank is unranked or unavailable in this snapshot; '
                                'rank values reflect the pinned Scryfall bulk source date.')
            row['tag_note'] = 'Tags are advisory; a missing tag does not prove a card lacks a gameplay role.'
            row['datasets'] = [dict(r) for r in db.execute(
                'SELECT kind, url, updated_at, compressed_size, sha256, downloaded_at '
                'FROM dataset_sources WHERE snapshot_id=? ORDER BY kind', (self.snapshot_id,))]
            return row

    @staticmethod
    def _has_ranks(db: sqlite3.Connection) -> bool:
        return {column['name'] for column in db.execute("PRAGMA table_info('cards')")} >= set(RANK_FIELDS)

    def search_tags(self, query: str, *, kind: str = 'oracle', limit: int = 20, cursor: str | None = None) -> dict[str, Any]:
        query = _required_term(query, 'query')
        if kind not in ('oracle', 'art'):
            raise CatalogError('kind must be oracle or art')
        limit = _limit(limit)
        scope = _scope('search_tags', {'kind': kind, 'query': query})
        after = _cursor_decode(cursor, self.snapshot_id, scope)
        where = ['snapshot_id=?', 'kind=?', "label LIKE ? ESCAPE '\\'"]
        args: list[Any] = [self.snapshot_id, kind, _like(query)]
        if after:
            where.append('(label COLLATE NOCASE, tag_id) > (?, ?)')
            args.extend(after)
        sql = 'SELECT tag_id, label FROM tags WHERE ' + ' AND '.join(where) + ' ORDER BY label COLLATE NOCASE, tag_id LIMIT ?'
        with self._connect() as db:
            rows = [dict(r) for r in db.execute(sql, (*args, limit + 1))]
        more = len(rows) > limit
        rows = rows[:limit]
        return {'snapshot_id': self.snapshot_id, 'kind': kind, 'tags': rows,
                'next_cursor': _cursor_encode(self.snapshot_id, scope, rows[-1]['label'], rows[-1]['tag_id']) if more else None,
                'advisory': True}

    def search_cards(self, *, name: str | None = None, oracle_text: str | None = None,
                     type_line: str | None = None, tag_id: str | None = None,
                     tag_kind: str = 'oracle', commander_legal: bool | None = None,
                     color_identity: str | None = None, mana_value_min: float | None = None,
                     mana_value_max: float | None = None,
                     edhrec_rank_min: int | None = None, edhrec_rank_max: int | None = None,
                     penny_rank_min: int | None = None, penny_rank_max: int | None = None,
                     sort_by: str = 'name', limit: int = 20,
                     cursor: str | None = None,
                     _registry_filter: tuple[str, tuple[str, ...]] | None = None) -> dict[str, Any]:
        limit = _limit(limit)
        if tag_kind not in ('oracle', 'art'):
            raise CatalogError('tag_kind must be oracle or art')
        if commander_legal is not None and not isinstance(commander_legal, bool):
            raise CatalogError('commander_legal must be boolean')
        if color_identity is not None:
            if not isinstance(color_identity, str) or any(c not in 'WUBRG' for c in color_identity.upper()) or len(set(color_identity.upper())) != len(color_identity):
                raise CatalogError('color_identity must use unique WUBRG letters')
            color_identity = color_identity.upper()
        for field, value in (('mana_value_min', mana_value_min), ('mana_value_max', mana_value_max)):
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                      or not math.isfinite(value) or value < 0):
                raise CatalogError(f'{field} must be a nonnegative finite number')
        if mana_value_min is not None and mana_value_max is not None and mana_value_min > mana_value_max:
            raise CatalogError('mana value range is reversed')
        if sort_by not in ('name', *RANK_FIELDS):
            raise CatalogError('sort_by must be name, edhrec_rank, or penny_rank')
        rank_filters = {field: value for field, value in (
            ('edhrec_rank_min', edhrec_rank_min), ('edhrec_rank_max', edhrec_rank_max),
            ('penny_rank_min', penny_rank_min), ('penny_rank_max', penny_rank_max)) if value is not None}
        for field, value in rank_filters.items():
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 2**63 - 1:
                raise CatalogError(f'{field} must be a positive integer')
        for rank in RANK_FIELDS:
            low, high = rank_filters.get(rank + '_min'), rank_filters.get(rank + '_max')
            if low is not None and high is not None and low > high:
                raise CatalogError(f'{rank} range is reversed')
        name = _term(name, 'name')
        oracle_text = _term(oracle_text, 'oracle_text')
        type_line = _term(type_line, 'type_line')
        if tag_id is not None:
            tag_id = _required_term(tag_id, 'tag_id')
        scoped_filters = {
            'name': name, 'oracle_text': oracle_text, 'type_line': type_line,
            'tag_id': tag_id, 'tag_kind': tag_kind,
            'commander_legal': commander_legal, 'color_identity': color_identity,
            'mana_value_min': mana_value_min, 'mana_value_max': mana_value_max,
        }
        # Preserve existing name-sort cursor hashes for unchanged queries.
        if rank_filters or sort_by != 'name':
            scoped_filters.update(rank_filters)
            scoped_filters['sort_by'] = sort_by
        if _registry_filter is not None:
            scoped_filters['registered_in'] = _registry_filter[0]
        scope = _scope('search_cards', scoped_filters)
        after = (_cursor_decode(cursor, self.snapshot_id, scope) if sort_by == 'name' else
                 _rank_cursor_decode(cursor, self.snapshot_id, scope))
        where = ['c.snapshot_id=?']
        args: list[Any] = [self.snapshot_id]
        if _registry_filter is not None:
            where.append('c.oracle_id IN (SELECT value FROM json_each(?))')
            args.append(json.dumps(_registry_filter[1]))
        for column, value in (('name', name), ('oracle_text', oracle_text), ('type_line', type_line)):
            if value:
                where.append(f"(c.{column} LIKE ? ESCAPE '\\' OR EXISTS ("
                             f"SELECT 1 FROM card_faces f WHERE f.snapshot_id=c.snapshot_id "
                             f"AND f.oracle_id=c.oracle_id "
                             f"AND (f.face_oracle_id IS NULL OR f.face_oracle_id=c.oracle_id) "
                             f"AND f.{column} LIKE ? ESCAPE '\\'))")
                args.extend([_like(value), _like(value)])
        if tag_id is not None:
            where.append('EXISTS (SELECT 1 FROM card_tags ct WHERE ct.snapshot_id=c.snapshot_id AND ct.oracle_id=c.oracle_id AND ct.kind=? AND ct.tag_id=?)')
            args.extend([tag_kind, tag_id])
        if commander_legal is not None:
            where.append('c.commander_legal=?')
            args.append(int(commander_legal))
        if color_identity is not None:
            for color in 'WUBRG':
                if color not in color_identity:
                    where.append('instr(c.color_identity, ?)=0')
                    args.append(color)
        if mana_value_min is not None:
            where.append('c.mana_value>=?')
            args.append(mana_value_min)
        if mana_value_max is not None:
            where.append('c.mana_value<=?')
            args.append(mana_value_max)
        for field, value in rank_filters.items():
            rank, bound = field.rsplit('_', 1)
            where.append(f'c.{rank}{">=" if bound == "min" else "<="}?')
            args.append(value)
        if after:
            if sort_by == 'name':
                where.append('(c.name COLLATE NOCASE, c.oracle_id) > (?, ?)')
                args.extend(after)
            elif after[0] is None:
                where.append(f'c.{sort_by} IS NULL AND (c.name COLLATE NOCASE, c.oracle_id) > (?, ?)')
                args.extend(after[1:])
            else:
                where.append(f'(c.{sort_by} IS NULL OR c.{sort_by}>? OR '
                             f'(c.{sort_by}=? AND (c.name COLLATE NOCASE, c.oracle_id) > (?, ?)))')
                args.extend((after[0], after[0], *after[1:]))
        order = (_SORT if sort_by == 'name' else
                 f'c.{sort_by} IS NULL, c.{sort_by}, {_SORT}')
        sql = 'SELECT c.* FROM cards c WHERE ' + ' AND '.join(where) + f' ORDER BY {order} LIMIT ?'
        with self._connect() as db:
            if (rank_filters or sort_by != 'name') and not self._has_ranks(db):
                raise CatalogError('rank fields unavailable in this snapshot')
            rows = [self._card(db, r) for r in db.execute(sql, (*args, limit + 1)).fetchall()]
        more = len(rows) > limit
        rows = rows[:limit]
        return {'snapshot_id': self.snapshot_id, 'cards': rows,
                'next_cursor': ((
                    _cursor_encode(self.snapshot_id, scope, rows[-1]['name'], rows[-1]['oracle_id'])
                    if sort_by == 'name' else
                    _rank_cursor_encode(self.snapshot_id, scope, rows[-1][sort_by],
                                        rows[-1]['name'], rows[-1]['oracle_id'])) if more else None),
                'sort_by': sort_by,
                'tags_advisory': True}

    def get_card(self, *, oracle_id: str | None = None, printing_id: str | None = None) -> dict[str, Any] | None:
        if (oracle_id is None) == (printing_id is None):
            raise CatalogError('provide exactly one of oracle_id or printing_id')
        field, value = ('oracle_id', oracle_id) if oracle_id is not None else ('printing_id', printing_id)
        with self._connect() as db:
            value = _required_term(value, field)
            if printing_id is not None:
                rows = db.execute('SELECT c.* FROM printings p JOIN cards c ON '
                                  'c.snapshot_id=p.snapshot_id AND c.oracle_id=p.oracle_id '
                                  'WHERE p.snapshot_id=? AND p.printing_id=? ORDER BY p.face_index',
                                  (self.snapshot_id, value)).fetchall()
                if not rows:
                    rows = db.execute('SELECT * FROM cards WHERE snapshot_id=? AND printing_id=?',
                                      (self.snapshot_id, value)).fetchall()
            else:
                rows = db.execute('SELECT * FROM cards WHERE snapshot_id=? AND oracle_id=?',
                                  (self.snapshot_id, value)).fetchall()
            if not rows:
                return None
            results = [self._card(db, row) for row in rows]
            if printing_id is not None:
                for result in results:
                    printing = db.execute('SELECT * FROM printings WHERE snapshot_id=? AND printing_id=? AND oracle_id=?',
                                          (self.snapshot_id, value, result['oracle_id'])).fetchone()
                    if printing:
                        result['requested_printing'] = self._printing(db, printing)
                    else:
                        result['requested_printing'] = None
                    result['requested_printing_note'] = (
                        'Use requested_printing for printing-specific details. '
                        'Top-level card fields describe the representative Oracle printing.'
                    )
                if len(results) > 1:
                    return {'snapshot_id': self.snapshot_id, 'printing_id': value,
                            'oracle_faces': results, 'tags_advisory': True}
            return results[0]

    def _card(self, db: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        for field in RANK_FIELDS:
            result.setdefault(field, None)
        result['rank_fields_available'] = self._has_ranks(db)
        source = db.execute(
            'SELECT card_source, tag_source, cards_updated_at, oracle_tags_updated_at, art_tags_updated_at '
            'FROM snapshots WHERE snapshot_id=?', (self.snapshot_id,)).fetchone()
        result['provenance'] = dict(source)
        result['provenance']['datasets'] = [dict(r) for r in db.execute(
            'SELECT kind, url, updated_at, compressed_size, sha256, downloaded_at '
            'FROM dataset_sources WHERE snapshot_id=? ORDER BY kind', (self.snapshot_id,))]
        result['color_identity'] = list(result['color_identity'])
        result['commander_legal'] = None if result['commander_legal'] is None else bool(result['commander_legal'])
        result['tags'] = [dict(r) for r in db.execute(
            'SELECT t.kind, t.tag_id, t.label FROM card_tags ct JOIN tags t ON '
            't.snapshot_id=ct.snapshot_id AND t.kind=ct.kind AND t.tag_id=ct.tag_id '
            'WHERE ct.snapshot_id=? AND ct.oracle_id=? ORDER BY t.kind, t.label',
            (self.snapshot_id, result['oracle_id']))]
        result['price_usd'] = result['price_usd'] or None
        result['price_note'] = 'Missing price means unavailable, not zero.' if result['price_usd'] is None else None
        representative = db.execute(
            'SELECT * FROM printings WHERE snapshot_id=? AND printing_id=? AND oracle_id=?',
            (self.snapshot_id, result['printing_id'], result['oracle_id']),
        ).fetchone()
        if representative is not None:
            details = self._printing(db, representative)
            for field in AUTHORING_FIELDS:
                result[field] = details[field]
            result['authoring_fields_available'] = details['authoring_fields_available']
            result['expanded_authoring_fields_available'] = details['expanded_authoring_fields_available']
        else:
            result.update({field: None for field in AUTHORING_FIELDS})
            result['authoring_fields_available'] = False
            result['expanded_authoring_fields_available'] = False
        result['tags_advisory'] = True
        result['printing_count'] = db.execute(
            'SELECT count(*) FROM printings WHERE snapshot_id=? AND oracle_id=?',
            (self.snapshot_id, result['oracle_id'])).fetchone()[0]
        result['faces'] = [dict(r) for r in db.execute(
            'SELECT face_index, face_oracle_id, mana_value, name, type_line, oracle_text FROM card_faces '
            'WHERE snapshot_id=? AND oracle_id=? ORDER BY face_index LIMIT 10',
            (self.snapshot_id, result['oracle_id']))]
        representative_faces = details['faces'] if representative is not None else []
        for face in result['faces']:
            index = face['face_index']
            source_face = representative_faces[index] if index < len(representative_faces) else {}
            for field in AUTHORING_FIELDS:
                face[field] = source_face.get(field)
            face['authoring_fields_available'] = result['authoring_fields_available']
            face['expanded_authoring_fields_available'] = result['expanded_authoring_fields_available']
        result['ruling_count'] = db.execute(
            'SELECT count(*) FROM rulings WHERE snapshot_id=? AND oracle_id=?',
            (self.snapshot_id, result['oracle_id'])).fetchone()[0]
        result['rulings'] = [dict(r) for r in db.execute(
            'SELECT source, published_at, comment FROM rulings WHERE snapshot_id=? AND oracle_id=? '
            'ORDER BY published_at DESC, source, comment LIMIT 20',
            (self.snapshot_id, result['oracle_id']))]
        return result

    def _printing(self, db: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result['faces'] = json.loads(result.pop('faces_json'))
        for face in result['faces']:
            for field in AUTHORING_FIELDS:
                face.setdefault(field, None)
        for field in RANK_FIELDS:
            result.setdefault(field, None)
        result['rank_fields_available'] = self._has_ranks(db)
        if 'authoring_json' in result:
            result.update(json.loads(result.pop('authoring_json')))
            for field in AUTHORING_FIELDS:
                result.setdefault(field, None)
            result['authoring_fields_available'] = True
            result['expanded_authoring_fields_available'] = result['rank_fields_available']
        else:
            result.update({field: None for field in AUTHORING_FIELDS})
            result['authoring_fields_available'] = False
            result['expanded_authoring_fields_available'] = False
        result['provenance'] = {
            'snapshot_id': self.snapshot_id,
            'dataset': 'default_cards',
            'source_url': db.execute(
                'SELECT card_source FROM snapshots WHERE snapshot_id=?', (self.snapshot_id,)
            ).fetchone()[0],
            'printing_id': result['printing_id'],
        }
        return result
