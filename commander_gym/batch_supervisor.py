"""Owner-only durable serial batch supervisor, controlling only fixed isolated units."""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import socketserver
import struct
import subprocess
import threading
import time
import urllib.request
import uuid

from .batch_budget import atomic_json
from .batch_store import BatchStore, BatchConflict, digest

UNITS = ('commander-gym-batch-provider.service','commander-gym-batch-fixture.service',
         'commander-gym-batch-recorder.service','commander-gym-batch-native.service','commander-gym-batch-local.service')


def provider_unit(mode):
    return {'paid':UNITS[0], 'fixture':UNITS[1], 'local':UNITS[4]}[mode]


def provider_stop(progress):
    if progress.get('state') == 'Limit reached':
        messages = {
            'batch_token_fence': 'Batch total token limit reached before provider dispatch',
            'batch_request_limit': 'Batch provider request limit reached before dispatch',
            'batch_cost_limit': 'Batch estimated spending limit reached before provider dispatch',
            'batch_decision_limit': 'Per-game decision limit reached',
            'batch_time_limit': 'Per-game time limit reached',
        }
        message = messages.get(progress.get('reason'), 'Per-game decision or time limit reached')
        if progress.get('reason') == 'batch_token_fence' and all(
            type(progress.get(k)) is int and progress[k] >= 0 for k in ('used', 'reservation', 'limit')
        ):
            message += f" ({progress['used']:,} used or reserved + {progress['reservation']:,} next reservation > {progress['limit']:,} limit)"
        return 'Interrupted', message
    if progress.get('state') == 'Callback failed':
        return 'Failed', 'Provider callback failed; inspect recorded decision evidence'
    return None


def exchange(path, message):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(3); sock.connect(str(path))
        sock.sendall(json.dumps(message).encode()+b'\n')
        with sock.makefile('rb') as reader: raw=reader.readline(32769)
        if len(raw)>32768: raise ValueError('Oversize control response')
        data=json.loads(raw)
        if not data.get('ok'): raise ValueError('Native lifecycle rejected request')
        return data


def native_http(port, method, path, value=None):
    request=urllib.request.Request('http://127.0.0.1:'+str(port)+path,
        data=None if value is None else json.dumps(value).encode(),method=method,headers={'Content-Type':'application/json'})
    # No proxy, redirects, or caller-supplied host/path.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args): raise ValueError('Native redirect refused')
    with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect).open(request,timeout=5) as response:
        raw=response.read(131073)
    if len(raw)>131072: raise ValueError('Oversize native status')
    return json.loads(raw)


