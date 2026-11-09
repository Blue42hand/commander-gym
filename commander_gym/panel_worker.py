"""Resource-limited, read-only journal inspection for the administrative panel."""
from __future__ import annotations
import json
import resource
import sys
import time
from pathlib import Path


def inspect(directory: Path, seconds: float = 12) -> dict:
    # Lower than the recorder's format maximum. Never change producer limits.
    from . import record_codec
    record_codec.MAX_RECORD_BYTES = 2 * 1024 * 1024
    from . import game_journal
    from .game_journal import JournalError, inspect_journal, verify_finalized_manifest
    original_lines, original_decode = game_journal.physical_lines, game_journal.decode_record
    def panel_lines(stream, **kwargs):
        try:
            yield from original_lines(stream, max_bytes=record_codec.MAX_RECORD_BYTES, **kwargs)
        except record_codec.RecordCodecError as error:
            raise JournalError(str(error)) from error
    def panel_decode(physical, **kwargs):
        try:
            return original_decode(physical, max_bytes=record_codec.MAX_RECORD_BYTES, **kwargs)
        except record_codec.RecordCodecError as error:
            raise JournalError(str(error)) from error
    game_journal.physical_lines, game_journal.decode_record = panel_lines, panel_decode
    deadline = time.monotonic() + seconds
    result = {'verification': 'unavailable', 'issues': [], 'verified_at': None}
    try:
        report = inspect_journal(directory, cancel=lambda: time.monotonic() > deadline)
        result['issues'] = report['issues']
        result['prefix_rows'] = len(report['rows'])
        result['prefix_hash'] = report['root_sha256']
        first, last = report['rows'].first, report['rows'].last
        if first:
            result.update(metadata(first))
        if last:
            result['last_recorded_at'] = last.get('recorded_at')
            result['last_event'] = last['sequence']
        if any('record_' in i or 'unsupported_record_frame' in i for i in result['issues']):
            result['verification'] = 'too-large'
        elif not report['integrity_ok']:
            result['verification'] = 'damaged'
        elif (directory / 'manifest.json').exists():
            manifest = verify_finalized_manifest(directory, cancel=lambda: time.monotonic() > deadline)
            result.update(verification='verified', verified_at=time.time(), artifact_hash=manifest['artifact_hash'])
        else:
            result['verification'] = 'unsealed'
    except record_codec.ScanCancelled:
        result['verification'] = 'budget-limited'
        result['issues'].append('Inspection time budget reached; verification incomplete')
    except (OSError, ValueError, TypeError, KeyError, MemoryError) as error:
        result['verification'] = 'unavailable' if isinstance(error, OSError) else 'damaged'
        # Never include raw parser content, host paths or possible model text.
        result['issues'].append('Inspection failed: ' + type(error).__name__)
    # Rebuildable sparse seek map. It is never written into the capture.
    # Keep enough offsets to jump to evidence without rescanning the whole game.
    anchors = []
    try:
        for file_index, path in enumerate(sorted(directory.glob('*.jsonl'))):
            if path.is_symlink():
                break
            with path.open('rb') as stream:
                while time.monotonic() < deadline:
                    offset = stream.tell()
                    physical = stream.readline(record_codec.MAX_RECORD_BYTES + 1)
                    if not physical or len(physical) > record_codec.MAX_RECORD_BYTES:
                        break
                    row = json.loads(record_codec.decode_record(physical, max_bytes=record_codec.MAX_RECORD_BYTES))
                    sequence = row.get('sequence')
                    if type(sequence) is int and sequence % 128 == 0 and len(anchors) < 5000:
                        anchors.append([sequence, file_index, offset])
                if time.monotonic() >= deadline:
                    break
    except (OSError, ValueError, TypeError):
        pass
    result['evidence_index'] = anchors
    return result


def metadata(first: dict) -> dict:
    pins = first.get('payload', {}).get('pins', {})
    if not isinstance(pins, dict):
        return {}
    decks = pins.get('decks') or {}
    config = pins.get('config') or {}
    players = decks.get('players', []) if isinstance(decks, dict) else []
    roster = config.get('native', {}).get('seatRoster', []) if isinstance(config, dict) else []
    seats = {p.get('playerId'): p.get('seatIndex') for p in roster if isinstance(p, dict)}
    participants = []
    for i, p in enumerate(players[:16]):
        if not isinstance(p, dict):
            continue
        deck = p.get('deck') or {}
        participants.append({'seat': seats.get(p.get('playerId'), i), 'id': p.get('playerId'),
                             'name': p.get('name'), 'deck': p.get('commanderCardName') or deck.get('name') or deck.get('commander') or 'Deck name not recorded'})
    participants.sort(key=lambda p: p['seat'] if isinstance(p['seat'], int) else 999)
    return {'run_id': first.get('run_id'), 'game_id': first.get('payload', {}).get('game_id'),
            'started_at': first.get('recorded_at'), 'participants': participants,
            'pins': {k: pins.get(k) for k in ('engine', 'gym', 'models', 'bindings', 'rng')},
            'config_digest': first.get('payload', {}).get('pin_digest')}


def main():
    resource.setrlimit(resource.RLIMIT_AS, (320 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
    print(json.dumps(inspect(Path(sys.argv[1]))))


if __name__ == '__main__':
    main()
