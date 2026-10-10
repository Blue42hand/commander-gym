"""Read-only QA drain evidence, separate from canonical completion and rollout.

Native QA admission must first be paused on the private operator socket. This
observer reads that socket itself and verifies the exact durable callback joins;
it never accepts a caller's drained boolean, pauses a game, or edits receipts.
Run as the recorder/native UID, with sealed paths chosen by the root controller.
"""
from itertools import zip_longest
import json
import os
from pathlib import Path
import socket

from .game_journal import JournalError, inspect_journal, _json
from .native_game_capture import NativeCursor, iter_native_source
from .recorder_bridge import peer_uid

MAX_BYTES = 16 * 1024 * 1024
MAX_CALLBACKS = 128


def _require(value):
    if not value: raise JournalError('qa_callback_drain_unverified')


def private_status(socket_path):
    # Reuse native-owner private path/framing validation; also verify peer UID.
    path = Path(socket_path)
    from .game_journal import _private_dir
    import stat
    _private_dir(path.parent)
    info = path.lstat()
    _require(stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid() and info.st_mode & 0o077 == 0)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(2);client.connect(str(path))
        _require(peer_uid(client) == os.getuid())
        client.sendall(b'{"protocol":1,"op":"status"}\n')
        raw = bytearray()
        while b'\n' not in raw:
            part=client.recv(4096-len(raw));_require(part and len(raw)+len(part)<4096);raw.extend(part)
        _require(raw.count(b'\n') == 1 and raw[-1:] == b'\n')
        result=json.loads(raw)
        _require(result.get('ok') is True)
        return result


def observe_callback_drain(directory, socket_path):
    return _observe_callback_drain(Path(directory), lambda: private_status(socket_path))


