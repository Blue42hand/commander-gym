"""Native v2 callback conversion before journal sealing; never infer acceptance.

Native states/actions establish application identity only. Model inputs and targets
come exclusively from authenticated seat observations and native callback receipts.
"""
from collections import Counter
from itertools import zip_longest
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from .captured_choices import CALLBACK_SCHEMA_HASH, verified_observation
from .evidence import build_raw_evidence_envelope
from .game_journal import JournalError, _json
from .identity import IdentityRef
from .pilot_execution import PilotExecutionTrace
from .pilot_records import (PilotRecordContext, decision_record_from_execution_trace,
                           structured_decision_record_from_execution_trace)
from .record_codec import encode_record, decode_record, check_cancelled
from .records import PilotProvenance
from .run_records import RunRecord, RunParticipant, RunTermination, EngineProvenance


# Conservative decoded budget, independent of compressed journal custody limits.
MAX_CANONICAL_BYTES = 16 * 1024 * 1024
MAX_CANONICAL_CALLBACKS = 4096


def build_capture_envelope(directory: Path, report, *, cancel=None):
    """Verify a terminal native prefix and every captured callback before attachment.

    The journal may still be open, enabling atomic raw-envelope attachment followed
    by its existing terminal. Temporary joins are private and disk-backed. A failed
    conversion leaves the diagnostic journal intact and cannot certify a subset.
    """
    from .native_game_capture import NativeCursor, iter_native_source
    cursor = NativeCursor()
    imported = (row['payload']['source_body'] for row in report['rows'] if row.get('source') == 'native')
    for imported_body, source in zip_longest(imported, iter_native_source(directory, cursor, cancel=cancel)):
        check_cancelled(cancel)
        if imported_body is None or source is None or imported_body != source['source_body']:
            raise JournalError('canonical native source prefix mismatch')
    if not cursor.terminal or cursor.offset != (directory / 'native-000000.ndjson').stat().st_size:
        raise JournalError('canonical native source is not terminal')
    if len(cursor.revisions) != 1 or list(directory.glob('native-gap-*.json')):
        raise JournalError('canonical native source has a gap or changed revision')
    with tempfile.TemporaryDirectory(prefix='private-canonical-joins-') as temporary:
        path = Path(temporary) / 'joins.sqlite'
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        database = sqlite3.connect(path)
        try:
            database.execute('PRAGMA cache_size=-2048')
            database.execute('PRAGMA temp_store=FILE')
            database.execute('CREATE TABLE joins(category TEXT, cid TEXT, ordinal INTEGER, body BLOB)')
            database.execute('CREATE INDEX joins_key ON joins(category,cid)')
            return _convert(report, database, cancel=cancel)
        finally:
            database.close()