class Supervisor:
    def __init__(self, config):
        self.config=config; self.root=Path(config['state_directory']); self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
        self.store=BatchStore(self.root,config['catalog'],config['pins'])
        self.stop=threading.Event(); self.last_tick=None; self.error=None
        self.runtime=Path(config['runtime_directory'])
        self.runtime.mkdir(mode=0o755,parents=True,exist_ok=True)
        self.active=self.root/'active';self.active.mkdir(mode=0o700,exist_ok=True)
        self.worker=None

    def unit(self, action, name):
        if name not in UNITS or action not in ('start','stop','is-active','show'):raise ValueError('Invalid fixed unit operation')
        cmd=['/usr/bin/systemctl',action,name]
        if action=='show':cmd+=['--property=InvocationID','--property=ActiveState','--property=SubState']
        result=subprocess.run(cmd,capture_output=True,timeout=40)
        if action in ('start','stop') and result.returncode:raise RuntimeError('Isolated batch unit operation failed')
        return result.stdout.decode().strip()

    def inactive(self):
        return all(self.unit('is-active',unit) not in ('active','activating','deactivating','reloading') for unit in UNITS)

    def close_runtime(self):
        # Never addresses any human service, process group, or recording root.
        for unit in UNITS:self.unit('stop',unit)
        if not self.inactive():raise RuntimeError('Owned runtime termination not confirmed')
        import stat
        for directory in ('native','provider'):
            for path in (self.runtime/directory).glob('*.sock'):
                info=path.lstat()
                if not stat.S_ISSOCK(info.st_mode) or info.st_uid!=self.config['engine_uid']:
                    raise RuntimeError('Unexpected isolated IPC custody; reconciliation required')
                path.unlink()

    def recover(self):
        self.store.recover()
        self.close_runtime()
        for batch in self.store.list(limit=100)['batches']:
            for attempt in batch['attempts']:
                if attempt['state']=='Needs reconciliation':
                    attempt.update(state='Interrupted',ended_unix=time.time(),supervisor_termination='Supervisor restart; isolated units confirmed stopped',
                                   message='Reconciled interruption; native result remains unknown')
                    self.store.record_attempt(attempt)
            if batch['state']=='Needs reconciliation':
                self.store.update(batch['id'],state='Paused',desired='Paused',message='Runtime reconciled after restart. Explicit Resume required; no create repeated.')

    def handle(self, request):
        if not isinstance(request,dict) or request.get('actor') not in self.config['allowed_logins']:raise PermissionError('Owner identity required')
        op=request.get('operation');data=request.get('data',{})
        if op=='catalog':return {'catalog':self.config['catalog'],'pins':self.config['pins'],'concurrency':1,
                                  'provider':'gpt-6-luna','execution_modes':['paid','fixture']+(['local'] if self.config.get('local_inference') else []),'cost_note':'Conservative estimates and reservations; not a billing guarantee',
                                  'native_seed':'Assigned by native engine; retained in recording'}
        if op=='list':return {**self.store.list(offset=int(data.get('offset',0))), 'health':self.health()}
        if op=='get':return self.store.get(request['batch_id'])
        if op=='game_context':return self.store.game_context(data.get('game_id'))
        if op=='create':return self.store.create(data.get('plan'),request['actor'],request.get('key'))
        return self.store.command(request.get('batch_id'),op,data,request['actor'],request.get('key'))

    def health(self):
        return {'supervisor':'Available' if self.last_tick and time.time()-self.last_tick<10 and not self.error else 'Unavailable',
                'observed_unix':self.last_tick,'error':self.error,'scope':'Managed batch runtime only; manual/private games outside this runtime are not counted'}

    def prepare(self,batch,attempt):
        if not self.inactive():raise RuntimeError('Isolated units are not stopped')
        cfg=self.config; plan=batch['plan']; engine=cfg['engine_uid'];engine_group=cfg['engine_gid'];provider=cfg['provider_uid'];group=cfg['provider_gid']
        # Frozen version pins must still describe the installed immutable artifacts.
        if plan['pins']!=cfg['pins']:raise RuntimeError('Frozen batch pins differ from installed runtime; clone a new draft')
        if plan['execution_mode']=='local':
            if not cfg.get('local_inference') or plan['pins'].get('local_inference')!=cfg['local_inference']:
                raise RuntimeError('Local inference configuration is not pinned')
        for path,expected in cfg['verify_files'].items():
            with open(path,'rb') as stream:
                sha=hashlib.file_digest(stream,'sha256').hexdigest()
            if sha!=expected:raise RuntimeError('Pinned runtime artifact changed')
        for key in ('native','provider'):
            folder=self.runtime/key
            folder.mkdir(mode=0o700 if key=='native' else 0o750,exist_ok=True)
            folder.chmod(0o700 if key=='native' else 0o750)
            os.chown(folder,engine,group if key=='provider' else engine_group)
            if any(folder.iterdir()):raise RuntimeError('Stale isolated IPC remains; admission refused')
        captures=self.root/'captures';captures.mkdir(mode=0o700,exist_ok=True)
        capture=captures/attempt['id'];capture.mkdir(mode=0o700);os.chown(capture,engine,engine_group)
        progress=self.root/'progress';progress.mkdir(mode=0o700,exist_ok=True)
        progress=progress/attempt['id'];progress.mkdir(mode=0o700);os.chown(progress,provider,group)
        budgets=self.root/'budgets';budgets.mkdir(mode=0o700,exist_ok=True)
        budget=budgets/batch['id']
        if not budget.exists():
            # Only the first attempt may create ledgers. Losing one later fails closed.
            if attempt['ordinal']!=1:raise RuntimeError('Batch budget directory lost')
            budget.mkdir(mode=0o700)
            from .openai_run_budget import OpenAIRunBudget
            if plan['execution_mode']=='local':
                atomic_json(budget/'local.json',{'requests':0,'input_tokens':0,'output_tokens':0,'unsettled_requests':0})
            else:
                OpenAIRunBudget(budget/'openai.json',plan['budget_usd'],authorized_max_usd=plan['budget_usd'],
                                max_requests=plan['max_requests'],initialize_new_ledger=True).snapshot()
            atomic_json(budget/'tokens.json',{'limit':plan['max_tokens'],'reserved_or_used':0,'reported_tokens':0,'unsettled':0})
            for file in budget.iterdir():os.chown(file,provider,group)
            os.chown(budget,provider,group)
        for filename in (('local.json','tokens.json') if plan['execution_mode']=='local' else ('openai.json','tokens.json')):
            if not (budget/filename).is_file():raise RuntimeError('Batch budget ledger lost')
        token=secrets.token_urlsafe(32)
        for role,uid,gid in [('native',engine,engine_group),('provider',provider,group)]:
            folder=self.active/(role+'-config');folder.mkdir(mode=0o700,exist_ok=True);os.chown(folder,uid,gid)
            file=folder/('commander-gym.sidecar.token' if role=='native' else 'token');file.write_text(token);file.chmod(0o600);os.chown(file,uid,gid)
        for name,target in [('captures',capture),('progress',progress),('budget',budget)]:
            link=self.active/name
            if link.is_symlink():link.unlink()
            link.symlink_to(target)
        recording_pins={**cfg['recording_pins'],'gym':cfg['pins']['gym'],
            'config':{'purpose':'panel-batch','batchId':batch['id'],'attemptId':attempt['id'],'planFingerprint':batch['fingerprint'],
                      'executionMode':plan['execution_mode'],'paidProvidersEnabled':plan['execution_mode']=='paid','manualOnly':False},
            'models':{'provider':'qa-native-legal-fixture' if plan['execution_mode']=='fixture' else 'openai-responses',
                      'model':'none' if plan['execution_mode']=='fixture' else 'gpt-6-luna'}}
        if plan['execution_mode']=='local':
            recording_pins['models']=cfg['local_inference']
            recording_pins['bindings']={b:cfg['pins']['bindings'][b] for b in plan['bindings']}
        role_config={k:cfg[k] for k in ('native_port','provider_port','java','python_packages','roster','engine_uid','provider_uid','provider_gid','pins')}
        if plan['execution_mode']=='local':
            role_config.update(local_inference=cfg['local_inference'], roster=cfg['local_roster'], catalog_root='/local-catalog')
        role_config.update(plan=plan,recording_pins=recording_pins,deadline=time.time()+plan['wall_seconds']+120,
                           runtime_id=digest([batch['fingerprint'],attempt['id']]))
        atomic_json(self.active/'role.json',role_config);(self.active/'role.json').chmod(0o444)
        attempt.update(runtime_id=role_config['runtime_id'],deadline=role_config['deadline'],capture_reference=attempt['id'])
        self.store.record_attempt(attempt)
        return role_config

    def budget_snapshot(self,batch):
        root=self.root/'budgets'/batch['id']
        try:
            tokens=json.loads((root/'tokens.json').read_text())
            if batch['plan']['execution_mode']=='local':
                local=json.loads((root/'local.json').read_text())
                return {**local,'estimated_usd':0,'reported_tokens':tokens['reported_tokens'],
                        'reserved_or_used_tokens':tokens['reserved_or_used'],'unsettled_token_requests':tokens['unsettled'],
                        'observed_unix':time.time(),'provider':'lmstudio'}
            money=json.loads((root/'openai.json').read_text())
            return {'estimated_usd':money['estimatedUsd'],'requests':money['requests'],'unsettled_requests':money['unsettledRequests'],
                    'reported_tokens':tokens['reported_tokens'],'reserved_or_used_tokens':tokens['reserved_or_used'],'unsettled_token_requests':tokens['unsettled'],
                    'input_tokens':money.get('inputTokens'),'output_tokens':money.get('outputTokens'),'observed_unix':time.time()}
        except (OSError,ValueError,KeyError):return {'state':'Unavailable'}

    def run_attempt(self,batch,attempt):
        outcome='Failed';message='Runtime preflight failed';native_terminal=None
        try:
            role=self.prepare(batch,attempt)
            # Persist attribution before a provider can dispatch its first request.
            attempt['budget_start']=self.budget_snapshot(batch)
            self.store.record_attempt(attempt)
            self.unit('start',UNITS[3]); self.unit('start',UNITS[2])
            self.unit('start',provider_unit(batch['plan']['execution_mode']))
            attempt['unit_invocations']={u:self.unit('show',u) for u in UNITS};self.store.record_attempt(attempt)
            limit=time.monotonic()+120
            status=None
            while time.monotonic()<limit and not self.stop.is_set():
                if self.store.get(batch['id'])['desired']=='Cancelled':raise RuntimeError('Owner requested interruption during startup')
                try:
                    status=exchange(self.runtime/'native/lifecycle.sock',{'protocol':1,'op':'status'})
                    if status.get('releaseId')==attempt['runtime_id'] and status.get('engineSha')==self.config['pins']['engine'] and status.get('gymSha')==self.config['pins']['gym'] and status.get('recordingHealthy') and status.get('recoveryComplete') and status.get('activeGames')==0:
                        break
                except (OSError,ValueError):pass
                self.last_tick=time.time();self.stop.wait(1)
            else:raise RuntimeError('Fresh native and recorder preflight unavailable')
            exchange(self.runtime/'native/lifecycle.sock',{'protocol':1,'op':'resume','bootId':status['bootId'],'releaseId':attempt['runtime_id']})
            attempt.update(message='Create intent persisted; a lost response is never retried',create_intent_unix=time.time())
            self.store.record_attempt(attempt)
            profiles={p['id']:p for p in self.config['catalog']}
            payload={'decks':[profiles[b]['deck']['cards'] for b in batch['plan']['bindings']],
                     'controllerSpecs':[{'mode':'commander-gym','profileId':b} for b in batch['plan']['bindings']],
                     'rules':'COMMANDER','gameMode':'FREE_FOR_ALL','gamesPerMatch':1}
            response=native_http(self.config['native_port'],'POST','/api/dev/ai-tournament',payload)
            lobby=response['lobbyId']
            if not isinstance(lobby,str) or not lobby or len(lobby)>128 or not all(c.isalnum() or c in '-_' for c in lobby):raise ValueError('Invalid native lobby identity')
            attempt.update(lobby_id=lobby,state='Running',started_unix=time.time(),message='Native private pod observed')
            self.store.record_attempt(attempt)
            last_good=time.time();deadline=time.time()+batch['plan']['wall_seconds']
            while not self.stop.is_set():
                self.last_tick=time.time()
                current=self.store.get(batch['id'])
                if current['desired']=='Cancelled':outcome='Interrupted';message='Owner requested interruption';break
                if time.time()>deadline:outcome='Interrupted';message='Per-game wall-time limit reached';break
                if any(self.unit('is-active',u)!='active' for u in (UNITS[3],UNITS[2],provider_unit(batch['plan']['execution_mode']))):
                    message='An isolated runtime service stopped';break
                try:
                    health=exchange(self.runtime/'native/lifecycle.sock',{'protocol':1,'op':'status'})
                    if health.get('releaseId')!=attempt['runtime_id'] or not health.get('recordingHealthy'):
                        message='Native recorder unhealthy or runtime identity changed';break
                    observed=native_http(self.config['native_port'],'GET','/api/dev/ai-tournament/'+lobby)
                    last_good=time.time()
                    lives=observed.get('liveGames',[]);finished=observed.get('completedGames',[])
                    attempt['observation']={'observed_unix':last_good,'source':'Private native lobby status','live_games':lives,'complete':observed.get('complete')}
                    if lives:attempt['game_id']=lives[0].get('gameSessionId')
                    actual=[g for g in finished if g.get('nativeGameOver') is True
                            and g.get('simulated',g.get('isSimulated')) is False]
                    if actual:
                        native_terminal=actual[-1];outcome='Completed';message='Native terminal outcome recorded';break
                except (OSError,ValueError,KeyError):
                    attempt['message']='Native observation unavailable; last state retained'
                    if time.time()-last_good>20:message='Native observation unavailable beyond freshness limit';break
                try:
                    progress=json.loads((self.root/'progress'/attempt['id']/'provider.json').read_text())
                    attempt['provider_progress']=progress
                    stopped = provider_stop(progress)
                    if stopped:
                        outcome, message = stopped
                        break
                except (OSError,ValueError,KeyError):pass
                attempt['budget']=self.budget_snapshot(batch);self.store.record_attempt(attempt);self.stop.wait(1)
            if self.stop.is_set() and native_terminal is None:outcome='Interrupted';message='Supervisor stopping'
            if native_terminal:
                attempt.update(game_id=native_terminal.get('gameSessionId'),winner_id=native_terminal.get('winnerId'),
                               is_draw=native_terminal.get('draw',native_terminal.get('isDraw')),native_result=native_terminal)
                # Give the existing recorder its normal terminal grace; no lifecycle finalization commands.
                for _ in range(20):
                    manifests=list((self.root/'captures'/attempt['id']).glob('*/manifest.json'))
                    if manifests:break
                    self.last_tick=time.time();self.stop.wait(1)
                # A session cleanup can appear as game-over in the lobby. The
                # recorder distinguishes that from a native rules terminal.
                game=attempt.get('game_id')
                for manifest in manifests:
                    value=json.loads(manifest.read_text())
                    if value.get('game_id')==game and value.get('outcome',{}).get('kind') in ('session_closed','finalized_aborted'):
                        outcome='Failed';message='Native session closed without a completed rules outcome'
                        attempt.update(winner_id=None,is_draw=None)
                        break
        except Exception as failure:
            import traceback
            frame=traceback.extract_tb(failure.__traceback__)[-1]
            print('Batch attempt failed:',type(failure).__name__,Path(frame.filename).name,frame.lineno,flush=True)
            # Fixed diagnostic only: never return transport bodies, model text or secrets.
            message='Isolated runtime preflight or create failed; no create retry. Inspect retained capture and service diagnostics.'
            if attempt.get('create_intent_unix'):outcome='Interrupted'
        finally:
            try:self.close_runtime()
            except Exception:
                attempt.update(state='Needs reconciliation',message='Owned runtime stop not confirmed; admission blocked')
                self.store.record_attempt(attempt)
                self.store.update(batch['id'],state='Needs reconciliation',desired='Paused',message=attempt['message'])
                self.error=attempt['message'];raise RuntimeError('Owned runtime requires reconciliation')
            # Only isolated sockets left by our stopped processes are unlinked.
            for directory in ('native','provider'):
                for path in (self.runtime/directory).glob('*.sock'):path.unlink()
            attempt.update(state=outcome,message=message,ended_unix=time.time(),budget=self.budget_snapshot(batch),
                           supervisor_termination=None if native_terminal else message)
            capture=self.root/'captures'/attempt['id']
            manifests=list(capture.glob('*/manifest.json')) if capture.exists() else []
            attempt['recording_state']='Finalized manifest observed; inspection verification pending' if manifests else 'Open or unavailable capture; inspect prefix diagnostics'
            # A manifest's existence alone does not establish recording completeness.
            attempt['recording_complete']=None
            self.store.finish_attempt(attempt)

    def loop(self):
        while not self.stop.is_set():
            self.last_tick=time.time()
            if not self.error:
                try:
                    batch=self.store.runnable()
                    if batch:
                        attempt=self.store.new_attempt(batch['id'])
                        if attempt:self.run_attempt(batch,attempt)
                except BatchConflict:pass
                except Exception:
                    self.error='Supervisor failed closed; inspect service diagnostics and restart to reconcile'
                    self.close_runtime()
            self.stop.wait(.5)


