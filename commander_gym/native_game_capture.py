"""Local native producer import and per-game sidecar recording; no provider calls.

Only masked callback arguments go to a pilot. The native source is read solely by
the recorder and offline analysis. Historical sources are never deleted or changed.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
from collections.abc import Sequence
from itertools import islice
import copy
from .record_codec import NATIVE_MAX_RECORD_BYTES, decode_record, physical_lines

from .game_journal import (
    JournalError, PIN_KEYS, ZERO, PrivateGameJournal, RecorderSeatSink,
    _json, _private_dir, _safe, RowSlice, inspect_journal, publish_finalized_manifest, verify_finalized_manifest,
)


@dataclass
class NativeCursor:
    offset: int = 0
    sequence: int = 0
    previous: str = ZERO
    revision: str | None = None
    terminal: bool = False
    inode: tuple[int, int] | None = None
    revisions: set[str] = field(default_factory=set)


def iter_native_source(directory: Path, cursor: NativeCursor | None = None, *, stop_offset: int | None = None):
    """Verify complete newline-terminated rows; an incomplete live tail stays pending.

    A closed source has a terminal as its last row. No prefix is promoted to a
    complete run. SHA checks cover the producer's exact UTF-8 body, not re-encoding.
    """
    _private_dir(directory)
    path = directory / 'native-000000.ndjson'
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise JournalError('native source must be private and not a symlink')
    cursor = cursor if cursor is not None else NativeCursor()
    stat = path.stat()
    inode = (stat.st_dev, stat.st_ino)
    if (cursor.inode is not None and cursor.inode != inode) or stat.st_size < cursor.offset:
        raise JournalError('native source replaced or shortened')
    cursor.inode = inode
    with path.open('rb') as stream:
        stream.seek(cursor.offset)
        lines = physical_lines(stream, max_bytes=NATIVE_MAX_RECORD_BYTES)
        while stop_offset is None or cursor.offset < stop_offset:
            try:
                physical = next(lines)
            except StopIteration:
                break
            if not physical.endswith(b'\n'):
                break
            try:
                wrapper = json.loads(decode_record(physical, max_bytes=NATIVE_MAX_RECORD_BYTES))
                if not isinstance(wrapper, dict) or not isinstance(wrapper.get('body'), str):
                    raise JournalError('invalid native wrapper')
                digest = hashlib.sha256(wrapper['body'].encode('utf-8')).hexdigest()
                body = json.loads(wrapper['body'])
                if (wrapper.get('sha256') != digest or not isinstance(body, dict) or
                        type(body.get('schemaVersion')) is not int or body.get('schemaVersion') != 1 or body.get('gameId') != directory.name or
                        type(body.get('sequence')) is not int or body['sequence'] != cursor.sequence + 1 or
                        body.get('previousSha256') != cursor.previous or cursor.terminal):
                    raise JournalError('native source integrity or ordering failure')
                if not re.fullmatch(r'[0-9a-f]{40}', body.get('engineRevision', '')):
                    raise JournalError('native source exact revision unavailable')
                if not isinstance(body.get('payload'), dict) or body.get('visibility') not in {'seat', 'admin'}:
                    raise JournalError('invalid native payload or visibility')
                if body['visibility'] == 'seat' and not isinstance(body.get('seatId'), str):
                    raise JournalError('native seat identity unavailable')
                _safe(body)
                cursor.previous, cursor.revision = digest, body['engineRevision']
                cursor.revisions.add(body['engineRevision'])
                cursor.sequence += 1
                cursor.offset += len(physical)
                cursor.terminal = body['kind'] in {'terminal', 'session_closed'}
                yield {'native': body, 'source_body': wrapper['body'], 'source_sha256': digest}
            except (ValueError, TypeError, KeyError) as error:
                raise JournalError('native source verification failed') from error


class NativeRows(Sequence):
    """Reiterable verified physical range; only a single decoded row is resident."""
    def __init__(self, directory, start, end, count):
        self.directory, self.start, self.end, self.count = directory, start, end, count
    def __len__(self): return self.count
    def __iter__(self):
        cursor = copy.deepcopy(self.start)
        yield from iter_native_source(self.directory, cursor, stop_offset=self.end.offset)
        if cursor.offset != self.end.offset or cursor.previous != self.end.previous:
            raise JournalError('native prefix changed during read')
    def __eq__(self, other):
        return isinstance(other, Sequence) and len(self) == len(other) and all(a == b for a, b in zip(self, other))
    def __getitem__(self, index):
        if isinstance(index, slice):
            return RowSlice(self, range(*index.indices(self.count)))
        if index < 0: index += self.count
        if not 0 <= index < self.count: raise IndexError(index)
        return next(islice(iter(self), index, index + 1))


def read_native_source(directory: Path, cursor: NativeCursor | None = None) -> Sequence:
    """Legacy indexed reader contract, now disk-backed and memory-bounded."""
    cursor = cursor if cursor is not None else NativeCursor()
    start = copy.deepcopy(cursor)
    count = 0
    for _ in iter_native_source(directory, cursor): count += 1
    return NativeRows(directory, start, copy.deepcopy(cursor), count)


class NativeGameCapture:
    """One process owns Gym journals; Argentum independently owns its native source.

    Pins are supplied privately by the operator. Native setup supplies the actual
    engine/decks/config/RNG rather than guessing them from the selected profile.
    """
    def __init__(self, root: Path, declared_pins: Mapping[str, Any], *, terminal_grace_seconds: int = 600) -> None:
        _private_dir(root)
        if set(declared_pins) != set(PIN_KEYS):
            raise JournalError('recording pins require every explicit pin key')
        _safe(declared_pins)
        if not isinstance(declared_pins['gym'], str) or not re.fullmatch(r'[0-9a-f]{40}', declared_pins['gym']):
            raise JournalError('recording requires exact Gym commit')
        self.root, self.declared_pins = root, dict(declared_pins)
        if type(terminal_grace_seconds) is not int or not 1 <= terminal_grace_seconds <= 86400:
            raise JournalError('invalid terminal callback drain limit')
        self.terminal_grace_seconds = terminal_grace_seconds
        self._terminal_since: dict[str, float] = {}
        self._lock = threading.RLock()
        self._journals: dict[str, PrivateGameJournal] = {}
        self._cursors: dict[str, NativeCursor] = {}
        self._initializations: dict[str, dict[str, Any]] = {}
        self._finalized_bytes: dict[str, int] = {}
        self._terminals: dict[str, dict[str, Any]] = {}
        self._stop = threading.Event()
        self.errors: dict[str, str] = {}
        self._thread: threading.Thread | None = None

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> NativeGameCapture | None:
        root, pin_path = environment.get('COMMANDER_GYM_RECORDING_ROOT'), environment.get('COMMANDER_GYM_RECORDING_PINS')
        if not root and not pin_path:
            return None
        if not root or not pin_path:
            raise JournalError('recording root and private pins file must be configured together')
        path = Path(pin_path)
        if path.is_symlink() or path.stat().st_mode & 0o077:
            raise JournalError('recording pins must be private and not a symlink')
        return cls(Path(root), json.loads(path.read_bytes()))

    def _journal(self, game_id: str, rows: list[dict[str, Any]]) -> PrivateGameJournal:
        if game_id in self._journals:
            return self._journals[game_id]
        initialization = self._initializations.get(game_id) or next((row['native'] for row in rows if row['native']['kind'] == 'initialization'), None)
        if initialization is None:
            raise JournalError('native initialization not available yet')
        setup = initialization['payload']['setup']
        pins = {**self.declared_pins, 'engine': initialization['engineRevision'],
                'decks': {'players': setup['players'], 'pinnedCards': initialization['payload']['pinnedCards']},
                'rng': {'seed': setup['seed'], 'source': 'native_initialization'},
                'config': {'native': {k: v for k, v in setup.items() if k not in {'players', 'seed'}},
                           'gym': self.declared_pins['config'],
                           'declared_engine': self.declared_pins['engine']}}
        if (self.root / game_id / '000000.jsonl').exists():
            # Preserve immutable first-run pins and append current runtime provenance separately.
            pins = inspect_journal(self.root / game_id)['rows'][0]['payload']['pins']
        journal = PrivateGameJournal(self.root / game_id, game_id, pins, game_id=game_id)
        journal.append('runtime_context', {'declared_pins': self.declared_pins},
                       source='gym-runtime', source_sequence=journal.sources.get('gym-runtime', 0))
        self._journals[game_id] = journal
        return journal

    def seat_sink(self, game_id: str, seat_id: str) -> RecorderSeatSink:
        with self._lock:
            self._validate_id(game_id)
            rows = [] if game_id in self._journals else read_native_source(self.root / game_id)
            return RecorderSeatSink(self._journal(game_id, rows), seat_id, gym_revision=self.declared_pins['gym'])

    @staticmethod
    def _validate_id(game_id: str) -> None:
        if not isinstance(game_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', game_id):
            raise JournalError('invalid native game identity')

    def scan(self) -> None:
        """Import stable rows and finalize every native terminal, including human-only games."""
        with self._lock:
            for directory in sorted(self.root.iterdir()):
                if directory.is_symlink() or not directory.is_dir() or not (directory / 'native-000000.ndjson').exists():
                    continue
                game_id = directory.name
                try:
                    self._validate_id(game_id)
                    if (directory / 'manifest.json').exists():
                        if game_id not in self._finalized_bytes:
                            manifest = verify_finalized_manifest(directory)
                            self._finalized_bytes[game_id] = manifest['storage_bytes']
                        continue
                    if game_id not in self._journals and (directory / '000000.jsonl').exists() and inspect_journal(directory)['closed']:
                        publish_finalized_manifest(directory)
                        continue
                    cursor = self._cursors.setdefault(game_id, NativeCursor())
                    # Start from the last durable imported source row after a recorder restart.
                    if cursor.offset == 0 and (directory / '000000.jsonl').exists():
                        prior = inspect_journal(directory)
                        source_rows = ({'native': json.loads(r['payload']['source_body']), 'source_body': r['payload']['source_body']}
                                       for r in prior['rows'] if r['source'] == 'native')
                        for item in source_rows:
                            if item['native']['kind'] == 'initialization':
                                self._initializations[game_id] = item['native']
                            if item['native']['kind'] in {'terminal', 'session_closed'}:
                                self._terminals[game_id] = {**item['native']['payload'], 'source_kind': item['native']['kind']}
                        # Verify the source prefix once; subsequent scans read only appended bytes.
                        verified = read_native_source(directory, cursor)
                        imported_count = prior['sources'].get('native', 0)
                        source_rows = (r['payload']['source_body'] for r in prior['rows'] if r['source'] == 'native')
                        from itertools import zip_longest
                        if any(a != b['source_body'] if isinstance(b, dict) else True
                               for a, b in zip_longest(source_rows, verified[:imported_count])):
                            raise JournalError('native prefix differs from durable import')
                        rows = verified[imported_count:]
                    else:
                        rows = read_native_source(directory, cursor)
                    for item in rows:
                        if item['native']['kind'] == 'initialization':
                            self._initializations[game_id] = item['native']
                        if item['native']['kind'] in {'terminal', 'session_closed'}:
                            self._terminals[game_id] = {**item['native']['payload'], 'source_kind': item['native']['kind']}
                    if game_id not in self._initializations:
                        self._cursors.pop(game_id, None)
                        continue
                    journal = self._journal(game_id, rows)
                    with journal._lock:
                        imported = journal.sources.get('native', 0)
                        if imported > cursor.sequence:
                            raise JournalError('native source lost previously imported rows')
                        for item in rows:
                            if item['native']['sequence'] <= imported:
                                continue
                            body, payload = item['native'], item['native']['payload']
                            seat = body.get('seatId') if body['visibility'] == 'seat' else None
                            kind = 'native_seat_observation' if seat is not None else 'native_source'
                            normalized = dict(item)
                            if seat is None:
                                normalized['native'] = {k: v for k, v in body.items() if k != 'payload'}
                            if body['kind'] == 'native_transition':
                                kind = 'native_transition'
                                normalized.update(game_id=game_id, native_schema='argentum-native-evidence-v1',
                                    action=payload['action'], events=payload['result']['events'],
                                    result={'outcome': payload['result']['outcome'], 'native_result_in_source_body': True},
                                    before_state_digest=payload['beforeStateDigest'],
                                    after_state_digest=payload['effectiveStateDigest'])
                            journal.append(kind, normalized, seat_id=seat, source='native', source_sequence=body['sequence'] - 1)
                        if cursor.terminal and game_id in self._terminals:
                            terminal = self._terminals[game_id]
                            since = self._terminal_since.setdefault(game_id, time.monotonic())
                            if journal.pending_decisions and time.monotonic() - since < self.terminal_grace_seconds:
                                continue  # preserve later paid-response/usage receipts during concession
                            gaps = ['canonical_game_server_training_adapter_unavailable']
                            if list(directory.glob('native-gap-*.json')):
                                gaps.append('native_capture_gap_marker')
                            if len(cursor.revisions) > 1:
                                gaps.append('native_engine_revision_changed')
                            if terminal.get('administrativeStall'):
                                gaps.append('administrative_stall_not_native_rules_outcome')
                            if journal.pending_decisions:
                                gaps.append('callback_completion_unavailable_after_terminal_timeout')
                            journal.finish({'kind': ('finalized_aborted' if terminal.get('administrativeStall') else
                                         'native_terminal' if terminal['source_kind'] == 'terminal' else 'session_closed'), 'game_id': game_id,
                                'winner_id': terminal['winnerId'], 'administrative_stall': terminal.get('administrativeStall')},
                                expected_sources=dict(journal.sources),
                                gaps=gaps)
                            journal.close()
                            self._journals.pop(game_id, None)
                    self.errors.pop(game_id, None)
                except (OSError, ValueError, KeyError, TypeError) as error:
                    # Class only: no credential-bearing exception text or raw request headers.
                    self.errors[game_id] = type(error).__name__
                    self._cursors.pop(game_id, None)  # retry from durable prefix after uncertain import

    def start(self) -> None:
        if self._thread is not None:
            raise JournalError('capture scanner already started')
        def loop() -> None:
            while not self._stop.is_set():
                self.scan()
                self._stop.wait(1)
        self._thread = threading.Thread(target=loop, name='private-game-capture', daemon=True)
        self._thread.start()

    def operational_metrics(self) -> dict[str, Any]:
        """Aggregate durable evidence only; pending tails/choices prevent drain acknowledgement."""
        with self._lock:
            pending = 0
            durable = sum(self._finalized_bytes.values())
            for directory in self.root.iterdir():
                source = directory / 'native-000000.ndjson'
                if directory.is_symlink() or not directory.is_dir() or not source.exists():
                    continue
                if directory.name in self._finalized_bytes:
                    continue
                pending += 1  # unresolved sources, including orphan crash prefixes, require explicit finalization
                cursor = self._cursors.get(directory.name)
                if cursor is None or cursor.offset != source.stat().st_size:
                    pending += 1
                if cursor is not None:
                    durable += cursor.offset
                    if cursor.terminal:
                        pending += 1  # terminal import is not drained until immutable manifest verifies
                journal = self._journals.get(directory.name)
                if journal is not None:
                    with journal._lock:
                        pending += len(journal.pending_decisions)
                        durable += sum(p.stat().st_size for p in directory.glob('[0-9]*.jsonl'))
            return {'healthy': not self.errors, 'pendingRecordWrites': pending, 'durableBytes': durable}

    def health(self) -> dict[str, Any]:
        """Read-only operational metadata; no game state, model text or seat context."""
        with self._lock:
            return {'active_games': sorted(self._journals), 'errors': dict(self.errors),
                    'native_gap_games': sorted(p.name for p in self.root.iterdir()
                        if p.is_dir() and not p.is_symlink() and any(p.glob('native-gap-*.json'))),
                    'terminal_waiting_for_callbacks': sorted(game for game, journal in self._journals.items()
                        if game in self._terminals and journal.pending_decisions)}

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        with self._lock:
            self.scan()
            for journal in self._journals.values():
                journal.close()  # unsealed prefixes remain incomplete, never operator_stop rewritten
            self._journals.clear()
