"""Bounded, read-only queries over a locally prepared Scryfall catalog.

This module deliberately does not download data, decide game rules, or open a
network listener. A separate, explicitly scoped loader can populate the schema.
"""

from __future__ import annotations

import base64
import json
import sqlite3
from pathlib import Path
from typing import Any


MAX_PAGE_SIZE = 50
MAX_QUERY_LENGTH = 120
_SORT = "c.name COLLATE NOCASE, c.oracle_id"


class CatalogError(ValueError):
    pass


def normalize_oracle_tag(record: dict[str, Any]) -> tuple[dict[str, str], tuple[str, ...]]:
    """Project an official oracle_tags JSONL record into tag and membership IDs.

    The record shape was checked against a bounded official sample. The caller
    remains responsible for joining memberships to a complete card snapshot.
    """
    if not isinstance(record, dict) or record.get('object') != 'tag' or record.get('type') != 'oracle':
        raise CatalogError('expected an oracle tag record')
    tag_id = _term(record.get('id'), 'tag id')
    label = _term(record.get('label'), 'tag label')
    taggings = record.get('taggings')
    if not isinstance(taggings, list):
        raise CatalogError('taggings must be an array')
    ids: set[str] = set()
    for tagging in taggings:
        if not isinstance(tagging, dict):
            raise CatalogError('tagging must be an object')
        ids.add(_term(tagging.get('oracle_id'), 'oracle_id'))
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
            PRIMARY KEY (snapshot_id, oracle_id)
        );
        CREATE INDEX cards_name ON cards(snapshot_id, name COLLATE NOCASE, oracle_id);
        CREATE UNIQUE INDEX cards_printing ON cards(snapshot_id, printing_id);
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


def _like(value: str) -> str:
    return '%' + value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'


def _cursor_encode(snapshot: str, name: str, key: str) -> str:
    raw = json.dumps([snapshot, name, key], separators=(',', ':')).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def _cursor_decode(value: str | None, snapshot: str) -> tuple[str, str] | None:
    if value is None:
        return None
    try:
        if not isinstance(value, str) or len(value) > 1024:
            raise ValueError()
        decoded = base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))
        parts = json.loads(decoded)
        if not isinstance(parts, list) or len(parts) != 3 or any(not isinstance(x, str) for x in parts):
            raise ValueError()
        if parts[0] != snapshot:
            raise CatalogError('cursor belongs to another snapshot')
        return parts[1], parts[2]
    except CatalogError:
        raise
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CatalogError('invalid cursor') from exc


