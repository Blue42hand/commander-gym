"""Read-only live collector: status requests and existing batch metadata only.

No credential, recorder, writer, admission, launch, or game-control operations.
The web process receives only an allowlisted snapshot, never these sockets.
"""
from __future__ import annotations
import argparse
import json
import math
import os
from pathlib import Path
import re
import signal
import socket
import struct
import subprocess
import threading
import time

from .batch_budget import atomic_json

FRESH_SECONDS = 20
ID = re.compile(r'[A-Za-z0-9_-]{1,128}')
STATES = {'active','inactive','failed','activating','deactivating','reloading','unknown'}
NATIVE_FIELDS = ('engineSha','gymSha','releaseId','bootId','acceptingNewGames','recoveryComplete',
                 'recordingHealthy','pendingRecordWrites','pendingActivities','inFlightAdmissions')


def integer(value):
    return type(value) is int and 0 <= value <= 1000000


def status(path, uid):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
        client.settimeout(2);client.connect(str(path))
        peer=struct.unpack('3i',client.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))[1]
        if peer!=uid:raise ValueError('Unexpected status peer')
        client.sendall(b'{"protocol":1,"op":"status"}\n')
        with client.makefile('rb') as reader:raw=reader.readline(4097)
    if len(raw)>4096:raise ValueError('Status exceeds limit')
    value=json.loads(raw)
    if value.get('protocol')!=1 or value.get('ok') is not True or not integer(value.get('activeGames')) or not integer(value.get('inFlightAdmissions')):
        raise ValueError('Invalid native status')
    observed=value.get('observedUnix')
    if type(observed) not in (int,float) or not math.isfinite(observed) or not -5<=time.time()-observed<=FRESH_SECONDS:
        raise ValueError('Stale native status')
    if value['inFlightAdmissions']>value['activeGames']:raise ValueError('Inconsistent admission counts')
    return {**{k:value.get(k) for k in NATIVE_FIELDS},'observed_unix':observed,
            'running':value['activeGames']-value['inFlightAdmissions'],'starting':value['inFlightAdmissions']}


def service_states(units):
    if not all(re.fullmatch(r'[a-zA-Z0-9_.@-]+\.service',u) for u in units):raise ValueError('Invalid fixed service')
    result=subprocess.run(['/usr/bin/systemctl','show',*units,'--property=Id','--property=ActiveState'],
                          capture_output=True,text=True,timeout=4)
    values={}
    for block in result.stdout.split('\n\n'):
        fields=dict(line.split('=',1) for line in block.splitlines() if '=' in line)
        unit=fields.get('Id');state=fields.get('ActiveState')
        if unit in units:values[unit]=state if state in STATES else 'unknown'
    return {u:values.get(u,'unknown') for u in units}


def batch_metadata(config):
    """Bounded pages of the existing owner read API; never opens the control DB.

    Native status supplies authoritative running counts. A changing or oversized
    metadata inventory is explicitly partial, never a false empty queue.
    """
    from .batch_rpc import call
    batches={};total=None;consistent=True;health={};started=time.monotonic()
    for page in range(8):
        value=call({'batch_socket':config['control_socket']},config['actor'],'list',data={'offset':page*30})
        health=value['health'];observed_total=value['total']
        if total is None:total=observed_total
        elif total!=observed_total:consistent=False
        for batch in value['batches']:batches[batch['id']]=batch
        if len(batches)>=observed_total:break
        if time.monotonic()-started>=3:break
    complete=consistent and len(batches)==total
    active=[];issues=[];queued=0;recent={'completed':0,'failed':0};now=time.time()
    for batch in batches.values():
        plan=batch['plan'];queued+=batch['state']=='Queued' and batch['desired']=='Running'
        for a in batch.get('attempts',[]):
            is_active=a['state'] in ('Starting','Running','Stopping')
            ended=a.get('ended_unix');in_window=type(ended) in (int,float) and ended>=now-86400
            if in_window:
                recent['completed']+=a['state']=='Completed'
                recent['failed']+=a['state'] in ('Failed','Interrupted')
            if not is_active and not (in_window and a['state'] in ('Failed','Interrupted')):continue
            if not ID.fullmatch(a['id']) or not ID.fullmatch(a['batch_id']):raise ValueError('Invalid stored identity')
            game=a.get('game_id')
            if game is not None and not ID.fullmatch(game):raise ValueError('Invalid native identity')
            observation=a.get('observation') or {};lives=observation.get('live_games') or []
            current=next((g for g in lives[:8] if g.get('gameSessionId')==game),{})
            progress=a.get('provider_progress') or {};budget=a.get('budget') or {}
            item={'id':a['id'],'batch_id':a['batch_id'],'name':str(plan.get('name',''))[:100],
                'execution_mode':plan.get('execution_mode','unknown'),'state':a['state'],
                'ordinal':a.get('ordinal'),'planned':plan.get('game_count'),'game_id':game,
                'started_unix':a.get('started_unix') or a.get('created_unix'),'ended_unix':ended,
                'message':str(a.get('message',''))[:300],'native_observed_unix':observation.get('observed_unix'),
                'turn':current.get('turnNumber'),'decisions':progress.get('decisions'),
                'provider_state':str(progress.get('state','Not observed'))[:40],
                'requests':budget.get('requests'),'estimated_usd':budget.get('estimated_usd'),
                'seats':[{'seat':i+1,'deck':str(seat.get('deck',{}).get('label','Unknown deck'))[:150]} for i,seat in enumerate(plan.get('seats',[])[:6])]}
            (active if is_active else issues).append(item)
    issues.sort(key=lambda a:a['ended_unix'],reverse=True)
    result={'available':True,'metadata_complete':complete,'queued':queued if complete else None,
            'active_count':len(active) if complete else None,'active':active[:8],
            'details_complete':complete and len(active)<=8,'recent_totals':recent,
            'issues':issues[:8],'observed_unix':now}
    broker={'available':health.get('supervisor')=='Available' and type(health.get('observed_unix')) in (int,float)
            and 0<=now-health['observed_unix']<=FRESH_SECONDS,'observed_unix':health.get('observed_unix')}
    return result,broker