def _observe_callback_drain(directory, read_status):
    # The injectable reader is for unit fixtures only; production API always uses private_status.
    before=read_status()
    def paused(status):
        return (status.get('qaCallbackGateEnabled') is True and status.get('qaCallbackPaused') is True
                and status.get('qaCallbackAdmissionDrained') is True
                and type(status.get('qaCallbackActiveHandlers')) is int and status['qaCallbackActiveHandlers']==0
                and type(status.get('qaCallbackAdmissionFailures')) is int and status['qaCallbackAdmissionFailures']==0
                and type(status.get('qaCallbackGeneration')) is int and status['qaCallbackGeneration']>0)
    _require(paused(before))
    report=inspect_journal(directory)
    _require(report['integrity_ok'] and (not report['closed'] or report['recording_complete']) and not list(directory.glob('native-gap-*.json')))
    cursor=NativeCursor();starts={};callbacks={};inputs={};results={};previous=None;total=0;pins=None;state_digest=None;awaiting_resume=False;checkpoint=False
    imported=(r for r in report['rows'] if r.get('source')=='native')
    for row in report['rows']:
        total+=len(_json(row));_require(total<=MAX_BYTES)
        if pins is None:
            pins=row['payload'].get('pins');_require(row['kind']=='manifest' and not row['payload'].get('unavailable_pins'))
        _require(row['kind']!='coverage_gap')
        if row['kind'] in {'decision_started','seat_callback'}:
            table=starts if row['kind']=='decision_started' else callbacks
            cid=row['payload'].get('decision_id');_require(isinstance(cid,str) and cid not in table)
            table[cid]=row;_require(len(table)<=MAX_CALLBACKS)
    _require(isinstance(pins,dict))
    for row,source in zip_longest(imported,iter_native_source(directory,cursor)):
        _require(row is not None and source is not None and row['payload']['source_body']==source['source_body'])
        body=json.loads(source['source_body']);native=body['payload'];kind=body['kind']
        _require(body['gameId']==report['run_id'] and body['engineRevision']==pins['engine'])
        if kind=='initialization':
            _require(state_digest is None and native.get('callbackEvidenceVersion')==2)
            state_digest=native.get('initialStateDigest');_require(isinstance(state_digest,str) and len(state_digest)==64)
        elif kind=='resume':
            _require(not awaiting_resume);awaiting_resume=True;checkpoint=False
        elif kind=='callback_resume_state':
            _require(awaiting_resume and native.get('version')==2 and native.get('restoredStateDigest')==state_digest)
            awaiting_resume=False
        elif awaiting_resume:
            _require(kind=='state_checkpoint' and not checkpoint and native.get('beforeStateDigest') is None
                     and isinstance(native.get('after'),dict) and native['after'])
            checkpoint=True
        if kind=='native_transition':
            _require(native.get('beforeStateDigest')==state_digest and not native.get('administrativeStall')
                     and native['result'].get('error') is None)
            state_digest=native['effectiveStateDigest']
        if kind=='ai_callback_disposition':_require(False)
        if kind in {'ai_callback_input','ai_callback_result'}:
            table=inputs if kind=='ai_callback_input' else results;cid=native.get('correlationId')
            _require(isinstance(cid,str) and cid not in table);table[cid]=(body,previous)
        previous=body
    _require(state_digest is not None and not awaiting_resume and cursor.offset<=MAX_BYTES
             and cursor.offset==(directory/'native-000000.ndjson').stat().st_size
             and len(cursor.revisions)==1 and set(starts)==set(callbacks)==set(inputs)==set(results)
             and 0<len(callbacks)<=MAX_CALLBACKS)
    for cid,callback in callbacks.items():
        start=starts[cid];inp,_=inputs[cid];result,transition=results[cid]
        context=inp['payload'];accepted=result['payload'];seat=callback['seat_id']
        _require(context.get('version')==accepted.get('version')==2 and accepted.get('status')=='accepted'
                 and inp.get('seatId')==result.get('seatId')==start['seat_id']==seat
                 and callback['payload'].get('decision_evidence')==start['payload'].get('decision_evidence')==context
                 and callback['payload'].get('gym_revision')==start['payload'].get('gym_revision')==pins['gym']
                 and start['sequence']<callback['sequence'] and inp['sequence']<result['sequence']
                 and accepted.get('inputStateDigest')==context.get('stateDigest')
                 and transition is not None and transition['kind']=='native_transition'
                 and transition['payload']['action']==accepted.get('action')
                 and transition['payload']['result'].get('error') is None)
        from .captured_choices import verified_observation
        masked = verified_observation(context, seat)
        payload = callback['payload']; observed = payload['observation']
        _require(start['payload']['observation'] == observed
                 and all(observed.get(k) == v for k,v in masked.items())
                 and not (set(observed)-set(masked)-{'schemaHash','stateDigest','knownDeck','nativePaymentError'})
                 and observed.get('schemaHash')==context['schemaHash']
                 and observed.get('stateDigest')==context['stateDigest']
                 and payload.get('lineage') == start['payload'].get('lineage'))
        submitted = payload['choice']; choice = accepted['choice']; kind = masked['callbackKind']
        expected_kind='chooseAction' if kind=='paymentCorrection' else kind
        _require(payload.get('callback')==start['payload'].get('callback')==expected_kind)
        result_context=accepted['resultObservation']
        result_view=verified_observation(result_context,seat)
        _require(result_context.get('version')==2 and result_view['callbackKind']=='result')
        if choice.get('channel') == 'action':
            index = choice.get('actionId')
            _require(type(index) is int and 0 <= index < len(masked['legalActions'])
                     and choice.get('semanticId') == masked['legalActions'][index].get('semanticId'))
            if kind == 'decideMulligan':
                _require(type(submitted.get('keep')) is bool and index == (0 if submitted['keep'] else 1)
                         and choice.get('params') == {})
            else:
                _require(submitted.get('channel') == 'action' and submitted.get('actionId') == index
                         and submitted.get('params') == choice.get('params'))
        elif choice.get('channel') == 'decision':
            pending = masked['pendingDecision']
            _require(isinstance(pending, dict) and choice.get('semanticId') == pending.get('semanticId'))
            expected = ({'type':'CardsSelectedResponse','selectedCards':submitted.get('selectedCards')}
                        if kind == 'chooseBottomCards' else
                        {k:v for k,v in submitted.get('response',{}).items() if k!='decisionId'})
            _require(choice.get('response') == expected)
        else:
            _require(False)
    after=read_status()
    _require(paused(after) and all(before.get(k)==after.get(k) for k in ('bootId','releaseId','qaCallbackGeneration')))
    return {'kind':'qa-callback-join-drain-observation','canonicalCompletion':False,
            'bootId':after['bootId'],'releaseId':after['releaseId'],'generation':after['qaCallbackGeneration'],
            'runId':report['run_id'],'pins':pins,'callbacks':len(callbacks),
            'nativeBytes':cursor.offset,'nativeHead':cursor.previous,'journalHead':report['root_sha256'],
            'journalRows':len(report['rows'])}


def verify_recorder_reopen(directory, socket_path, previous):
    """Require the old durable prefix and only ordinary journal resume/runtime rows after reopen."""
    current=observe_callback_drain(directory,socket_path)
    _require(all(current.get(k)==previous.get(k) for k in
                 ('bootId','releaseId','generation','runId','pins','callbacks','nativeBytes','nativeHead')))
    report=inspect_journal(Path(directory));count=previous.get('journalRows')
    _require(type(count) is int and 0<count<=len(report['rows'])
             and report['rows'][count-1]['sha256']==previous.get('journalHead'))
    extra=list(report['rows'][count:])
    _require(not extra or [row['kind'] for row in extra]==['resume','runtime_context'])
    if extra:
        old_contexts=[row['payload'] for row in report['rows'][:count] if row['kind']=='runtime_context']
        _require(old_contexts and extra[1]['source']=='gym-runtime'
                 and extra[1]['payload']==old_contexts[-1])
    return current
