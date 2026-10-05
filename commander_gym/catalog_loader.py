"""One-shot, offline publication of a local Scryfall catalog snapshot.

No listener, scheduler, private data source, or game runner is involved.
"""

from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import fcntl
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from typing import Iterator
import urllib.parse
import urllib.request
from uuid import UUID

from .card_catalog import SCHEMA_VERSION, create_schema, normalize_oracle_tag


KINDS = ('default_cards', 'rulings', 'oracle_tags')
MAX_COMPRESSED = {'default_cards': 150_000_000, 'rulings': 20_000_000, 'oracle_tags': 20_000_000}
MAX_RECORDS = {'default_cards': 1_000_000, 'rulings': 2_000_000, 'oracle_tags': 100_000}
MAX_LINE = 4_000_000
MAX_UNCOMPRESSED = {'default_cards': 3_000_000_000, 'rulings': 500_000_000, 'oracle_tags': 500_000_000}
HEADERS = {'User-Agent': 'CommanderGymCatalogLoader/0.1 (Blue42hand/commander-gym; local public catalog)',
           'Accept': 'application/json'}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise CatalogLoadError('source redirect refused')


_OPENER = urllib.request.build_opener(_NoRedirect)


class CatalogLoadError(ValueError):
    pass


@dataclass(frozen=True)
class Source:
    kind: str
    url: str
    updated_at: str
    compressed_size: int
    path: Path
    sha256: str
    downloaded_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _id(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise CatalogLoadError(f'{field} must be a UUID string')
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise CatalogLoadError(f'{field} must be a UUID string') from exc


def _text(value: object, field: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > MAX_LINE or (not empty and not value):
        raise CatalogLoadError(f'{field} must be a bounded string')
    return value


def _source_url(kind: str, url: object) -> str:
    if kind not in KINDS or not isinstance(url, str):
        raise CatalogLoadError('invalid source kind or URL')
    parts = urllib.parse.urlsplit(url)
    if (parts.scheme != 'https' or parts.netloc != 'data.scryfall.io' or
            parts.query or parts.fragment or not re.fullmatch(
                rf'/{kind.replace("_", "-")}/{kind.replace("_", "-")}-[0-9]{{14}}\.jsonl\.gz', parts.path)):
        raise CatalogLoadError(f'unapproved {kind} source URL')
    return url


def fetch_manifest() -> dict[str, dict]:
    request = urllib.request.Request('https://api.scryfall.com/bulk-data', headers=HEADERS)
    with _OPENER.open(request, timeout=30) as response:
        if response.geturl() != request.full_url:
            raise CatalogLoadError('manifest redirected')
        raw = response.read(1_000_001)
    if len(raw) > 1_000_000:
        raise CatalogLoadError('manifest too large')
    data = json.loads(raw)
    entries: dict[str, dict] = {}
    for item in data.get('data', []):
        kind = item.get('type')
        if kind not in KINDS:
            continue
        if kind in entries:
            raise CatalogLoadError(f'duplicate manifest kind {kind}')
        _source_url(kind, item.get('jsonl_download_uri'))
        size = item.get('compressed_size')
        if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= MAX_COMPRESSED[kind]:
            raise CatalogLoadError(f'invalid {kind} compressed_size')
        _text(item.get('updated_at'), f'{kind} updated_at')
        entries[kind] = item
    if set(entries) != set(KINDS):
        raise CatalogLoadError('manifest missing required dataset')
    return entries


def download_source(kind: str, entry: dict, directory: Path) -> Source:
    url = _source_url(kind, entry.get('jsonl_download_uri'))
    expected = entry['compressed_size']
    path = directory / f'{kind}.jsonl.gz'
    digest = hashlib.sha256()
    count = 0
    request = urllib.request.Request(url, headers=HEADERS)
    with _OPENER.open(request, timeout=60) as response, path.open('xb') as target:
        if response.geturl() != url or response.status != 200:
            raise CatalogLoadError(f'{kind} redirect or non-200 response')
        while chunk := response.read(1024 * 1024):
            count += len(chunk)
            if count > expected:
                raise CatalogLoadError(f'{kind} exceeds manifest size')
            digest.update(chunk)
            target.write(chunk)
        target.flush()
        os.fsync(target.fileno())
    if count != expected:
        raise CatalogLoadError(f'{kind} partial download: {count} != {expected}')
    return Source(kind, url, entry['updated_at'], expected, path, digest.hexdigest(), _now())


def _records(source: Source) -> Iterator[dict]:
    if source.kind not in KINDS or source.path.stat().st_size != source.compressed_size:
        raise CatalogLoadError(f'{source.kind} compressed size mismatch')
    with source.path.open('rb') as compressed:
        digest = hashlib.file_digest(compressed, 'sha256').hexdigest()
    if digest != source.sha256:
        raise CatalogLoadError(f'{source.kind} checksum mismatch')
    total = 0
    count = 0
    try:
        with gzip.open(source.path, 'rb') as stream:
            while line := stream.readline(MAX_LINE + 1):
                total += len(line)
                count += 1
                if len(line) > MAX_LINE or total > MAX_UNCOMPRESSED[source.kind] or count > MAX_RECORDS[source.kind]:
                    raise CatalogLoadError(f'{source.kind} decompression bound exceeded')
                if not line.endswith(b'\n'):
                    raise CatalogLoadError(f'{source.kind} incomplete JSONL line')
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise CatalogLoadError(f'{source.kind} record must be object')
                yield row
    except (OSError, EOFError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CatalogLoadError(f'{source.kind} invalid gzip or JSONL') from exc
    if count == 0:
        raise CatalogLoadError(f'{source.kind} is empty')


def _price(row: dict) -> str | None:
    prices = row.get('prices')
    if not isinstance(prices, dict):
        raise CatalogLoadError('card prices must be object')
    value = prices.get('usd')
    if value is None:
        return None
    if not isinstance(value, str):
        raise CatalogLoadError('USD price must be string or null')
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise CatalogLoadError('invalid USD price') from exc
    if not amount.is_finite() or amount < 0:
        raise CatalogLoadError('invalid USD price')
    return value


def _mana(value: object) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        raise CatalogLoadError('invalid cmc')
    return float(value)


def _optional_text(value: object, field: str, limit: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > limit:
        raise CatalogLoadError(f'{field} must be a bounded string or null')
    return value


def _image_uris(value: object) -> dict[str, str] | None:
    if value is None:
        return None
    if (not isinstance(value, dict) or len(value) > 12 or
            any(not isinstance(key, str) or not 1 <= len(key) <= 40 or
                not isinstance(url, str) or not 1 <= len(url) <= 2048 or
                urllib.parse.urlsplit(url).scheme != 'https' or
                not urllib.parse.urlsplit(url).netloc
                for key, url in value.items())):
        raise CatalogLoadError('image_uris must be a bounded HTTPS URL map or null')
    return value


def _authoring(row: dict) -> dict:
    return {
        'mana_cost': _optional_text(row.get('mana_cost'), 'mana_cost', 256),
        'rarity': _optional_text(row.get('rarity'), 'rarity', 64),
        'artist': _optional_text(row.get('artist'), 'artist', 512),
        'flavor_text': _optional_text(row.get('flavor_text'), 'flavor_text', 8192),
        'image_uris': _image_uris(row.get('image_uris')),
    }


def _card_data(row: dict) -> tuple[tuple[dict, ...], tuple[dict, ...], tuple]:
    if row.get('object') != 'card':
        raise CatalogLoadError('default_cards record is not a card')
    printing_id = _id(row.get('id'), 'printing id')
    name = _text(row.get('name'), 'name')
    lang = _text(row.get('lang'), 'lang')
    digital = row.get('digital')
    if not isinstance(digital, bool):
        raise CatalogLoadError('digital must be boolean')
    released = _text(row.get('released_at'), 'released_at')
    try:
        day = date.fromisoformat(released)
    except ValueError as exc:
        raise CatalogLoadError('invalid released_at') from exc
    faces = row.get('card_faces') or []
    if not isinstance(faces, list) or len(faces) > 10:
        raise CatalogLoadError('invalid card_faces')
    reversible = row.get('layout') == 'reversible_card'
    if reversible and len(faces) < 2:
        raise CatalogLoadError('reversible card requires multiple faces')
    face_data = tuple({'name': _text(f.get('name'), 'face name'),
                       'type_line': _text(f.get('type_line', ''), 'face type_line', empty=True),
                       'oracle_text': _text(f.get('oracle_text', ''), 'face oracle_text', empty=True),
                       'oracle_id': _id(f.get('oracle_id'), 'face oracle_id') if reversible else None,
                       'mana_value': _mana(f.get('cmc')) if reversible else None,
                       **_authoring(f)}
                      for f in faces if isinstance(f, dict))
    if len(face_data) != len(faces):
        raise CatalogLoadError('card face must be object')
    colors = row.get('color_identity')
    if (not isinstance(colors, list) or any(not isinstance(c, str) or c not in 'WUBRG' for c in colors)
            or len(set(colors)) != len(colors)):
        raise CatalogLoadError('invalid color_identity')
    legalities = row.get('legalities')
    if not isinstance(legalities, dict) or legalities.get('commander') not in ('legal', 'not_legal', 'banned', 'restricted'):
        raise CatalogLoadError('invalid commander legality')
    price = _price(row)
    common = {'printing_id': printing_id,
              'color_identity': ''.join(c for c in 'WUBRG' if c in colors),
              'commander_legal': int(legalities['commander'] == 'legal'),
              'price_usd': price, 'scryfall_uri': row.get('scryfall_uri'),
              'lang': lang, 'digital': int(digital), 'released_at': released,
              'set_code': _text(row.get('set'), 'set'),
              'collector_number': _text(row.get('collector_number'), 'collector_number'),
              'authoring': _authoring(row)}
    if reversible:
        # Two faces may share one Oracle identity (e.g. Propaganda // Propaganda).
        # Keep both in face_data, but associate the printing with that identity once.
        by_oracle: dict[str, dict] = {}
        for index, face in enumerate(face_data):
            by_oracle.setdefault(face['oracle_id'],
                                 {**common, 'oracle_id': face['oracle_id'], 'name': face['name'],
                                  'type_line': face['type_line'], 'oracle_text': face['oracle_text'],
                                  'mana_value': face['mana_value'], 'face_index': index})
        cards = tuple(by_oracle.values())
    else:
        cards = ({**common, 'oracle_id': _id(row.get('oracle_id'), 'oracle_id'), 'name': name,
                  'type_line': _text(row.get('type_line') or ' // '.join(f['type_line'] for f in face_data),
                                     'type_line', empty=True),
                  'oracle_text': _text(row.get('oracle_text') or ' // '.join(f['oracle_text'] for f in face_data),
                                       'oracle_text', empty=True),
                  'mana_value': _mana(row.get('cmc')), 'face_index': 0},)
    rank = (lang != 'en', digital, -day.toordinal(), printing_id)
    return cards, face_data, rank


def _load(db: sqlite3.Connection, snapshot: str, sources: dict[str, Source]) -> dict[str, int]:
    db.execute('PRAGMA foreign_keys=ON')
    create_schema(db)
    db.execute('INSERT INTO snapshots VALUES (?,?,?,?,?,?,?)',
               (snapshot, _now(), sources['default_cards'].updated_at, sources['oracle_tags'].updated_at,
                None, sources['default_cards'].url, sources['oracle_tags'].url))
    db.executemany('INSERT INTO dataset_sources VALUES (?,?,?,?,?,?,?)', [
        (snapshot, kind, source.url, source.updated_at, source.compressed_size,
         source.sha256, source.downloaded_at)
        for kind, source in sources.items()
    ])
    ranks: dict[str, tuple] = {}
    seen_printings: set[str] = set()
    counts = {kind: 0 for kind in KINDS}
    for row in _records(sources['default_cards']):
        cards, faces, rank = _card_data(row)
        printing_id = cards[0]['printing_id']
        if printing_id in seen_printings:
            raise CatalogLoadError(f'duplicate printing ID {printing_id}')
        seen_printings.add(printing_id)
        for card in cards:
            oid = card['oracle_id']
            if oid not in ranks:
                db.execute('INSERT INTO cards VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                           (snapshot, oid, printing_id, card['name'], card['type_line'], card['oracle_text'],
                            card['mana_value'], card['color_identity'], card['commander_legal'], card['price_usd'], card['scryfall_uri']))
            elif rank < ranks[oid]:
                db.execute('UPDATE cards SET printing_id=?, name=?, type_line=?, oracle_text=?, mana_value=?, '
                           'color_identity=?, commander_legal=?, price_usd=?, scryfall_uri=? WHERE snapshot_id=? AND oracle_id=?',
                           (printing_id, card['name'], card['type_line'], card['oracle_text'], card['mana_value'],
                            card['color_identity'], card['commander_legal'], card['price_usd'], card['scryfall_uri'], snapshot, oid))
                db.execute('DELETE FROM card_faces WHERE snapshot_id=? AND oracle_id=?', (snapshot, oid))
            if oid not in ranks or rank < ranks[oid]:
                ranks[oid] = rank
                for index, face in enumerate(faces):
                    db.execute('INSERT INTO card_faces VALUES (?,?,?,?,?,?,?,?)',
                               (snapshot, oid, index, face['oracle_id'], face['mana_value'],
                                face['name'], face['type_line'], face['oracle_text']))
            db.execute('INSERT INTO printings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (snapshot, printing_id, oid, card['face_index'], card['name'], card['type_line'], card['oracle_text'],
                        card['lang'], card['digital'], card['released_at'],
                        card['set_code'], card['collector_number'], card['price_usd'], card['scryfall_uri'],
                        json.dumps(faces, ensure_ascii=False),
                        json.dumps(card['authoring'], ensure_ascii=False)))
        counts['default_cards'] += 1
    for row in _records(sources['rulings']):
        if row.get('object') != 'ruling':
            raise CatalogLoadError('rulings record is not a ruling')
        oid = _id(row.get('oracle_id'), 'ruling oracle_id')
        if oid not in ranks:
            raise CatalogLoadError(f'ruling references unknown oracle_id {oid}')
        published = _text(row.get('published_at'), 'published_at')
        try:
            date.fromisoformat(published)
        except ValueError as exc:
            raise CatalogLoadError('invalid ruling date') from exc
        db.execute('INSERT OR IGNORE INTO rulings VALUES (?,?,?,?,?)',
                   (snapshot, oid, _text(row.get('source'), 'ruling source'),
                    published, _text(row.get('comment'), 'ruling comment')))
        counts['rulings'] += 1
    for row in _records(sources['oracle_tags']):
        try:
            tag, members = normalize_oracle_tag(row)
        except ValueError as exc:
            raise CatalogLoadError('invalid oracle tag') from exc
        tag_id = _id(tag['tag_id'], 'tag id')
        db.execute('INSERT INTO tags VALUES (?,?,?,?)', (snapshot, 'oracle', tag_id, tag['label']))
        for member in members:
            oid = _id(member, 'tagging oracle_id')
            if oid not in ranks:
                raise CatalogLoadError(f'tag references unknown oracle_id {oid}')
            db.execute('INSERT INTO card_tags VALUES (?,?,?,?)', (snapshot, oid, 'oracle', tag_id))
        counts['oracle_tags'] += 1
    if db.execute('PRAGMA foreign_key_check').fetchone() or db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise CatalogLoadError('SQLite integrity check failed')
    if any(value == 0 for value in counts.values()):
        raise CatalogLoadError('empty dataset')
    return counts


@contextmanager
def _exclusive_output(output_dir: Path):
    output_dir = Path(output_dir)
    def check_layout() -> None:
        if output_dir.is_symlink():
            raise CatalogLoadError('catalog output must not be a symlink')
        if not output_dir.exists():
            return
        entries = {p.name for p in output_dir.iterdir()} - {'.catalog-import.lock'}
        if entries - {'snapshots', 'staging', 'current.json'}:
            raise CatalogLoadError('non-catalog output directory is not allowed')
        snapshots = output_dir / 'snapshots'
        staging = output_dir / 'staging'
        current = output_dir / 'current.json'
        if (snapshots.is_symlink() or staging.is_symlink() or current.is_symlink()
                or (snapshots.exists() and not snapshots.is_dir())
                or (staging.exists() and not staging.is_dir())):
            raise CatalogLoadError('catalog path must not be a symlink or non-directory')
        if not current.exists() and snapshots.exists() and any(snapshots.iterdir()):
            raise CatalogLoadError('unpublished snapshots require operator review')
        if current.exists() and (not current.is_file() or not snapshots.is_dir()):
            raise CatalogLoadError('invalid catalog output directory')

    check_layout()  # Refuse an obvious wrong target without writing a lock file.
    output_dir.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(output_dir / '.catalog-import.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise CatalogLoadError('catalog import already in progress') from exc
        check_layout()  # Recheck after the lock in case another actor raced preflight.
        snapshots = output_dir / 'snapshots'
        staging = output_dir / 'staging'
        snapshots.mkdir(exist_ok=True, mode=0o700)
        staging.mkdir(exist_ok=True, mode=0o700)
        yield
    finally:
        os.close(lock_fd)


def _publish_locked(output_dir: Path, sources: dict[str, Source]) -> dict:
    if set(sources) != set(KINDS):
        raise CatalogLoadError('exactly three source datasets required')
    for kind, source in sources.items():
        if source.kind != kind:
            raise CatalogLoadError('source kind mismatch')
        _source_url(kind, source.url)
        if (isinstance(source.compressed_size, bool) or not 0 < source.compressed_size <= MAX_COMPRESSED[kind]
                or source.path.stat().st_size != source.compressed_size):
            raise CatalogLoadError(f'{kind} compressed size mismatch')
    snapshot = hashlib.sha256((f'schema-{SCHEMA_VERSION}:' + ''.join(
        sources[k].sha256 for k in KINDS)).encode()).hexdigest()[:24]
    output_dir = Path(output_dir)
    final_dir = output_dir / 'snapshots'
    final_db = final_dir / f'{snapshot}.sqlite'
    final_meta = final_dir / f'{snapshot}.json'
    if final_db.exists() or final_meta.exists():
        raise CatalogLoadError('snapshot already exists')
    with tempfile.TemporaryDirectory(prefix='.catalog-stage-', dir=output_dir / 'staging') as work:
        stage = Path(work)
        database = stage / 'catalog.sqlite'
        try:
            with closing(sqlite3.connect(database)) as db:
                with db:
                    counts = _load(db, snapshot, sources)
        except (sqlite3.Error, OSError) as exc:
            raise CatalogLoadError('catalog build failed') from exc
        metadata = {'snapshot_id': snapshot, 'schema_version': SCHEMA_VERSION,
                    'published_at': _now(), 'counts': counts,
                    'sources': {kind: {'url': item.url, 'updated_at': item.updated_at,
                                       'compressed_size': item.compressed_size, 'sha256': item.sha256,
                                       'downloaded_at': item.downloaded_at}
                                for kind, item in sources.items()}}
        stage_meta = stage / 'snapshot.json'
        stage_meta.write_text(json.dumps(metadata, indent=2) + '\n')
        with database.open('rb') as handle:
            os.fsync(handle.fileno())
        with stage_meta.open('rb') as handle:
            os.fsync(handle.fileno())
        database.chmod(0o444)
        stage_meta.chmod(0o444)
        created_db = created_meta = False
        try:
            os.link(database, final_db)
            created_db = True
            os.link(stage_meta, final_meta)
            created_meta = True
            _fsync_directory(final_dir)
            active = stage / 'current.json'
            active.write_text(json.dumps({'snapshot_id': snapshot, 'database': str(final_db),
                                          'metadata': str(final_meta)}, indent=2) + '\n')
            with active.open('rb') as handle:
                os.fsync(handle.fileno())
        except OSError:
            if created_db:
                final_db.unlink(missing_ok=True)
            if created_meta:
                final_meta.unlink(missing_ok=True)
            raise
        try:
            os.replace(active, output_dir / 'current.json')
        except OSError:
            final_db.unlink(missing_ok=True)
            final_meta.unlink(missing_ok=True)
            raise
        try:
            _fsync_directory(output_dir)
        except OSError as exc:
            raise CatalogLoadError('snapshot published but directory durability is uncertain') from exc
    return metadata


def publish_from_files(output_dir: Path, sources: dict[str, Source]) -> dict:
    """Validate local official gzip files and atomically publish a new snapshot."""
    with _exclusive_output(output_dir):
        return _publish_locked(output_dir, sources)


def download_and_publish(output_dir: Path) -> dict:
    with _exclusive_output(output_dir):
        manifest = fetch_manifest()
        with tempfile.TemporaryDirectory(prefix='commander-gym-catalog-') as work:
            sources = {kind: download_source(kind, manifest[kind], Path(work)) for kind in KINDS}
            return _publish_locked(output_dir, sources)


def main() -> None:
    parser = argparse.ArgumentParser(description='Download and publish an official local Scryfall catalog snapshot')
    parser.add_argument('--output', required=True, type=Path, help='private local catalog directory')
    args = parser.parse_args()
    print(json.dumps(download_and_publish(args.output), indent=2))


if __name__ == '__main__':
    main()