class CardCatalog:
    """Query a pinned SQLite snapshot. The database is always opened read-only."""

    def __init__(self, path: str | Path, snapshot_id: str):
        self.path = Path(path)
        self.snapshot_id = _term(snapshot_id, 'snapshot_id')
        if self.snapshot_id is None:
            raise CatalogError('snapshot_id is required')

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only=ON')
        if connection.execute('SELECT 1 FROM snapshots WHERE snapshot_id=?', (self.snapshot_id,)).fetchone() is None:
            connection.close()
            raise CatalogError('snapshot not found')
        return connection

    def catalog_status(self) -> dict[str, Any]:
        with self._connect() as db:
            row = dict(db.execute('SELECT * FROM snapshots WHERE snapshot_id=?', (self.snapshot_id,)).fetchone())
            row['card_count'] = db.execute('SELECT count(*) FROM cards WHERE snapshot_id=?', (self.snapshot_id,)).fetchone()[0]
            row['oracle_tag_count'] = db.execute("SELECT count(*) FROM tags WHERE snapshot_id=? AND kind='oracle'", (self.snapshot_id,)).fetchone()[0]
            row['art_tag_count'] = db.execute("SELECT count(*) FROM tags WHERE snapshot_id=? AND kind='art'", (self.snapshot_id,)).fetchone()[0]
            row['tag_note'] = 'Tags are advisory; a missing tag does not prove a card lacks a gameplay role.'
            return row

    def search_tags(self, query: str, *, kind: str = 'oracle', limit: int = 20, cursor: str | None = None) -> dict[str, Any]:
        query = _term(query, 'query')
        if kind not in ('oracle', 'art'):
            raise CatalogError('kind must be oracle or art')
        limit = _limit(limit)
        after = _cursor_decode(cursor, self.snapshot_id)
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
                'next_cursor': _cursor_encode(self.snapshot_id, rows[-1]['label'], rows[-1]['tag_id']) if more else None,
                'advisory': True}

    def search_cards(self, *, name: str | None = None, oracle_text: str | None = None,
                     type_line: str | None = None, tag_id: str | None = None,
                     tag_kind: str = 'oracle', commander_legal: bool | None = None,
                     color_identity: str | None = None, mana_value_min: float | None = None,
                     mana_value_max: float | None = None, limit: int = 20,
                     cursor: str | None = None) -> dict[str, Any]:
        limit = _limit(limit)
        after = _cursor_decode(cursor, self.snapshot_id)
        if tag_kind not in ('oracle', 'art'):
            raise CatalogError('tag_kind must be oracle or art')
        if commander_legal is not None and not isinstance(commander_legal, bool):
            raise CatalogError('commander_legal must be boolean')
        if color_identity is not None:
            if not isinstance(color_identity, str) or any(c not in 'WUBRG' for c in color_identity.upper()) or len(set(color_identity.upper())) != len(color_identity):
                raise CatalogError('color_identity must use unique WUBRG letters')
            color_identity = color_identity.upper()
        for field, value in (('mana_value_min', mana_value_min), ('mana_value_max', mana_value_max)):
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1000):
                raise CatalogError(f'{field} must be between 0 and 1000')
        if mana_value_min is not None and mana_value_max is not None and mana_value_min > mana_value_max:
            raise CatalogError('mana value range is reversed')
        where = ['c.snapshot_id=?']
        args: list[Any] = [self.snapshot_id]
        for column, value in (('name', name), ('oracle_text', oracle_text), ('type_line', type_line)):
            value = _term(value, column)
            if value:
                where.append(f"c.{column} LIKE ? ESCAPE '\\'")
                args.append(_like(value))
        if tag_id is not None:
            where.append('EXISTS (SELECT 1 FROM card_tags ct WHERE ct.snapshot_id=c.snapshot_id AND ct.oracle_id=c.oracle_id AND ct.kind=? AND ct.tag_id=?)')
            args.extend([tag_kind, _term(tag_id, 'tag_id')])
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
        if after:
            where.append('(c.name COLLATE NOCASE, c.oracle_id) > (?, ?)')
            args.extend(after)
        sql = 'SELECT c.* FROM cards c WHERE ' + ' AND '.join(where) + f' ORDER BY {_SORT} LIMIT ?'
        with self._connect() as db:
            rows = [self._card(db, r) for r in db.execute(sql, (*args, limit + 1)).fetchall()]
        more = len(rows) > limit
        rows = rows[:limit]
        return {'snapshot_id': self.snapshot_id, 'cards': rows,
                'next_cursor': _cursor_encode(self.snapshot_id, rows[-1]['name'], rows[-1]['oracle_id']) if more else None,
                'tags_advisory': True}

    def get_card(self, *, oracle_id: str | None = None, printing_id: str | None = None) -> dict[str, Any] | None:
        if (oracle_id is None) == (printing_id is None):
            raise CatalogError('provide exactly one of oracle_id or printing_id')
        field, value = ('oracle_id', oracle_id) if oracle_id is not None else ('printing_id', printing_id)
        with self._connect() as db:
            row = db.execute(f'SELECT * FROM cards WHERE snapshot_id=? AND {field}=?',
                             (self.snapshot_id, _term(value, field))).fetchone()
            return self._card(db, row) if row else None

    def _card(self, db: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        source = db.execute(
            'SELECT card_source, tag_source, cards_updated_at, oracle_tags_updated_at, art_tags_updated_at '
            'FROM snapshots WHERE snapshot_id=?', (self.snapshot_id,)).fetchone()
        result['provenance'] = dict(source)
        result['color_identity'] = list(result['color_identity'])
        result['commander_legal'] = None if result['commander_legal'] is None else bool(result['commander_legal'])
        result['tags'] = [dict(r) for r in db.execute(
            'SELECT t.kind, t.tag_id, t.label FROM card_tags ct JOIN tags t ON '
            't.snapshot_id=ct.snapshot_id AND t.kind=ct.kind AND t.tag_id=ct.tag_id '
            'WHERE ct.snapshot_id=? AND ct.oracle_id=? ORDER BY t.kind, t.label',
            (self.snapshot_id, result['oracle_id']))]
        result['price_usd'] = result['price_usd'] or None
        result['price_note'] = 'Missing price means unavailable, not zero.' if result['price_usd'] is None else None
        result['tags_advisory'] = True
        return result
