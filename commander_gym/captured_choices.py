"""Convert a bounded accepted native AI-choice subset offline; never certify a run.

Only native seat evidence enters records. Admin transitions establish application
identity; their states/events are never copied to pilot observations or targets.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from .game_journal import JournalError, inspect_journal, _json, _private_dir, _sync_dir
from .identity import IdentityRef
from .pilot_execution import PilotExecutionTrace
from .pilot_records import PilotRecordContext, decision_record_from_execution_trace

SCHEMA_HASH = hashlib.sha256(b'argentum-ai-enumerated-seat-evidence-v1').hexdigest()


def verified_observation(context: Mapping[str, Any], seat: str) -> dict[str, Any]:
    if (not isinstance(context, Mapping) or type(context.get('version')) is not int or
            context['version'] != 1 or context.get('schemaHash') != SCHEMA_HASH or
            not isinstance(context.get('correlationId'), str) or not context['correlationId'] or
            not isinstance(context.get('observationBody'), str)):
        raise JournalError('unsupported native decision evidence')
    if hashlib.sha256(context['observationBody'].encode('utf-8')).hexdigest() != context.get('stateDigest'):
        raise JournalError('native decision observation hash mismatch')
    observation = json.loads(context['observationBody'])
    if (not isinstance(observation, dict) or set(observation) != {'type', 'state', 'legalActions',
            'pendingDecision', 'recentGameLog', 'perspectivePlayerId', 'agentToAct', 'terminated'} or
            observation['type'] != 'GameServerSeat' or observation['perspectivePlayerId'] != seat or
            observation['agentToAct'] != seat or not isinstance(observation['state'], dict) or
            observation['state'].get('viewingPlayerId') != seat or observation['pendingDecision'] is not None or
            not isinstance(observation['legalActions'], list) or type(observation['terminated']) is not bool):
        raise JournalError('native decision seat or observation shape mismatch')
    return observation


def accepted_choice_records(directory: Path) -> tuple[list[Any], dict[str, int]]:
    """Return existing DecisionRecord objects and aggregate exclusion reasons.

    A closed verified capture is required. A record does not imply complete game
    recording, dataset membership or exact replay. No historical capture is rewritten.
    """
    report = inspect_journal(directory)
    if not report['closed']:
        raise JournalError('accepted-choice conversion requires a closed verified journal')
    from .native_game_capture import NativeCursor, read_native_source
    cursor = NativeCursor()
    source = read_native_source(directory, cursor)
    imported = [row['payload']['source_body'] for row in report['rows'] if row.get('source') == 'native']
    if (not cursor.terminal or cursor.offset != (directory / 'native-000000.ndjson').stat().st_size or
            imported != [row['source_body'] for row in source]):
        raise JournalError('native source differs from sealed journal or is incomplete')
    callbacks: dict[str, list[Any]] = defaultdict(list)
    starts: dict[str, list[Any]] = defaultdict(list)
    inputs: dict[str, list[Any]] = defaultdict(list)
    results: dict[str, list[Any]] = defaultdict(list)
    dispositions: set[str] = set()
    players = None
    revisions: set[str] = set()
    previous = None
    unsupported_source = False
    for row in report['rows']:
        payload = row['payload']
        if row['kind'] in {'decision_started', 'seat_callback'}:
            (starts if row['kind'] == 'decision_started' else callbacks)[payload['decision_id']].append(row)
        if row.get('source') != 'native':
            continue
        exact = payload['source_body']
        if hashlib.sha256(exact.encode('utf-8')).hexdigest() != payload['source_sha256']:
            raise JournalError('native source body hash mismatch')
        body = json.loads(exact)
        if body['gameId'] != report['run_id'] or type(body['schemaVersion']) is not int or body['schemaVersion'] != 1:
            raise JournalError('native game or schema mismatch')
        revisions.add(body['engineRevision'])
        if body['kind'] == 'resume':
            unsupported_source = True
        native = body['payload']
        if body['kind'] == 'initialization':
            if players is not None:
                unsupported_source = True
            players = native['setup']['players']
        if body['kind'] in {'ai_decision_input', 'ai_decision_result', 'ai_decision_disposition'}:
            if body.get('visibility') != 'seat' or not isinstance(body.get('seatId'), str):
                raise JournalError('native decision evidence must be seat-visible')
            cid = native['correlationId']
            if body['kind'] == 'ai_decision_input':
                inputs[cid].append((body, payload['source_sha256']))
            elif body['kind'] == 'ai_decision_result':
                results[cid].append((body, payload['source_sha256'], previous))
            else:
                dispositions.add(cid)
        previous = body
    diagnostics: Counter[str] = Counter()
    records = []
    if unsupported_source or len(revisions) != 1 or players is None or list(directory.glob('native-gap-*.json')):
        return [], {'unsupported_or_incomplete_native_source': len(callbacks)}
    seats = {player['playerId']: (index, player) for index, player in enumerate(players)}
    for cid, completed in callbacks.items():
        try:
            if (len(completed) != 1 or len(starts[cid]) != 1 or len(inputs[cid]) != 1 or
                    len(results[cid]) != 1 or cid in dispositions):
                raise JournalError('missing_duplicate_or_diagnostic_join')
            callback, start = completed[0], starts[cid][0]
            input_body, input_hash = inputs[cid][0]
            result_body, result_hash, transition = results[cid][0]
            seat = callback['seat_id']
            payload, context, result = callback['payload'], input_body['payload'], result_body['payload']
            if (seat not in seats or start['seat_id'] != seat or input_body['seatId'] != seat or
                    result_body['seatId'] != seat or payload.get('decision_evidence') != context or
                    start['payload'].get('decision_evidence') != context or start['sequence'] >= callback['sequence'] or
                    type(result.get('version')) is not int or result.get('version') != 1 or result.get('status') != 'accepted' or
                    result.get('inputStateDigest') != context['stateDigest']):
                raise JournalError('mismatched_or_unaccepted_join')
            native = verified_observation(context, seat)
            observation = payload['observation']
            if (set(observation) != set(native) | {'schemaHash', 'stateDigest', 'knownDeck'} or
                    not isinstance(observation['knownDeck'], dict) or
                    not set(observation['knownDeck']) <= {'cards', 'archetype'} or
                    ('archetype' in observation['knownDeck'] and not isinstance(observation['knownDeck']['archetype'], str))):
                raise JournalError('unverified_observation_additions')
            if start['payload']['observation'] != observation or any(observation.get(k) != v for k, v in native.items()):
                raise JournalError('callback_observation_mismatch')
            if (observation.get('schemaHash') != context['schemaHash'] or
                    observation.get('stateDigest') != context['stateDigest']):
                raise JournalError('callback_digest_mismatch')
            choice = payload['choice']
            index = choice.get('actionId')
            if (choice.get('channel') != 'action' or type(index) is not int or not 0 <= index < len(native['legalActions']) or
                    choice.get('params') != {}):
                raise JournalError('unsupported_choice_channel_or_parameters')
            offered = native['legalActions'][index]
            if (result.get('actionId') != index or result.get('semanticId') != offered.get('semanticId') or
                    result.get('action') != offered.get('action') or transition is None or
                    transition['kind'] != 'native_transition' or transition['payload']['action'] != result['action'] or
                    transition['payload'].get('administrativeStall') or transition['payload']['result'].get('error') is not None):
                raise JournalError('application_or_selected_action_mismatch')
            after_context = result['resultObservation']
            after = verified_observation(after_context, seat)
            after.update(schemaHash=after_context['schemaHash'], stateDigest=after_context['stateDigest'])
            lineage = payload['lineage']
            if lineage != start['payload'].get('lineage'):
                raise JournalError('lineage_changed_during_choice')
            refs = {key: IdentityRef.from_dict(lineage[key]) for key in ('binding', 'deck', 'pilot')}
            if any(ref.artifact_type != key for key, ref in refs.items()):
                raise JournalError('lineage_reference_type_mismatch')
            deck = seats[seat][1]['deck']
            names = [entry['name'] for entry in deck['cardEntries']] if deck.get('cardEntries') else deck['cards']
            if dict(Counter(names)) != lineage['deck_cards'] or observation.get('knownDeck', {}).get('cards') != lineage['deck_cards']:
                raise JournalError('native_deck_lineage_mismatch')
            metadata = {**choice.get('metadata', {}), 'recordingProvenance': {
                'journalRoot': report['root_sha256'], 'nativeInputHash': input_hash,
                'nativeResultHash': result_hash, 'engineRevision': next(iter(revisions)),
                'gymRevision': payload['gym_revision'], 'lineage': {k: v.to_dict() for k, v in refs.items()}}}
            if (not isinstance(payload['gym_revision'], str) or len(payload['gym_revision']) != 40 or
                    any(c not in '0123456789abcdef' for c in payload['gym_revision'])):
                raise JournalError('exact_gym_revision_unavailable')
            # Preserve timing only when both callback receipts share the same local clock.
            timing = {}
            if start['clock_id'] == callback['clock_id'] and callback['elapsed_ns'] >= start['elapsed_ns']:
                timing['pilot_elapsed_ms'] = (callback['elapsed_ns'] - start['elapsed_ns']) / 1_000_000
            trace = PilotExecutionTrace(lineage['pilot_name'], lineage['pilot_version'], 'action',
                result['semanticId'], index, observation, {'actionId': index, 'params': {}}, after, metadata, **timing)
            record_context = PilotRecordContext(report['run_id'], cid, seats[seat][0],
                refs['deck'].artifact_id, refs['deck'].revision, binding=refs['binding'])
            record = decision_record_from_execution_trace(trace, record_context)
            # No receipt measures submission duration; do not turn a constructor default into evidence.
            record.metadata['timing']['submission_elapsed_ms'] = None
            if not timing:
                record.metadata['timing']['pilot_elapsed_ms'] = None
            records.append(record)
        except (ValueError, KeyError, TypeError, IndexError) as error:
            diagnostics[str(error) if isinstance(error, JournalError) else 'invalid_choice_evidence'] += 1
    return records, dict(diagnostics)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--output', type=Path, required=True, help='New file inside an existing private directory')
    args = parser.parse_args()
    records, diagnostics = accepted_choice_records(args.directory)
    _private_dir(args.output.parent)
    if args.output.parent.resolve() == args.directory.resolve():
        raise JournalError('derived JSONL must not enter the source journal segment directory')
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        for record in records:
            stream.write(_json(record.to_dict()) + b'\n')
        stream.flush()
        os.fsync(stream.fileno())
    _sync_dir(args.output.parent)
    print(json.dumps({'accepted_records': len(records), 'diagnostics': diagnostics,
                      'run_readiness_certified': False, 'exact_replay_verified': False}, sort_keys=True))


if __name__ == '__main__':
    main()
