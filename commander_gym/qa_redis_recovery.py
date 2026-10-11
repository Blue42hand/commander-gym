"""Read-only native restart proof. Controller owns lifecycle and the absolute deadline."""
from pathlib import Path
import json
import re
from .game_journal import JournalError, inspect_journal
from .native_game_capture import NativeCursor, iter_native_source
from .qa_callback_drain import observe_callback_drain


def _require(value):
    if not value:
        raise JournalError('qa_redis_native_restore_unverified')


def snapshot_request(boot_id, release_id, game_id):
    _require(type(boot_id) is str and boot_id and type(release_id) is str
             and re.fullmatch('[a-f0-9]{64}', release_id) and type(game_id) is str
             and re.fullmatch('[A-Za-z0-9-]{1,128}', game_id))
    return dict(protocol=1, op='qa-redis-snapshot', bootId=boot_id, releaseId=release_id, gameId=game_id)


def validate_snapshot(response, drain):
    # This is metadata from the updater-only native GET proof, not a caller health flag.
    _require(type(response) is dict and set(response) == {'protocol','ok','bootId','releaseId','engineSha','gymSha','snapshot'}
             and type(response['protocol']) is int and response['protocol'] == 1 and response['ok'] is True
             and response['bootId'] == drain['bootId'] and response['releaseId'] == drain['releaseId']
             and response['engineSha'] == drain['pins']['engine'] and response['gymSha'] == drain['pins']['gym'])
    proof=response['snapshot']
    _require(type(proof) is dict and set(proof) == {'gameId','snapshotSha256','snapshotBytes','stateDigest','manualHumanStart','playerIdentitySha256'}
             and proof['gameId'] == drain['runId'] and proof['manualHumanStart'] is True
             and type(proof['snapshotBytes']) is int and 0 < proof['snapshotBytes'] <= 8*1024**2)
    for key in ('snapshotSha256','stateDigest','playerIdentitySha256'):
        _require(type(proof[key]) is str and re.fullmatch('[a-f0-9]{64}',proof[key]))
    return dict(proof)


def verify_native_restore(directory, lifecycle_socket, previous, previous_snapshot, current_snapshot):
    """Reobserve paused native and immutable prefix; never start/stop/resume services.

    Snapshot responses must be obtained by the root controller's actual private
    qa-redis-snapshot RPC on each boot. This verifier does not confer execution or
    trust an injected status reader. Canonical completion is a later terminal gate.
    """
    current=observe_callback_drain(directory,lifecycle_socket)
    _require(current['bootId'] != previous['bootId'] and current['generation'] == 1
             and all(current[k] == previous[k] for k in ('releaseId','runId','pins','callbacks')))
    old=validate_snapshot(previous_snapshot,previous);new=validate_snapshot(current_snapshot,current)
    _require(old == new)
    report=inspect_journal(Path(directory));count=previous.get('journalRows')
    _require(type(count) is int and 0 < count <= len(report['rows'])
             and report['rows'][count-1]['sha256'] == previous.get('journalHead'))
    extra=list(report['rows'][count:])
    kinds=[row['kind'] for row in extra]
    _require(kinds in (['native_source']*3, ['resume','runtime_context']+['native_source']*3))
    if kinds[:2] == ['resume','runtime_context']:
        old_contexts=[row['payload'] for row in report['rows'][:count] if row['kind']=='runtime_context']
        _require(old_contexts and extra[1]['source']=='gym-runtime' and extra[1]['payload']==old_contexts[-1])
    _require(all(row.get('source')=='native' for row in extra[-3:]))
    result=_verify_prefix(Path(directory),previous,current,old)
    return {**result,'journalRows':current['journalRows'],'journalHead':current['journalHead']}


def _verify_prefix(directory, previous, current, proof):
    # Pure file fixture seam, separate from the public actual-status API.
    cursor=NativeCursor();prefix_head=None;prefix_bytes=0;extra=[]
    for row in iter_native_source(directory,cursor):
        body=json.loads(row['source_body'])
        _require(body['gameId']==previous['runId'] and body['engineRevision']==previous['pins']['engine'])
        if cursor.offset <= previous['nativeBytes']:
            prefix_head=row['source_sha256'];prefix_bytes=cursor.offset
        else:
            extra.append(body)
    _require(prefix_bytes==previous['nativeBytes'] and prefix_head==previous['nativeHead']
             and cursor.offset==current['nativeBytes'] and cursor.previous==current['nativeHead'])
    _require([body['kind'] for body in extra] == ['resume','state_checkpoint','callback_resume_state']
             and extra[1]['payload'].get('beforeStateDigest') is None
             and extra[2]['payload'].get('version')==2
             and extra[2]['payload'].get('restoredStateDigest')==proof['stateDigest'])
    return {'kind':'qa-redis-native-process-restore-observation','canonicalCompletion':False,
            'oldBootId':previous['bootId'],'bootId':current['bootId'],'releaseId':current['releaseId'],
            'runId':current['runId'],'pins':current['pins'],'snapshot':proof,
            'prefixBytes':prefix_bytes,'prefixHead':prefix_head,'nativeBytes':cursor.offset,'nativeHead':cursor.previous}
