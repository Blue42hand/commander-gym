"""Convert a bounded accepted native AI-choice subset offline; never certify a run.

Only native seat evidence enters records. Admin transitions establish application
identity; their states/events are never copied to pilot observations or targets.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from itertools import zip_longest, islice
import sqlite3
import tempfile
from .record_codec import encode_record, decode_record
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
CALLBACK_SCHEMA_HASH = hashlib.sha256(b'argentum-ai-callback-seat-evidence-v2').hexdigest()


def verified_observation(context: Mapping[str, Any], seat: str) -> dict[str, Any]:
    version = context.get('version') if isinstance(context, Mapping) else None
    if (not isinstance(context, Mapping) or type(version) is not int or
            version not in {1, 2} or context.get('schemaHash') != (SCHEMA_HASH if version == 1 else CALLBACK_SCHEMA_HASH) or
            not isinstance(context.get('correlationId'), str) or not context['correlationId'] or
            not isinstance(context.get('observationBody'), str)):
        raise JournalError('unsupported native decision evidence')
    if hashlib.sha256(context['observationBody'].encode('utf-8')).hexdigest() != context.get('stateDigest'):
        raise JournalError('native decision observation hash mismatch')
    observation = json.loads(context['observationBody'])
    if not isinstance(observation, dict):
        raise JournalError('native decision observation must be an object')
    keys = {'type', 'state', 'legalActions', 'pendingDecision', 'recentGameLog', 'perspectivePlayerId', 'agentToAct', 'terminated'}
    if version == 2:
        keys.add('callbackKind')
        if observation.get('callbackKind') == 'paymentCorrection':
            keys.add('nativePaymentError')
            if not isinstance(observation.get('nativePaymentError'), str) or not observation['nativePaymentError'].strip():
                raise JournalError('native payment feedback is unavailable')
    if (not isinstance(observation, dict) or set(observation) != keys or
            observation['type'] != 'GameServerSeat' or observation['perspectivePlayerId'] != seat or
            observation['agentToAct'] != seat or not isinstance(observation['state'], dict) or
            not isinstance(observation['legalActions'], list) or type(observation['terminated']) is not bool):
        raise JournalError('native decision seat or observation shape mismatch')
    if version == 1:
        if observation['state'].get('viewingPlayerId') != seat or observation['pendingDecision'] is not None:
            raise JournalError('native v1 decision shape mismatch')
    else:
        kind = observation['callbackKind']
        if kind not in {'chooseAction', 'paymentCorrection', 'decideMulligan', 'chooseBottomCards', 'result'}:
            raise JournalError('unsupported native callback kind')
        if kind in {'chooseAction', 'paymentCorrection', 'result'} and observation['state'].get('viewingPlayerId') != seat:
            raise JournalError('native callback state perspective mismatch')
        if kind == 'decideMulligan' and (set(observation['state']) != {'mulligan'} or observation['pendingDecision'] is not None):
            raise JournalError('native mulligan callback shape mismatch')
        if kind == 'chooseBottomCards' and (observation['state'] != {} or not isinstance(observation['pendingDecision'], dict)
                or observation['pendingDecision'].get('kind') != 'BottomCards' or observation['legalActions']):
            raise JournalError('native bottom callback shape mismatch')
    return observation


def accepted_choice_records(directory: Path) -> tuple[Sequence[Any], dict[str, int]]:
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
    imported = (row['payload']['source_body'] for row in report['rows'] if row.get('source') == 'native')
    if (not cursor.terminal or cursor.offset != (directory / 'native-000000.ndjson').stat().st_size or
            any(a != (b['source_body'] if isinstance(b, dict) else None)
                for a, b in zip_longest(imported, source))):
        raise JournalError('native source differs from sealed journal or is incomplete')
    diagnostics = {}
    return ChoiceRecords(directory, report, diagnostics), diagnostics


class ChoiceRecords(Sequence):
    """Indexed-reader compatibility; CLI conversion streams one choice at a time."""
    def __init__(self, directory, report, diagnostics):
        self.directory, self.report, self.diagnostics = directory, report, diagnostics
    def __iter__(self):
        self.diagnostics.clear()
        with tempfile.TemporaryDirectory(prefix='private-choice-index-') as temporary:
            path = Path(temporary) / 'joins.sqlite'
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600); os.close(descriptor)
            database = sqlite3.connect(path)
            try:
                database.execute('PRAGMA cache_size=-2048')
                database.execute('PRAGMA temp_store=FILE')
                database.execute('CREATE TABLE joins(category TEXT, cid TEXT, ordinal INTEGER, body BLOB)')
                database.execute('CREATE INDEX joins_key ON joins(category,cid)')
                yield from _joined_choices(self.directory, self.report, self.diagnostics, database)
            finally: database.close()
    def __len__(self): return sum(1 for _ in self)
    def __getitem__(self, index):
        if index < 0: index += len(self)
        if index < 0: raise IndexError(index)
        try: return next(islice(iter(self), index, index + 1))
        except StopIteration: raise IndexError(index) from None
    def __eq__(self, other):
        sentinel = object()
        return isinstance(other, Sequence) and all(a == b for a, b in zip_longest(self, other, fillvalue=sentinel))


def _joined_choices(directory, report, diagnostics, database):
    def count(category, cid):
        return database.execute('SELECT COUNT(*) FROM joins WHERE category=? AND cid=?', (category, cid)).fetchone()[0]
    def load(category, cid):
        n = count(category, cid)
        if n != 1: return [None] * min(n, 2)
        blob = database.execute('SELECT body FROM joins WHERE category=? AND cid=?', (category, cid)).fetchone()[0]
        return [json.loads(decode_record(blob))]
    def add(category, cid, value, ordinal):
        if not isinstance(cid, str): raise JournalError('invalid_correlation_identity')
        database.execute('INSERT INTO joins VALUES(?,?,?,?)', (category, cid, ordinal, encode_record(_json(value) + b'\n')))
    players, previous = None, None
    revisions = set()
    unsupported_source = False
    for row in report['rows']:
        payload = row['payload']
        if row['kind'] in {'decision_started', 'seat_callback'}:
            add('starts' if row['kind'] == 'decision_started' else 'callbacks', payload['decision_id'], row, row['sequence'])
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
                add('inputs', cid, (body, payload['source_sha256']), row['sequence'])
            elif body['kind'] == 'ai_decision_result':
                add('results', cid, (body, payload['source_sha256'], previous), row['sequence'])
            else:
                add('dispositions', cid, None, row['sequence'])
        previous = {'kind': body['kind']}
        if body['kind'] == 'native_transition':
            previous['payload'] = {'action': native['action'], 'administrativeStall': native.get('administrativeStall'),
                                   'result': {'error': native['result'].get('error')}}
    database.commit()
    if unsupported_source or len(revisions) != 1 or players is None or list(directory.glob('native-gap-*.json')):
        diagnostics['unsupported_or_incomplete_native_source'] = database.execute(
            "SELECT COUNT(DISTINCT cid) FROM joins WHERE category='callbacks'").fetchone()[0]
        return
    seats = {player['playerId']: (index, player) for index, player in enumerate(players)}
    for (cid,) in database.execute("SELECT cid FROM joins WHERE category='callbacks' GROUP BY cid ORDER BY MIN(ordinal), cid"):
        completed = load('callbacks', cid)
        starts = {cid: load('starts', cid)}
        inputs = {cid: load('inputs', cid)}
        results = {cid: load('results', cid)}
        dispositions = {cid} if count('dispositions', cid) else set()
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
            yield record
        except (ValueError, KeyError, TypeError, IndexError) as error:
            key = str(error) if isinstance(error, JournalError) else 'invalid_choice_evidence'
            diagnostics[key] = diagnostics.get(key, 0) + 1



def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--output', type=Path, required=True, help='New file inside an existing private directory')
    args = parser.parse_args()
    records, diagnostics = accepted_choice_records(args.directory)
    _private_dir(args.output.parent)
    if args.output.parent.resolve() == args.directory.resolve():
        raise JournalError('derived JSONL must not enter the source journal segment directory')
    # Publish only a fully converted, fsynced export; cleanup owns this scratch file only.
    fd, temporary = tempfile.mkstemp(prefix='.accepted-choices-', dir=args.output.parent)
    temporary = Path(temporary)
    accepted_count = 0
    try:
        with os.fdopen(fd, 'wb') as stream:
            for record in records:
                accepted_count += 1
                stream.write(_json(record.to_dict()) + b'\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, args.output, follow_symlinks=False)
        _sync_dir(args.output.parent)
    finally:
        temporary.unlink()
    print(json.dumps({'accepted_records': accepted_count, 'diagnostics': diagnostics,
                      'run_readiness_certified': False, 'exact_replay_verified': False}, sort_keys=True))


if __name__ == '__main__':
    main()