def runtime(config, services, batch=None, broker=None):
    source={'id':config['id'],'name':config['name'],'scope':config['scope'],'state':'Unavailable',
            'available':False,'complete':False,'running':None,'recorder_state':'Unavailable','observed_unix':None,
            'service_state':services.get(config['unit'],'unknown')}
    try:
        value=status(config['socket'],config['uid'])
        source.update(value,available=True,complete=True,state='Running' if value['running'] else
                      'Starting' if value['starting'] else 'Busy' if value.get('pendingActivities') else
                      'Idle' if value.get('acceptingNewGames') and value.get('recoveryComplete') else 'Paused',
                      recorder_state='Healthy' if value.get('recordingHealthy') is True else 'Warning')
    except (OSError,ValueError,TypeError,KeyError):
        # Stopped batch units are expected between attempts. A missing socket alone
        # cannot prove idle: the queue and all owned worker states must agree.
        if (config['id']=='batches' and batch and batch.get('available') and batch['active_count']==0 and broker and broker['available']
            and all(services.get(u) in ('inactive','failed') for u in config['worker_units'])):
            source.update(available=True,complete=True,running=0,starting=0,state='Queued' if batch['queued'] else 'Idle',
                          recorder_state='Standby',observed_unix=time.time())
    return source


def collect(config):
    observed=time.time()
    units=list(dict.fromkeys(u for r in config['runtimes'] for u in [r['unit'],*r.get('worker_units',[])]))+[config['supervisor_unit'],*config.get('services',[])]
    try:services=service_states(units)
    except (OSError,ValueError,subprocess.SubprocessError):services={u:'unknown' for u in units}
    try:batch,broker=batch_metadata(config)
    except Exception:
        batch={'available':False,'active':[],'issues':[],'queued':None}
        broker={'available':False,'observed_unix':None}
    runtimes=[runtime(r,services,batch,broker) for r in config['runtimes']]
    complete=all(r['complete'] for r in runtimes)
    known=sum(r['running'] or 0 for r in runtimes)
    states=[r['state'] for r in runtimes]
    if known:runner='Running'
    elif 'Starting' in states or batch.get('active_count'):runner='Starting' if any(a['state']=='Starting' for a in batch['active']) else 'Unknown'
    elif not complete:runner='Unavailable'
    elif not batch.get('available') or batch.get('metadata_complete') is False:runner='Unknown'
    elif batch.get('queued'):runner='Queued'
    elif any(s not in ('Idle','Queued') for s in states):runner='Busy' if 'Busy' in states else 'Unavailable'
    elif broker['available']:runner='Idle'
    else:runner='Unavailable'
    return {'schema_version':1,'observed_unix':observed,'freshness_seconds':FRESH_SECONDS,
            'scope':config['scope'],'coverage_complete':complete,'running':known if complete else None,'running_known':known,
            'runner_state':runner,'runtimes':runtimes,'batch':batch,'supervisor':broker,'services':services,
            'limitations':['Game identities and turns for human games are not exposed by the configured native status socket.',
                           'Scope covers the configured human-play and managed-batch runtimes; legacy training/gateway sessions are outside it.']+
                          (['Batch metadata inventory is partial; queue totals are unknown.'] if batch.get('metadata_complete') is False else [])}


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',type=Path,required=True);args=parser.parse_args()
    config=json.loads(args.config.read_text());output=Path(config['output']);output.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    stop=threading.Event()
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:stop.set())
    while not stop.is_set():
        started=time.monotonic()
        atomic_json(output,collect(config))
        stop.wait(max(.2,5-(time.monotonic()-started)))

if __name__=='__main__':main()