def _convert(report, database, *, cancel):
    join_bytes = 0
    def add(category, cid, row, ordinal):
        if not isinstance(cid, str) or not cid:
            raise JournalError('canonical correlation identity missing')
        nonlocal join_bytes
        encoded = _json(row) + b'\n'
        join_bytes += len(encoded)
        if join_bytes > MAX_CANONICAL_BYTES:
            raise JournalError('canonical aggregate decoded join limit exceeded')
        database.execute('INSERT INTO joins VALUES(?,?,?,?)',
                         (category, cid, ordinal, encode_record(encoded)))
    def one(category, cid):
        rows = database.execute('SELECT body FROM joins WHERE category=? AND cid=? LIMIT 2', (category, cid)).fetchall()
        if len(rows) != 1:
            raise JournalError('canonical callback join is missing or duplicate')
        return json.loads(decode_record(rows[0][0], cancel=cancel))
    initialization = terminal = previous = None
    state_digest = None
    awaiting_resume = False
    resume_checkpoint_seen = False
    revision = None
    first = report['rows'][0]
    pins = first['payload']['pins']
    for row in report['rows']:
        check_cancelled(cancel)
        payload = row['payload']
        if row['kind'] in {'decision_started', 'seat_callback'}:
            add('starts' if row['kind'] == 'decision_started' else 'callbacks', payload['decision_id'], row, row['sequence'])
        if row.get('source') != 'native':
            continue
        exact = payload['source_body']
        if hashlib.sha256(exact.encode()).hexdigest() != payload['source_sha256']:
            raise JournalError('canonical native body digest mismatch')
        body = json.loads(exact)
        if body['gameId'] != report['run_id'] or body['engineRevision'] != pins['engine']:
            raise JournalError('canonical native identity mismatch')
        revision = body['engineRevision']
        native = body['payload']
        if body['kind'] == 'initialization':
            if initialization is not None or native.get('callbackEvidenceVersion') != 2:
                raise JournalError('canonical native v2 capability unavailable')
            initialization = body
            state_digest = native.get('initialStateDigest')
            if not isinstance(state_digest, str) or len(state_digest) != 64:
                raise JournalError('canonical initial state identity missing')
        elif body['kind'] == 'resume':
            if awaiting_resume:
                raise JournalError('canonical repeated unauthenticated resume')
            awaiting_resume = True
            resume_checkpoint_seen = False
        elif body['kind'] == 'callback_resume_state':
            if not awaiting_resume or native.get('version') != 2 or native.get('restoredStateDigest') != state_digest:
                raise JournalError('canonical recovered state differs from committed source')
            awaiting_resume = False
        elif awaiting_resume:
            # The native state setter emits one administrative checkpoint while restoring
            # into an empty session, before the authoritative restored-state receipt.
            if (body['kind'] != 'state_checkpoint' or resume_checkpoint_seen or
                    native.get('beforeStateDigest') is not None or not isinstance(native.get('after'), dict) or not native['after']):
                raise JournalError('canonical resume state receipt missing')
            resume_checkpoint_seen = True
        if body['kind'] == 'native_transition':
            if native.get('beforeStateDigest') != state_digest or native.get('administrativeStall') or native['result'].get('error') is not None:
                raise JournalError('canonical native transition is discontinuous or unaccepted')
            state_digest = native['effectiveStateDigest']
        if body['kind'] in {'terminal', 'session_closed'}:
            if terminal is not None or native.get('administrativeStall'):
                raise JournalError('canonical terminal is invalid')
            terminal = body
        if body['kind'] in {'ai_callback_input', 'ai_callback_result', 'ai_callback_disposition'}:
            if body['visibility'] != 'seat' or not isinstance(body.get('seatId'), str):
                raise JournalError('canonical native callback must be seat-visible')
            category = {'ai_callback_input': 'inputs', 'ai_callback_result': 'results', 'ai_callback_disposition': 'dispositions'}[body['kind']]
            add(category, native['correlationId'], {'native': body, 'hash': payload['source_sha256'],
                'transition': previous if category == 'results' else None}, row['sequence'])
        previous = body
    if initialization is None or terminal is None or awaiting_resume:
        raise JournalError('canonical lifecycle is incomplete')
    database.commit()
    # Inputs/results without completions are gaps too; no accepted subset can clear them.
    callback_count = database.execute("SELECT COUNT(DISTINCT cid) FROM joins WHERE category='callbacks'").fetchone()[0]
    if callback_count > MAX_CANONICAL_CALLBACKS:
        raise JournalError("canonical aggregate callback limit exceeded")
    callbacks = database.execute("SELECT cid,MIN(ordinal) FROM joins WHERE category='callbacks' GROUP BY cid ORDER BY MIN(ordinal)").fetchall()
    for category in ('starts', 'inputs', 'results'):
        if database.execute('SELECT COUNT(*) FROM joins WHERE category=?', (category,)).fetchone()[0] != len(callbacks):
            raise JournalError('canonical callback coverage differs from native receipts')
    if database.execute("SELECT COUNT(*) FROM joins WHERE category='dispositions'").fetchone()[0]:
        raise JournalError('canonical callback has a failure disposition')
    players = initialization['payload']['setup']['players']
    seats = {player['playerId']: (index, player) for index, player in enumerate(players)}
    if len(seats) != len(players):
        raise JournalError('canonical native seat identities duplicate')
    records, participants, lineage_by_seat = [], {}, {}
    aggregate_bytes = 0
    for cid, _ in callbacks:
        check_cancelled(cancel)
        start, callback = one('starts', cid), one('callbacks', cid)
        source_input, source_result = one('inputs', cid), one('results', cid)
        input_body, result_body = source_input['native'], source_result['native']
        context, result = input_body['payload'], result_body['payload']
        seat, payload = callback['seat_id'], callback['payload']
        if (seat not in seats or start['seat_id'] != seat or input_body['seatId'] != seat or result_body['seatId'] != seat or
                context.get('version') != 2 or result.get('version') != 2 or result.get('status') != 'accepted' or
                context != payload.get('decision_evidence') or context != start['payload'].get('decision_evidence') or
                start['sequence'] >= callback['sequence'] or input_body['sequence'] >= result_body['sequence'] or
                result.get('inputStateDigest') != context['stateDigest'] or payload['gym_revision'] != pins['gym'] or
                start['payload']['gym_revision'] != pins['gym']):
            raise JournalError('canonical correlated native callback mismatch')
        native = verified_observation(context, seat)
        observation = payload['observation']
        extra = set(observation) - set(native) - {'schemaHash', 'stateDigest', 'knownDeck', 'nativePaymentError'}
        if (extra or start['payload']['observation'] != observation or
                any(observation.get(k) != v for k, v in native.items()) or
                observation.get('schemaHash') != context['schemaHash'] or observation.get('stateDigest') != context['stateDigest']):
            raise JournalError('canonical seat observation mismatch or private additions')
        callback_kind = native['callbackKind']
        expected_callback = 'chooseAction' if callback_kind == 'paymentCorrection' else callback_kind
        if payload['callback'] != expected_callback or start['payload']['callback'] != expected_callback:
            raise JournalError('canonical callback kind mismatch')
        transition = source_result['transition']
        if (transition is None or transition['kind'] != 'native_transition' or
                transition['payload']['action'] != result['action'] or transition['payload']['result'].get('error') is not None):
            raise JournalError('canonical actual application receipt missing')
        choice, accepted = payload['choice'], result['choice']
        channel = accepted.get('channel')
        if channel == 'action':
            index, params = accepted.get('actionId'), accepted.get('params')
            if type(index) is not int or not 0 <= index < len(native['legalActions']):
                raise JournalError('canonical native template missing')
            offered = native['legalActions'][index]
            if accepted.get('semanticId') != offered.get('semanticId'):
                raise JournalError('canonical native semantic choice mismatch')
            if callback_kind == 'decideMulligan':
                if type(choice.get('keep')) is not bool or index != (0 if choice['keep'] else 1) or params != {}:
                    raise JournalError('canonical native mulligan choice mismatch')
            elif choice.get('channel') != 'action' or choice.get('actionId') != index or choice.get('params') != params:
                raise JournalError('canonical submitted action parameters mismatch')
            submitted, routing = {'actionId': index, 'params': params}, index
        elif channel == 'decision':
            pending = native['pendingDecision']
            response = accepted.get('response')
            if not isinstance(pending, dict) or accepted.get('semanticId') != pending.get('semanticId') or not isinstance(response, dict):
                raise JournalError('canonical structured choice mismatch')
            if callback_kind == 'chooseBottomCards':
                expected = {'type': 'CardsSelectedResponse', 'selectedCards': choice.get('selectedCards')}
            else:
                if choice.get('channel') != 'decision' or not isinstance(choice.get('response'), dict):
                    raise JournalError('canonical structured callback choice missing')
                expected = {k: v for k, v in choice['response'].items() if k != 'decisionId'}
                if choice['response'].get('decisionId') != pending.get('decisionId'):
                    raise JournalError('canonical structured routing mismatch')
            if response != expected or 'decisionId' in response:
                raise JournalError('canonical structured accepted response differs from submitted')
            routing = pending['decisionId']
            submitted = {**response, 'decisionId': routing}
        else:
            raise JournalError('canonical native choice channel unsupported')
        after_context = result['resultObservation']
        after = verified_observation(after_context, seat)
        if after_context.get('version') != 2 or after['callbackKind'] != 'result':
            raise JournalError('canonical result seat observation unavailable')
        after.update(schemaHash=after_context['schemaHash'], stateDigest=after_context['stateDigest'])
        lineage = payload['lineage']
        if lineage != start['payload'].get('lineage') or (seat in lineage_by_seat and lineage_by_seat[seat] != lineage):
            raise JournalError('canonical immutable seat lineage changed')
        lineage_by_seat[seat] = lineage
        refs = {key: IdentityRef.from_dict(lineage[key]) for key in ('binding', 'deck', 'pilot')}
        if any(ref.artifact_type != key for key, ref in refs.items()):
            raise JournalError('canonical lineage reference type mismatch')
        deck = seats[seat][1]['deck']
        names = [entry['name'] for entry in deck['cardEntries']] if deck.get('cardEntries') else deck['cards']
        if dict(Counter(names)) != lineage['deck_cards']:
            raise JournalError('canonical native deck differs from immutable lineage')
        if callback_kind in {'chooseAction', 'paymentCorrection'} and observation.get('knownDeck', {}).get('cards') != lineage['deck_cards']:
            raise JournalError('canonical policy deck knowledge differs from native deck')
        metadata = {**choice.get('metadata', {}), 'recordingProvenance': {
            'journalGenesis': first['sha256'], 'nativeInputHash': source_input['hash'], 'nativeResultHash': source_result['hash'],
            'engineRevision': revision, 'gymRevision': pins['gym'], 'lineage': {k: v.to_dict() for k, v in refs.items()}}}
        # Mechanical routing has no provider metadata; classify actual callback delivery explicitly.
        metadata.setdefault('routing', {'path': 'native-callback', 'callback': callback_kind})
        timing = {}
        if start['clock_id'] == callback['clock_id'] and callback['elapsed_ns'] >= start['elapsed_ns']:
            timing['pilot_elapsed_ms'] = (callback['elapsed_ns'] - start['elapsed_ns']) / 1_000_000
        trace = PilotExecutionTrace(lineage['pilot_name'], lineage['pilot_version'], channel,
            accepted['semanticId'], routing, observation, submitted, after, metadata, **timing)
        record_context = PilotRecordContext(report['run_id'], cid, seats[seat][0], refs['deck'].artifact_id,
                                            refs['deck'].revision, binding=refs['binding'])
        record = (decision_record_from_execution_trace(trace, record_context, schema_version=2) if channel == 'action'
                  else structured_decision_record_from_execution_trace(trace, record_context))
        record.metadata['timing']['submission_elapsed_ms'] = None
        if not timing:
            record.metadata['timing']['pilot_elapsed_ms'] = None
        aggregate_bytes += len(_json(record.to_dict()))
        if aggregate_bytes > MAX_CANONICAL_BYTES:
            raise JournalError("canonical aggregate decoded byte limit exceeded")
        records.append(record)
        participants[record.seat] = RunParticipant(record.seat, record.pilot, record.deck_id, record.deck_version, binding=record.binding)
    # Human/non-Gym seats have native participation, without invented Binding identities.
    for index, player in enumerate(players):
        participants.setdefault(index, RunParticipant(index, PilotProvenance('native', 'native-seat')))
    run = RunRecord(report['run_id'], initialization['utc'], terminal['utc'],
        EngineProvenance('argentum', revision, schema=CALLBACK_SCHEMA_HASH, revision=revision),
        RunTermination('completed' if terminal['kind'] == 'terminal' else 'stopped',
                       reason=None if terminal['kind'] == 'terminal' else 'native session closed'),
        participants=[participants[index] for index in sorted(participants)], game_id=report['run_id'],
        seed=initialization['payload']['setup']['seed'], decision_ids=[record.decision_id for record in records],
        metadata={'evidence_scope': 'native-transitions-and-recorded-ai-callbacks-v2',
                  'nativeTerminalHash': hashlib.sha256(_json(terminal)).hexdigest(), 'exact_replay_verified': False})
    return build_raw_evidence_envelope(run, records, commander_gym_revision=pins['gym'], schema_version=2)