class RPC(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(5)
        uid=struct.unpack('3i',self.connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))[1]
        if uid not in (0,self.server.supervisor.config['panel_uid']):return
        raw=self.rfile.readline(65537)
        if len(raw)>65536:return
        try:
            value=self.server.supervisor.handle(json.loads(raw));response={'status':200,'value':value}
        except PermissionError:response={'status':403,'error':'Owner identity required'}
        except BatchConflict as error:response={'status':409,'error':str(error)}
        except KeyError:response={'status':404,'error':'Unknown batch or attempt'}
        except (ValueError,TypeError):response={'status':400,'error':'Invalid batch command'}
        except Exception:response={'status':503,'error':'Batch control unavailable'}
        self.wfile.write(json.dumps(response).encode()+b'\n')


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True,type=Path);args=parser.parse_args()
    config=json.loads(args.config.read_text());supervisor=Supervisor(config)
    lock=(supervisor.root/'supervisor.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    supervisor.recover()
    control=supervisor.runtime/'panel'
    control.mkdir(mode=0o755,exist_ok=True);control.chmod(0o755)
    path=control/'control.sock'
    if path.exists():path.unlink()
    class Server(socketserver.ThreadingUnixStreamServer):daemon_threads=True;request_queue_size=8
    server=Server(str(path),RPC);server.supervisor=supervisor
    os.chown(path,0,config['panel_gid']);path.chmod(0o660)
    thread=threading.Thread(target=supervisor.loop,daemon=True);thread.start()
    def stopping(*_):
        supervisor.stop.set();threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,stopping);signal.signal(signal.SIGINT,stopping)
    try:server.serve_forever(poll_interval=.2)
    finally:
        supervisor.stop.set();thread.join(110);supervisor.close_runtime();server.server_close();path.unlink(missing_ok=True)

if __name__=='__main__':main()
