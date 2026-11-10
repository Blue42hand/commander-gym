"""Durable owner commands and attempt ledger, separate from immutable evidence."""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time
import uuid

COMMANDS = {'start','pause','resume','finish','cancel','clone','retry'}
TERMINAL = {'Completed','Failed','Stopped'}
ID = re.compile(r'(?:batch|attempt)-[a-f0-9]{32}')


class BatchConflict(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def game_usage(attempt):
    """Attribute batch-ledger deltas without rewriting historical observations."""
    unavailable = {'state':'Unavailable','scope':'game','reason':'No usable usage snapshot was saved for this game'}
    end = attempt.get('budget')
    if not isinstance(end, dict): return unavailable
    start = attempt.get('budget_start')
    if start is None:
        if attempt.get('ordinal') != 1:
            return {**unavailable,'reason':'This older game has no saved starting balance; batch totals cannot establish its usage'}
        start = {}
        attribution = 'First game in batch; ledger started at zero'
    elif not isinstance(start, dict) or start.get('state') == 'Unavailable':
        return unavailable
    else:
        attribution = 'Difference from saved pre-game balances'
    keys = ('estimated_usd','requests','reported_tokens','reserved_or_used_tokens',
            'unsettled_requests','unsettled_token_requests','input_tokens','output_tokens')
    result = {'state':'Available','scope':'game','attribution':attribution,
              'observed_unix':end.get('observed_unix')}
    for key in keys:
        value, baseline = end.get(key), start.get(key, 0) if not start else start.get(key)
        valid = (lambda x: type(x) in (int,float) and math.isfinite(x) and x >= 0) if key == 'estimated_usd' else (lambda x: type(x) is int and x >= 0)
        if not valid(value) or not valid(baseline):
            result[key] = None
            continue
        delta = value - baseline
        result[key] = max(0,delta) if delta >= (-1e-9 if key == 'estimated_usd' else 0) else None
    if any(result[k] is None for k in keys[:6]):
        result.update(state='Partial',reason='Some saved counters are missing or cannot be attributed safely')
    return result


class BatchStore:
    def __init__(self, directory, catalog, pins):
        self.root = Path(directory)
        self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
        self.path = self.root/'batches.sqlite3'
        self.catalog = catalog
        self.pins = pins
        with self.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY, version INTEGER NOT NULL, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, value TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS attempts_batch ON attempts(batch_id);
                CREATE INDEX IF NOT EXISTS attempts_game ON attempts(json_extract(value,'$.game_id'));
                CREATE TABLE IF NOT EXISTS commands(key TEXT PRIMARY KEY, digest TEXT NOT NULL, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, batch_id TEXT NOT NULL, value TEXT NOT NULL);''')

    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=5)
        db.row_factory=sqlite3.Row
        try:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA synchronous=FULL')
            with db: yield db
        finally: db.close()

    def validate(self, raw):
        if not isinstance(raw,dict): raise ValueError('Batch plan must be an object')
        if set(raw)-{'name','purpose','bindings','game_count','wall_seconds','max_decisions','budget_usd','max_requests','max_tokens','failure_policy','execution_mode'}:
            raise ValueError('Unknown batch plan field')
        name=raw.get('name','')
        purpose=raw.get('purpose','')
        if not isinstance(name,str) or not 1<=len(name.strip())<=100:raise ValueError('Batch name must contain 1–100 characters')
        if not isinstance(purpose,str) or len(purpose)>1000:raise ValueError('Purpose exceeds limit')
        bindings=raw.get('bindings')
        mode=raw.get('execution_mode','paid')
        if mode not in ('paid','fixture','local'):raise ValueError('Invalid execution mode')
        available={p['id']:p for p in self.catalog if mode in p.get('execution_modes', ('paid','fixture'))}
        if not isinstance(bindings,list) or len(bindings)!=4 or any(not isinstance(x,str) or x not in available for x in bindings):
            raise ValueError('Select exactly four approved Binding snapshots in seat order')
        result={'name':name.strip(),'purpose':purpose,'bindings':bindings,'seats':[available[x] for x in bindings]}
        for key,low,high in [('game_count',1,50),('wall_seconds',30,7200),('max_decisions',1,100000),('max_requests',1,10000),('max_tokens',10000,100000000)]:
            value=raw.get(key)
            if type(value) is not int or not low<=value<=high:raise ValueError(f'{key} must be between {low} and {high}')
            result[key]=value
        budget=raw.get('budget_usd')
        if type(budget) not in (float,int) or not math.isfinite(budget) or not (0<=budget<=5 if mode=='local' else 0<budget<=5):raise ValueError('Estimated spending fence must be positive and at most $5 (zero for local)')
        if mode=='local' and budget!=0:raise ValueError('Local inference requires a zero provider spending fence')
        result['budget_usd']=float(budget)
        if raw.get('failure_policy','pause') not in ('pause','continue'):raise ValueError('Invalid failure policy')
        result.update(failure_policy=raw.get('failure_policy','pause'),execution_mode=raw.get('execution_mode','paid'),
                      concurrency=1,seed_policy='Native assigned; recorded seed retained, caller-selected seed unsupported',pins=self.pins)
        return result

    @staticmethod
    def _get(db, batch_id):
        row=db.execute('SELECT value FROM batches WHERE id=?',(batch_id,)).fetchone()
        if not row:raise KeyError('Unknown batch')
        return json.loads(row['value'])

    @staticmethod
    def _save(db,batch):
        db.execute('INSERT OR REPLACE INTO batches VALUES(?,?,?)',(batch['id'],batch['version'],json.dumps(batch)))

    @staticmethod
    def _event(db,batch_id,kind,actor,detail=''):
        event={'kind':kind,'actor':actor,'observed_unix':time.time(),'detail':detail}
        db.execute('INSERT INTO events(batch_id,value) VALUES(?,?)',(batch_id,json.dumps(event)))

    def create(self, raw, actor, key):
        return self.command(None,'create',{'plan':raw},actor,key)

    def command(self,batch_id,op,data,actor,key):
        if not isinstance(key,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{16,100}',key):raise ValueError('An idempotency key is required')
        if op not in COMMANDS|{'create'}:raise ValueError('Unsupported command')
        fingerprint=digest([batch_id,op,data,actor])
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            previous=db.execute('SELECT digest,value FROM commands WHERE key=?',(key,)).fetchone()
            if previous:
                if previous['digest']!=fingerprint:raise BatchConflict('Idempotency key already used for another command')
                return json.loads(previous['value'])
            if op=='create':
                plan=self.validate(data.get('plan'))
                now=time.time()
                batch={'id':'batch-'+uuid.uuid4().hex,'version':1,'state':'Draft','desired':'Paused','plan':plan,
                       'fingerprint':digest(plan),'actor':actor,'created_unix':now,'updated_unix':now,
                       'message':'Review the immutable plan before Start','observation':None}
            else:
                batch=self._get(db,batch_id)
                if type(data.get('version')) is not int or batch['version']!=data['version']:raise BatchConflict('Batch changed. Reload before issuing this command.')
                state=batch['state']
                if op=='clone':
                    fields={'name','purpose','bindings','game_count','wall_seconds','max_decisions','budget_usd','max_requests','max_tokens','failure_policy','execution_mode'}
                    plan=self.validate({k:v for k,v in batch['plan'].items() if k in fields});now=time.time()
                    batch={'id':'batch-'+uuid.uuid4().hex,'version':1,'state':'Draft','desired':'Paused','plan':plan,
                           'fingerprint':digest(plan),'actor':actor,'created_unix':now,'updated_unix':now,
                           'parent_batch_id':batch_id,'message':'Cloned draft; review before Start','observation':None}
                elif op=='retry':
                    attempt=db.execute('SELECT value FROM attempts WHERE id=? AND batch_id=?',(data.get('attempt_id'),batch_id)).fetchone()
                    if not attempt:raise ValueError('Select a failed or interrupted attempt')
                    attempt=json.loads(attempt['value'])
                    if attempt['state'] not in ('Failed','Interrupted'):raise ValueError('Only reconciled failed/interrupted attempts can be retried')
                    fields={'name','purpose','bindings','game_count','wall_seconds','max_decisions','budget_usd','max_requests','max_tokens','failure_policy','execution_mode'}
                    plan=self.validate({**{k:v for k,v in batch['plan'].items() if k in fields},'game_count':1,'name':batch['plan']['name'][:90]+' · retry'});now=time.time()
                    batch={'id':'batch-'+uuid.uuid4().hex,'version':1,'state':'Draft','desired':'Paused','plan':plan,
                           'fingerprint':digest(plan),'actor':actor,'created_unix':now,'updated_unix':now,
                           'parent_attempt_id':attempt['id'],'parent_batch_id':batch_id,
                           'message':'Linked retry draft; original attempt retained','observation':None}
                else:
                    if op=='start':
                        if state!='Draft':raise BatchConflict('Only a Draft can be started')
                        if data.get('fingerprint')!=batch['fingerprint']:raise BatchConflict('Reviewed plan fingerprint does not match')
                        batch.update(state='Queued',desired='Running',message='Queued for fresh runtime preflight')
                    elif op=='pause':
                        if state not in ('Queued','Running'):raise BatchConflict('Batch is not queued or running')
                        batch.update(state='Draining' if state=='Running' else 'Paused',desired='Paused',message='Pause requested; current game may finish')
                    elif op=='resume':
                        if state!='Paused':raise BatchConflict('Resume requires a reconciled Paused batch')
                        count=db.execute('SELECT count(*) FROM attempts WHERE batch_id=?',(batch_id,)).fetchone()[0]
                        if count>=batch['plan']['game_count']:raise BatchConflict('No unstarted games remain; create a linked retry draft')
                        batch.update(state='Queued',desired='Running',message='Queued to resume remaining planned games')
                    elif op=='finish':
                        if state in TERMINAL or state=='Needs reconciliation':raise BatchConflict('Batch cannot accept this command in its current state')
                        active=db.execute("SELECT value FROM attempts WHERE batch_id=?",(batch_id,)).fetchall()
                        running=any(json.loads(a['value'])['state'] in ('Starting','Running','Stopping') for a in active)
                        batch.update(state='Draining' if running else 'Stopped',desired='Stopped',message='Finish current game, then stop' if running else 'Unstarted work stopped')
                    elif op=='cancel':
                        if state not in ('Running','Draining'):raise BatchConflict('No active batch game to interrupt')
                        batch.update(state='Stopping',desired='Cancelled',message='Interruption requested for this batch-owned isolated runtime')
                    batch['version']+=1
                    batch['updated_unix']=time.time()
            self._save(db,batch)
            self._event(db,batch['id'],op,actor)
            response={'batch':batch,'command':op,'accepted':True}
            db.execute('INSERT INTO commands VALUES(?,?,?)',(key,fingerprint,json.dumps(response)))
            return response

    def get(self,batch_id):
        with self.db() as db:
            batch=self._get(db,batch_id)
            batch['attempts']=[json.loads(r['value']) for r in db.execute('SELECT value FROM attempts WHERE batch_id=? ORDER BY rowid',(batch_id,))]
            batch['events']=[json.loads(r['value']) for r in db.execute('SELECT value FROM events WHERE batch_id=? ORDER BY id DESC LIMIT 50',(batch_id,))]
        attempts=batch['attempts']
        for attempt in attempts: attempt['game_usage'] = game_usage(attempt)
        batch['counts']={state:sum(a['state']==state for a in attempts) for state in ('Starting','Running','Completed','Failed','Interrupted','Needs reconciliation')}
        batch['counts']['unstarted']=max(0,batch['plan']['game_count']-len(attempts))
        return batch

    def list(self,offset=0,limit=30):
        with self.db() as db:
            ids=[r['id'] for r in db.execute('SELECT id FROM batches ORDER BY rowid DESC LIMIT ? OFFSET ?',(min(100,limit),max(0,offset)))]
            total=db.execute('SELECT count(*) FROM batches').fetchone()[0]
        return {'batches':[self.get(i) for i in ids],'total':total}

    def update(self,batch_id,**changes):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE');batch=self._get(db,batch_id)
            batch.update(changes,updated_unix=time.time(),version=batch['version']+1)
            self._save(db,batch)
        return batch

    def record_attempt(self,attempt):
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO attempts VALUES(?,?,?)',(attempt['id'],attempt['batch_id'],json.dumps(attempt)))

    def new_attempt(self,batch_id):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE');batch=self._get(db,batch_id)
            if batch['desired']!='Running' or batch['state'] not in ('Queued','Running'):raise BatchConflict('Batch is no longer runnable')
            count=db.execute('SELECT count(*) FROM attempts WHERE batch_id=?',(batch_id,)).fetchone()[0]
            if count>=batch['plan']['game_count']:return None
            attempt={'id':'attempt-'+uuid.uuid4().hex,'batch_id':batch_id,'ordinal':count+1,'state':'Starting',
                     'created_unix':time.time(),'lobby_id':None,'game_id':None,'observation':None,'message':'Preparing isolated runtime',
                     'recording_complete':None,'recording_state':'Not observed','winner_id':None,'is_draw':None}
            db.execute('INSERT INTO attempts VALUES(?,?,?)',(attempt['id'],batch_id,json.dumps(attempt)))
            batch.update(state='Running',updated_unix=time.time(),version=batch['version']+1)
            self._save(db,batch)
            return attempt

    def recover(self):
        """A supervisor restart never repeats an uncertain create or resumes paid work."""
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            rows=list(db.execute('SELECT value FROM attempts'))
            for row in rows:
                attempt=json.loads(row['value'])
                if attempt['state'] in ('Starting','Running','Stopping'):
                    attempt.update(state='Needs reconciliation',message='Supervisor restarted; reconcile the owned runtime before further admission')
                    db.execute('UPDATE attempts SET value=? WHERE id=?',(json.dumps(attempt),attempt['id']))
                    batch=self._get(db,attempt['batch_id'])
                    batch.update(state='Needs reconciliation',desired='Paused',version=batch['version']+1)
                    self._save(db,batch)
            for row in list(db.execute('SELECT value FROM batches')):
                batch=json.loads(row['value'])
                if batch['state']=='Queued':
                    batch.update(state='Paused',desired='Paused',message='Supervisor restarted; explicit Resume required',version=batch['version']+1)
                    self._save(db,batch)

    def finish_attempt(self, attempt):
        """Resolve completion against the latest owner command in one transaction."""
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR REPLACE INTO attempts VALUES(?,?,?)',(attempt['id'],attempt['batch_id'],json.dumps(attempt)))
            batch=self._get(db,attempt['batch_id'])
            count=db.execute('SELECT count(*) FROM attempts WHERE batch_id=?',(batch['id'],)).fetchone()[0]
            desired=batch['desired']
            state=('Completed' if attempt['state']=='Completed' else 'Failed') if count>=batch['plan']['game_count'] else 'Queued'
            if desired=='Paused':state='Paused'
            elif desired in ('Stopped','Cancelled'):state='Stopped'
            elif attempt['state']!='Completed' and batch['plan']['failure_policy']=='pause':state='Failed' if count>=batch['plan']['game_count'] else 'Paused';desired='Paused'
            batch.update(state=state,desired=desired,message=attempt['message'],version=batch['version']+1,updated_unix=time.time())
            self._save(db,batch)
            self._event(db,batch['id'],'attempt '+attempt['state'],'supervisor',attempt['id'])

    def runnable(self):
        with self.db() as db:
            rows=db.execute('SELECT value FROM batches ORDER BY rowid').fetchall()
        for row in rows:
            batch=json.loads(row['value'])
            if batch['state']=='Queued' and batch['desired']=='Running':return batch
        return None

    def game_context(self, game_id):
        if not isinstance(game_id,str) or not 1<=len(game_id)<=128:raise ValueError('Invalid game identity')
        with self.db() as db:
            row=db.execute("SELECT value FROM attempts WHERE json_extract(value,'$.game_id')=? ORDER BY rowid DESC LIMIT 1",(game_id,)).fetchone()
            if row is None:return None
            attempt=json.loads(row['value'])
            batch=self._get(db,attempt['batch_id'])
        return {'batch_id':batch['id'],'attempt_id':attempt['id'],'state':attempt['state'],
                'supervisor_termination':attempt.get('supervisor_termination'),'execution_mode':batch['plan']['execution_mode'],
                'game_usage':game_usage(attempt)}
