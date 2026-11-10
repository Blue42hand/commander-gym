"""Resource-limited, read-only journal inspection for the administrative panel."""
from __future__ import annotations
import json
import re
import resource
import sys
import time
from pathlib import Path


def inspect(directory: Path, seconds: float = 30) -> dict:
    # Panel-only reader limits in this isolated process. Never change producers.
    from . import record_codec, game_journal
    from .game_journal import JournalError, verify_finalized_manifest
    limit = 2 * 1024 * 1024
    original_lines, original_decode = game_journal.physical_lines, game_journal.decode_record
    original_inspect = game_journal.inspect_journal
    deadline = time.monotonic() + seconds
    result = {'verification': 'unavailable', 'issues': [], 'verified_at': None}
    anchors, physical_sequence = [], 0

    def panel_lines(stream, **kwargs):
        nonlocal physical_sequence
        try:
            for physical in original_lines(stream, max_bytes=limit, **kwargs):
                if physical_sequence % 128 == 0 and len(anchors) < 5000:
                    anchors.append([physical_sequence, int(Path(stream.name).stem), stream.tell() - len(physical)])
                physical_sequence += 1
                yield physical
        except record_codec.RecordCodecError as error:
            raise JournalError(str(error)) from error

    def panel_decode(physical, **kwargs):
        try:
            return original_decode(physical, max_bytes=limit, **kwargs)
        except record_codec.RecordCodecError as error:
            # An over-limit decoded frame is a panel size limitation; an
            # unsupported codec/version remains a malformed-source diagnostic.
            if str(error) == 'unsupported_record_frame':
                frame = json.loads(physical)
                if isinstance(frame, dict) and type(frame.get('decodedBytes')) is int and frame['decodedBytes'] > limit:
                    raise JournalError('record_size_or_framing_limit') from error
            raise JournalError(str(error)) from error

    def observed_inspection(*args, **kwargs):
        report = original_inspect(*args, **kwargs)
        result['issues'] = report['issues']
        result['prefix_rows'] = len(report['rows'])
        result['prefix_hash'] = report['root_sha256']
        first, last = report['rows'].first, report['rows'].last
        if first:
            result.update(metadata(first))
        if last:
            result['last_recorded_at'] = last.get('recorded_at')
            result['last_event'] = last['sequence']
        return report

    game_journal.physical_lines, game_journal.decode_record = panel_lines, panel_decode
    game_journal.inspect_journal = observed_inspection
    try:
        if (directory / 'manifest.json').exists():
            # The existing verifier calls inspect_journal itself. Capture that
            # report and sparse offsets, avoiding another full archive scan.
            manifest = verify_finalized_manifest(directory, cancel=lambda: time.monotonic() > deadline)
            result.update(verification='verified', verified_at=time.time(), artifact_hash=manifest['artifact_hash'])
        else:
            report = observed_inspection(directory, cancel=lambda: time.monotonic() > deadline)
            result['verification'] = 'unsealed' if report['integrity_ok'] else 'damaged'
    except record_codec.ScanCancelled:
        result['verification'] = 'budget-limited'
        result['issues'].append('Inspection time budget reached; verification incomplete')
    except MemoryError:
        result['verification'] = 'too-large'
        result['issues'].append('Inspection memory budget reached')
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
        result['verification'] = 'unavailable' if isinstance(error, OSError) else 'damaged'
        result['issues'].append('Inspection failed: ' + (str(error)[:200] if isinstance(error, JournalError) else type(error).__name__))
    if any('record_size' in i for i in result['issues']):
        result['verification'] = 'too-large'
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
    gym_config=config.get('gym',config) if isinstance(config,dict) else {}
    batch_ref=None
    if isinstance(gym_config,dict) and re.fullmatch(r'batch-[a-f0-9]{32}',str(gym_config.get('batchId',''))) and re.fullmatch(r'attempt-[a-f0-9]{32}',str(gym_config.get('attemptId',''))):
        batch_ref={'batch_id':gym_config['batchId'],'attempt_id':gym_config['attemptId']}
    return {'run_id': first.get('run_id'), 'game_id': first.get('payload', {}).get('game_id'),'batch_ref':batch_ref,
            'started_at': first.get('recorded_at'), 'participants': participants,
            'pins': {k: pins.get(k) for k in ('engine', 'gym', 'models', 'bindings', 'rng')},
            'config_digest': first.get('payload', {}).get('pin_digest')}


def main():
    resource.setrlimit(resource.RLIMIT_AS, (320 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
    print(json.dumps(inspect(Path(sys.argv[1]))))


if __name__ == '__main__':
    main()
