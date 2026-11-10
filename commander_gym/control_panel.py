"""Loopback-only administrative evidence browser. Never owns a recorder/writer.

Annotations and rebuildable index live outside evidence. Authentication trusts
only identity headers from the local Tailscale Serve proxy; missing identities
are denied, including for evidence. Production runs with source mounts read-only.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import socket
import sqlite3
import stat
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .record_codec import decode_record, physical_lines
from .game_journal import _safe
from .panel_worker import metadata
from . import batch_rpc

STATES = ('Unreviewed', 'Needs follow-up', 'Investigating', 'Reviewed')
TAGS = ('Engine or rules issue', 'Pilot decision', 'Timeout or stall', 'Recorder or infrastructure',
        'Incomplete evidence', 'Interesting game')
MAX_RECORD = 2 * 1024 * 1024
MAX_PAGE = 128 * 1024
STATIC = Path(__file__).with_name('panel_static')


def utc():
    return datetime.now(timezone.utc).isoformat()


def read_json(path, cap=1024 * 1024):
    if path.is_symlink() or not path.is_file():
        raise ValueError('unavailable or unsafe source')
    with path.open('rb') as f:
        raw = f.read(cap + 1)
    if len(raw) > cap:
        raise ValueError('metadata too large')
    return json.loads(raw)


def sanitized(value, depth=0):
    """Defense in depth for legacy logs; unknown nested transport fields excluded."""
    if depth > 20:
        return '[depth limit]'
    if isinstance(value, dict):
        result = {}
        for key, item in list(value.items())[:200]:
            normalized = re.sub('[^a-z]', '', str(key).lower())
            if any(s in normalized for s in ('secret', 'token', 'password', 'credential', 'header', 'environment', 'environ', 'authorization', 'apikey', 'cookie', 'privatekey')):
                result[str(key)] = '[excluded]'
            elif key not in {'source_body', 'pinnedCards'}:
                result[str(key)] = sanitized(item, depth + 1)
        return result
    if isinstance(value, list):
        return [sanitized(x, depth + 1) for x in value[:200]]
    if isinstance(value, str):
        value = re.sub(r'\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{16,}', '[excluded credential]', value)
        value = re.sub(r'(?i)\bBearer\s+[A-Za-z0-9._~-]+', 'Bearer [excluded]', value)
        value = re.sub(r'(?i)(?:authorization|api[_-]?key|password|secret|access[_-]?token|cookie)\s*[:=]\s*\S+', '[excluded]', value)
        return value[:12000] + (' [truncated]' if len(value) > 12000 else '')
    return value


class Conflict(Exception):
    pass


class Panel:
    def __init__(self, config):
        self.config = config
        self.grouped_start = len(config['capture_roots'])
        self.roots = [Path(p).resolve(strict=True) for p in config['capture_roots'] + config.get('grouped_capture_roots', [])]
        self.state = Path(config['state_directory']).resolve()
        if any(self.state == r or r in self.state.parents for r in self.roots):
            raise ValueError('annotation state must be outside capture roots')
        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.indexdir = self.state / 'index'
        self.indexdir.mkdir(mode=0o700, exist_ok=True)
        self.database = self.state / 'reviews.sqlite3'
        self.lock = threading.RLock()
        self.records = {}
        self.locations = {}
        self.coverage = {'state': 'indexing', 'observed': 0, 'limit': 500, 'errors': [], 'scope': config.get('scope', 'Configured capture roots only')}
        self.stop = threading.Event()
        with self.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS reviews (
                ref TEXT PRIMARY KEY, version INTEGER NOT NULL, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS changes (
                id INTEGER PRIMARY KEY, ref TEXT NOT NULL, version INTEGER NOT NULL, value TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS change_ref ON changes(ref, id);''')
        self.key = secrets.token_bytes(32)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.database, timeout=5)
        try:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA synchronous=FULL')
            with db:
                yield db
        finally:
            db.close()

    def review(self, ref):
        with self.db() as db:
            row = db.execute('SELECT value FROM reviews WHERE ref=?', (ref,)).fetchone()
        return json.loads(row[0]) if row else {'version': 0, 'state': 'Unreviewed', 'tags': [], 'note': '', 'anchor': '', 'next_action': ''}

    def annotate(self, ref, data, actor):
        record = self.get(ref)
        directory = self.locations.get(ref)
        if directory is None or directory.is_symlink() or self.signature(directory)[0] != record['source_revision']:
            raise Conflict('Source evidence changed or became unavailable. Wait for indexing, then reload the game.')
        if data.get('state') not in STATES or not isinstance(data.get('tags'), list) or any(t not in TAGS for t in data['tags']):
            raise ValueError('invalid review state or tags')
        for k, n in (('note', 4000), ('anchor', 250), ('next_action', 1000)):
            if not isinstance(data.get(k, ''), str) or len(data.get(k, '')) > n:
                raise ValueError('annotation field exceeds limit')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT version,value FROM reviews WHERE ref=?', (ref,)).fetchone()
            version = current[0] if current else 0
            if type(data.get('version')) is not int or data['version'] != version:
                raise Conflict('A newer review was saved. Reload the review before merging your draft.')
            if data.get('source_revision') != record['source_revision']:
                raise Conflict('Source evidence changed. Reload the game before saving this observation.')
            value = {k: data.get(k, '') for k in ('state', 'tags', 'note', 'anchor', 'next_action')}
            value.update(version=version + 1, actor=actor, created_at=json.loads(current[1])['created_at'] if current else utc(),
                         updated_at=utc(), source_revision=record['source_revision'], artifact_hash=record.get('artifact_hash'),
                         run_id=record.get('run_id'), game_id=record.get('game_id'), diagnostic_ref=ref if not record.get('run_id') else None)
            encoded = json.dumps(value)
            db.execute('INSERT OR REPLACE INTO reviews VALUES (?,?,?)', (ref, version + 1, encoded))
            db.execute('INSERT INTO changes(ref,version,value) VALUES(?,?,?)', (ref, version + 1, encoded))
        return value

    def changes(self, ref):
        with self.db() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT value FROM changes WHERE ref=? ORDER BY id DESC LIMIT 20', (ref,))]

    def get(self, ref):
        with self.lock:
            if ref not in self.records:
                raise KeyError('unknown reference')
            result = dict(self.records[ref])
        result.pop('evidence_index', None)
        result['review'] = self.review(ref)
        result['new_evidence'] = result['review'].get('source_revision') not in (None, result['source_revision'])
        return result

    def signature(self, directory):
        entries = []
        # Bound directory work; never recurse into arbitrary artifact directories.
        with os.scandir(directory) as scan:
            for i, entry in enumerate(scan):
                if i >= 1000:
                    raise ValueError('capture file inventory limit')
                if entry.is_symlink():
                    raise ValueError('capture contains symlink')
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISREG(info.st_mode):
                    entries.append((entry.name, info.st_size, info.st_mtime_ns, info.st_ino))
        return hashlib.sha256(json.dumps(sorted(entries)).encode()).hexdigest(), entries

    def quick_record(self, directory, ref, revision, entries):
        record = {'index_version': 2, 'seal_state': 'Unsealed', 'ref': ref, 'diagnostic_ref': ref, 'run_id': None, 'game_id': None, 'participants': [],
                  'source_revision': revision, 'inventory_bytes': sum(e[1] for e in entries), 'observed_at': utc(), 'verification': 'pending', 'issues': [],
                  'lifecycle': 'Unknown', 'result': 'Unknown', 'recording_complete': None, 'ready_for_analysis': None,
                  'turns': None, 'duration': None, 'pins': {}, 'coverage': [], 'artifacts': [], 'reports': [],
                  'termination': None, 'supervisor_termination': None, 'source_kind': 'diagnostic capture'}
        try:
            p = directory / '000000.jsonl'
            if p.exists():
                with p.open('rb') as stream:
                    row = json.loads(decode_record(stream.readline(MAX_RECORD + 1), max_bytes=MAX_RECORD))
                _safe(row)
                record.update(metadata(row))
                record['source_kind'] = 'journal capture (verification pending)'
            elif (directory / 'native-000000.ndjson').exists():
                record['issues'].append('Native-only capture; canonical journal missing')
            else:
                record['issues'].append('Legacy or missing journal; unsupported summary schema')
            p = directory / 'manifest.json'
            if p.exists():
                record['seal_state'] = 'Finalized manifest present'
                m = read_json(p, cap=8 * 1024 * 1024)
                _safe(m)
                if not isinstance(m, dict) or not isinstance(m.get('artifacts'), list):
                    raise ValueError('invalid manifest')
                for key in ('run_id', 'game_id', 'artifact_hash', 'recording_complete', 'ready_for_analysis', 'analysis'):
                    record[key] = m.get(key)
                if not isinstance(m.get('gaps', []), list) or not isinstance(m.get('outcome') or {}, dict):
                    raise ValueError('invalid outcome or gap shape')
                record['coverage'] = m.get('gaps', [])
                outcome = m.get('outcome') or {}
                kind = outcome.get('kind')
                record['termination'] = kind
                record['supervisor_termination'] = outcome.get('administrative_stall')
                record['outcome'] = outcome
                record['lifecycle'] = 'Completed' if kind == 'native_terminal' else 'Interrupted' if kind in ('session_closed', 'finalized_aborted') else 'Failed' if kind in ('failed', 'failure') else 'Unknown'
                winner = outcome.get('winner_id')
                record['result'] = ('Winner: ' + next((p['name'] or p['id'] for p in record['participants'] if p['id'] == winner), str(winner))) if winner else 'Draw' if outcome.get('draw') is True else 'Unknown'
                record['artifacts'] = [{k: a.get(k) for k in ('path', 'role', 'artifact_id', 'sha256', 'size_bytes')} for a in m['artifacts'][:200] if isinstance(a, dict)]
            else:
                record['issues'].append('Missing finalized manifest; outcome is not confirmed')
            # Existing report files only: never claim or run an analysis job.
            analysis = directory / 'analysis'
            if analysis.is_dir() and not analysis.is_symlink():
                for folder in sorted(analysis.iterdir())[:20]:
                    if folder.is_dir() and not folder.is_symlink():
                        for p in list(folder.iterdir())[:20]:
                            if p.is_file() and not p.is_symlink() and p.suffix in ('.json', '.md', '.txt'):
                                record['reports'].append({'path': str(p.relative_to(directory)), 'size_bytes': p.stat().st_size})
        except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
            record['issues'].append('Malformed, unreadable or too-large metadata')
        return sanitized(record)

    def index_once(self):
        found, errors, limited = [], [], False
        for root_index, root in enumerate(self.roots):
            try:
                with os.scandir(root) as scan:
                    for entry in scan:
                        if len(found) >= 500:
                            limited = True
                            break
                        if entry.name.startswith('.'):
                            continue
                        if entry.is_dir(follow_symlinks=False) or entry.is_symlink():
                            if root_index < self.grouped_start:
                                found.append((root_index, entry.name))
                            elif entry.is_dir(follow_symlinks=False):
                                with os.scandir(entry.path) as nested:
                                    for child in nested:
                                        if len(found) >= 500:
                                            limited = True
                                            break
                                        if not child.name.startswith('.') and (child.is_dir(follow_symlinks=False) or child.is_symlink()):
                                            found.append((root_index, entry.name + '/' + child.name))
            except OSError:
                errors.append('Capture root ' + str(root_index + 1) + ' unavailable')
        # Publish bounded metadata for the whole inventory before spending a
        # per-capture verification budget. A large first game must not hide the
        # rest of the archive while the background verifier works.
        for root_index, name in found:
            directory = self.roots[root_index] / name
            ref = 'capture-' + hashlib.sha256((str(root_index) + ':' + name).encode()).hexdigest()[:24]
            try:
                if directory.is_symlink():
                    continue
                revision, entries = self.signature(directory)
                with self.lock:
                    current = self.records.get(ref)
                if current and current.get('source_revision') == revision:
                    continue
                record = self.quick_record(directory, ref, revision, entries)
                try:
                    saved = read_json(self.indexdir / (ref + '.json'))
                    if saved['source_revision'] == revision and saved.get('index_version') == 2 and saved.get('verification') not in ('pending', 'unavailable'):
                        record = saved
                except (OSError, ValueError, KeyError, TypeError):
                    pass
                with self.lock:
                    self.records[ref] = record
                    self.locations[ref] = directory
                    self.coverage['observed'] = len(self.records)
            except (OSError, ValueError, TypeError, AttributeError, RecursionError):
                pass  # The detailed pass below publishes the diagnostic.
        found.sort(key=lambda item: self.records.get('capture-' + hashlib.sha256((str(item[0]) + ':' + item[1]).encode()).hexdigest()[:24], {}).get('inventory_bytes', 0))
        for root_index, name in found:
            if self.stop.is_set():
                return
            directory = self.roots[root_index] / name
            ref = 'capture-' + hashlib.sha256((str(root_index) + ':' + name).encode()).hexdigest()[:24]
            try:
                if directory.is_symlink():
                    raise ValueError('capture is a symlink')
                revision, entries = self.signature(directory)
                with self.lock:
                    current = self.records.get(ref)
                if current and current.get('source_revision') == revision and current.get('verification') != 'pending':
                    continue
                record = self.quick_record(directory, ref, revision, entries)
                with self.lock:
                    self.locations[ref] = directory
                    self.records[ref] = record
                    self.coverage['observed'] = len(self.records)
                cache = self.indexdir / (ref + '.json')
                try:
                    saved = read_json(cache)
                    if saved['source_revision'] == revision and saved.get('index_version') == 2 and saved.get('verification') not in ('pending', 'unavailable'):
                        with self.lock:
                            self.records[ref] = saved
                        continue
                except (OSError, ValueError, KeyError, TypeError):
                    pass
                if any(re.fullmatch(r'\d{6}\.jsonl', e[0]) for e in entries):
                    start = time.monotonic()
                    try:
                        process = subprocess.run([sys.executable, '-B', '-m', 'commander_gym.panel_worker', str(directory)],
                                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=40,
                                                 env={k: v for k, v in os.environ.items() if k in ('PATH', 'LANG', 'PYTHONPATH')}, check=True)
                        inspected = json.loads(process.stdout)
                        metadata_issues = record['issues']
                        record.update(inspected)
                        record['issues'] = list(dict.fromkeys(metadata_issues + record['issues']))
                        if record.get('verification') == 'verified':
                            record['source_kind'] = 'verified finalized manifest'
                        elif record.get('verification') == 'damaged':
                            record['coverage'].append('Integrity diagnostic; inspect source before trusting result')
                        if record.get('started_at') and record.get('last_recorded_at') and record.get('termination'):
                            record['duration'] = max(0, (datetime.fromisoformat(record['last_recorded_at']) - datetime.fromisoformat(record['started_at'])).total_seconds())
                    except (subprocess.SubprocessError, ValueError, OSError):
                        record['verification'] = 'budget-limited'
                        record['issues'].append('Inspection exceeded resource budget; no verification claim')
                    record['inspection_seconds'] = round(time.monotonic() - start, 3)
                else:
                    record['verification'] = 'unavailable'
                if self.signature(directory)[0] != revision:
                    record['verification'] = 'pending'
                    record['issues'].append('Source changed during inspection; waiting for the next observation')
                evidence_index = record.pop('evidence_index', [])
                record = sanitized(record)
                record['evidence_index'] = evidence_index
                # Cache is replaceable. Notes live in a different file and transaction.
                temporary = cache.with_suffix('.pending')
                temporary.write_text(json.dumps(record))
                temporary.replace(cache)
                with self.lock:
                    self.records[ref] = record
            except (OSError, ValueError, TypeError, AttributeError, RecursionError) as error:
                with self.lock:
                    self.records[ref] = {'ref': ref, 'source_revision': 'unreadable', 'participants': [], 'pins': {},
                                         'lifecycle': 'Unknown', 'result': 'Unknown', 'verification': 'unavailable',
                                         'coverage': [], 'issues': ['Capture unavailable: ' + type(error).__name__],
                                         'diagnostic_ref': ref, 'observed_at': utc(), 'source_kind': 'panel-local diagnostic reference'}
        with self.lock:
            self.coverage.update(state='partial' if errors or limited else 'complete', errors=errors,
                                 observed=len(self.records), updated_at=utc(), scope=self.config.get('scope', 'Configured roots only'))

    def index_loop(self):
        while not self.stop.is_set():
            self.index_once()
            self.stop.wait(60)

    def live_snapshot(self):
        fallback={'available':False,'fresh':False,'running':None,'running_known':0,'coverage_complete':False,
                  'runner_state':'Unavailable','runtimes':[],'batch':{'available':False,'active':[],'issues':[],'queued':None},
                  'scope':'Live observer unavailable','limitations':[],'age_seconds':None}
        if not self.config.get('live_snapshot'):return fallback
        try:
            value=read_json(Path(self.config['live_snapshot']),512*1024)
            if value.get('schema_version')!=1:raise ValueError()
            observed=value['observed_unix']
            if type(observed) not in (int,float) or not math.isfinite(observed):raise ValueError()
            age=time.time()-observed
            if age < -5:raise ValueError()
            fresh=age<=20
            runtimes=value['runtimes']
            if not isinstance(runtimes,list) or len(runtimes)!=2:raise ValueError()
            for r in runtimes:
                stamp=r.get('observed_unix')
                r['age_seconds']=max(0,time.time()-stamp) if type(stamp) in (int,float) else None
                r['fresh']=fresh and r.get('available') is True and r['age_seconds'] is not None and r['age_seconds']<=20
                if not r['fresh']:
                    r['running']=None;r['complete']=False
                    if r.get('available'):r['state']='Stale observation'
            complete=fresh and all(r.get('complete') and r['fresh'] for r in runtimes)
            value.update(available=True,fresh=fresh,age_seconds=max(0,age),coverage_complete=complete,
                         running=value.get('running') if complete else None,
                         running_known=sum(r.get('running') or 0 for r in runtimes))
            if not fresh:value['runner_state']='Stale observation'
            elif not complete and not value['running_known'] and value.get('runner_state')=='Idle':value['runner_state']='Unavailable'
            if not fresh:value['batch']={**value.get('batch',{}),'available':False,'queued':None,'recent_totals':{}}
            return value
        except (OSError,ValueError,TypeError,KeyError,AttributeError):return fallback

    def health(self, live=None):
        result = {'observed_at': utc(), 'freshness_seconds': self.config.get('health_fresh_seconds', 20), 'running': None, 'runner_state': 'Unknown', 'live_coverage': 'No complete authoritative active-game source configured',
                  'recorder_state': 'Unavailable', 'sources': {}, 'services': {}}
        for name, path in self.config.get('health_snapshots', {}).items():
            try:
                value = read_json(Path(path), 64 * 1024)
                observed = value.get('observedUnix')
                if observed is None and value.get('utc'):
                    observed = datetime.fromisoformat(value['utc'].replace('Z', '+00:00')).timestamp()
                age = max(0, time.time() - float(observed)) if observed is not None else None
                fresh = age is not None and age <= self.config.get('health_fresh_seconds', 20)
                fields = ('utc', 'observedUnix', 'recordingHealthy', 'recordingComplete', 'recoveryComplete', 'producerCoverageComplete',
                          'registeredSources', 'connectedSources', 'pendingRecordWrites', 'acknowledgedIncomplete', 'engineSha', 'gymSha', 'releaseId')
                result['sources'][name] = {'age_seconds': age, 'fresh': fresh, 'value': {k: value[k] for k in fields if k in value}}
            except (OSError, ValueError, TypeError, AttributeError, RecursionError):
                result['sources'][name] = {'available': False, 'fresh': False}
        sources = result['sources']
        if sources and all(x.get('fresh') for x in sources.values()):
            result['recorder_state'] = 'Healthy' if all(x['value'].get('recordingHealthy') is True for x in sources.values()) else 'Warning'
        elif any(x.get('value') for x in sources.values()):
            result['recorder_state'] = 'Stale observation'
        live=live if live is not None else self.live_snapshot()
        if self.config.get('live_snapshot'):
            result.update(running=live['running'],runner_state=live['runner_state'],live_coverage=live['scope'])
            if live['fresh']:
                recorder=[r['recorder_state'] for r in live['runtimes']]
                result['recorder_state']='Warning' if 'Warning' in recorder else 'Unavailable' if 'Unavailable' in recorder else 'Healthy'
                result['services']=live.get('services',{})
            else:result['recorder_state']='Stale observation' if live['available'] else 'Unavailable'
        if not result['services']:
            from .local_display import _run
            for name in self.config.get('services', []):
                if re.fullmatch(r'[a-zA-Z0-9_.@-]+\.service', name):
                    result['services'][name] = _run('systemctl', 'is-active', name) or 'unavailable'
        return result

    def history_records(self):
        with self.lock:records=[dict(r) for r in self.records.values()]
        refs=[r['ref'] for r in records]
        with self.db() as db:
            reviews={r[0]:json.loads(r[1]) for r in db.execute('SELECT ref,value FROM reviews WHERE ref IN ('+','.join('?' for _ in refs)+')',refs)} if refs else {}
        for r in records:
            r.pop('evidence_index',None)
            r['review']=reviews.get(r['ref'],{'version':0,'state':'Unreviewed','tags':[],'note':'','anchor':'','next_action':''})
            r['new_evidence']=r['review'].get('source_revision') not in (None,r['source_revision'])
        records.sort(key=lambda r:r.get('started_at') or '',reverse=True)
        return records

    def history(self, query):
        records = self.history_records()
        text = query.get('q', [''])[0].lower()[:200]
        for key, field in [('lifecycle', 'lifecycle'), ('recording', 'verification')]:
            choice = query.get(key, [''])[0]
            if choice:
                records = [r for r in records if r.get(field) == choice]
        state = query.get('review', [''])[0]
        if state:
            records = [r for r in records if r['review']['state'] == state]
        if text:
            records = [r for r in records if text in json.dumps({k:r.get(k) for k in ('ref','run_id','game_id','participants','pins')}).lower()]
        for key, compare in [('from', lambda a,b:a>=b), ('to', lambda a,b:a<=b+'T23:59:59.999999')]:
            value = query.get(key, [''])[0]
            if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                records = [r for r in records if r.get('started_at') and compare(r['started_at'], value)]
        if query.get('triage') == ['1']:
            records = [r for r in records if self.needs_inspection(r)]
            records.sort(key=lambda r: 0 if r.get('verification') in ('damaged', 'unavailable') else 1 if r.get('verification') == 'unsealed' or r.get('lifecycle') in ('Failed', 'Interrupted') else 2)
        limit = min(100, max(1, int(query.get('limit', ['20'])[0])))
        offset = min(100000, max(0, int(query.get('offset', ['0'])[0])))
        return {'records': records[offset:offset + limit], 'total': len(records), 'offset': offset, 'limit': limit, 'coverage': self.coverage}

    @staticmethod
    def needs_inspection(r):
        review = r['review']
        if review['state'] == 'Reviewed' and not r.get('new_evidence'):
            return False
        # Baseline replay/conversion gaps are coverage, not recurring urgent bugs.
        urgent = r.get('lifecycle') in ('Failed', 'Interrupted') or r.get('verification') in ('damaged', 'unsealed', 'unavailable') or any(not i.startswith('Inspection time budget') and not i.startswith('Inspection exceeded resource') for i in r.get('issues', []))
        return urgent or bool(review.get('tags')) or review['state'] in ('Needs follow-up', 'Investigating')

    def overview(self):
        all_records=self.history_records();live=self.live_snapshot();health=self.health(live)
        since=time.time()-86400
        def recent(r):
            try:return bool(r.get('last_recorded_at')) and datetime.fromisoformat(r['last_recorded_at']).timestamp()>=since
            except (ValueError,TypeError):return False
        batch=live['batch'];totals=batch.get('recent_totals') or {}
        # The batch ledger owns supervisor dispositions. Exclude its capture roots
        # from that union so a terminal/failed manifest cannot be counted twice.
        ordinary=all_records
        if live['fresh'] and batch.get('available'):
            ordinary=[r for r in all_records if not any(self.locations.get(r['ref'],Path('/')).is_relative_to(root) for root in self.roots[self.grouped_start:])]
        counts={'running':live['running'],'running_known':live['running_known'],
                'completed':sum(r.get('lifecycle')=='Completed' and recent(r) for r in ordinary)+totals.get('completed',0),
                'failed':sum(r.get('lifecycle') in ('Failed','Interrupted') and recent(r) for r in ordinary)+totals.get('failed',0),
                'triage':sum(self.needs_inspection(r) for r in all_records),
                'time_scope_partial':any(not r.get('last_recorded_at') for r in ordinary) or (bool(self.config.get('live_snapshot')) and (not batch.get('available') or batch.get('metadata_complete') is False))}
        by_game={r.get('game_id'):r for r in all_records if r.get('game_id')}
        for a in batch.get('active',[])+batch.get('issues',[]):
            record=by_game.get(a.get('game_id'))
            if record and self.locations[record['ref']].parent.name==a['id']:
                a['capture_ref']=record['ref'];a['review_state']=record['review']['state']
        triage=[r for r in all_records if self.needs_inspection(r)]
        return {'health':health,'live':live,'counts':counts,'coverage':self.coverage,'recent':all_records[:8],
                'triage':triage[:5],'total':len(all_records)}

    def evidence(self, ref, cursor='', search='', anchor=None):
        record = self.get(ref)
        directory = self.locations.get(ref)
        if directory is None or directory.is_symlink():
            raise ValueError('capture unavailable')
        if self.signature(directory)[0] != record['source_revision']:
            raise Conflict('Source changed since indexing. Reload after the next inventory observation.')
        files = sorted(p.name for p in directory.glob('*.jsonl') if re.fullmatch(r'\d{6}\.jsonl', p.name))[:1000]
        index, offset = 0, 0
        if anchor is not None and not cursor:
            anchor = max(0, min(10000000, int(anchor)))
            with self.lock:
                seek_points = self.records[ref].get('evidence_index', [])
            candidates = [a for a in seek_points if a[0] <= anchor]
            if candidates:
                _, index, offset = candidates[-1]
        if cursor:
            try:
                payload, signature = cursor.split('.')
                expected = hmac.new(self.key, (ref + payload).encode(), hashlib.sha256).hexdigest()[:24]
                if not hmac.compare_digest(signature, expected):
                    raise ValueError()
                position = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
                index, offset = position['index'], position['offset']
                if position['revision'] != record['source_revision']:
                    raise Conflict('Source changed; restart evidence paging')
            except (ValueError, TypeError, KeyError):
                raise ValueError('invalid evidence cursor')
        rows, used, scanned, diagnostic = [], 0, 0, None
        start = time.monotonic()
        while index < len(files) and len(rows) < 20 and scanned < 200 and time.monotonic() - start < 1:
            path = directory / files[index]
            if path.is_symlink():
                raise ValueError('unsafe source')
            with path.open('rb') as stream:
                stream.seek(offset)
                physical = stream.readline(MAX_RECORD + 1)
                if not physical:
                    index += 1
                    offset = 0
                    continue
                if len(physical) > MAX_RECORD:
                    diagnostic = 'Record exceeds the 2 MiB panel limit. Source is retained; this record cannot be displayed.'
                    break
                if not physical.endswith(b'\n'):
                    diagnostic = 'Partial journal tail; evidence ends at the last complete record.'
                    break
                try:
                    row = json.loads(decode_record(physical, max_bytes=MAX_RECORD))
                    _safe(row)
                    if not isinstance(row, dict):
                        raise ValueError()
                except (ValueError, KeyError, TypeError):
                    diagnostic = 'Malformed, forbidden or too-large record; paging stopped.'
                    break
                safe = sanitized(row)
                excerpt = json.dumps(safe, ensure_ascii=False, indent=2)
                # Always progress across large rows, showing an explicit clipped excerpt.
                clipped = len(excerpt) > 14000
                excerpt = excerpt[:14000] + ('\n[Excerpt truncated to 14,000 characters]' if clipped else '')
                byte_size = len(excerpt.encode())
                if used + byte_size > MAX_PAGE:
                    break
                offset = stream.tell()
                scanned += 1
                if (anchor is None or row.get('sequence', -1) >= anchor) and search.lower() in excerpt.lower():
                    rows.append({'sequence': row.get('sequence'), 'kind': row.get('kind'), 'recorded_at': row.get('recorded_at'),
                                 'sha256': row.get('sha256'), 'excerpt': excerpt, 'truncated': clipped})
                    used += byte_size
        next_cursor = None
        if index < len(files) and not diagnostic:
            payload = base64.urlsafe_b64encode(json.dumps({'index':index,'offset':offset,'revision':record['source_revision']}).encode()).decode().rstrip('=')
            next_cursor = payload + '.' + hmac.new(self.key, (ref + payload).encode(), hashlib.sha256).hexdigest()[:24]
        if anchor is not None and not rows and not diagnostic:
            diagnostic = 'Anchor not reached in this bounded page. Continue paging or wait for the evidence index.'
        return {'events': rows, 'next_cursor': next_cursor, 'diagnostic': diagnostic,
                'scope': 'At most 200 source rows, 20 results and 128 KiB per page; search applies to displayed excerpts',
                'verification': record['verification'], 'source_revision': record['source_revision']}

    def artifact(self, ref, name):
        record = self.get(ref)
        # Never allow arbitrary host paths, even from unverified manifests.
        allowed = {a['path'] for a in record.get('artifacts', []) if isinstance(a.get('path'), str) and Path(a['path']).name == a['path']}
        allowed.update(a['path'] for a in record.get('reports', []))
        if name not in allowed or name.startswith('/') or '..' in Path(name).parts:
            raise KeyError('unknown artifact')
        directory = self.locations[ref]
        path = directory
        for part in Path(name).parts:
            path = path / part
            if path.is_symlink():
                raise ValueError('unsafe artifact')
        if path.stat().st_size > 512 * 1024 or path.suffix not in ('.json', '.jsonl', '.md', '.txt'):
            raise ValueError('Artifact exceeds the inline limit or is not a supported text report. Inspect it on Tolaria.')
        with path.open('rb') as stream:
            text = stream.read(512 * 1024 + 1).decode('utf-8', errors='replace')
        # Text only, no active HTML, secrets stripped. Original never served raw.
        return sanitized(text)


class Handler(BaseHTTPRequestHandler):
    server_version = 'CommanderPanel/0.1'

    def log_message(self, *args):
        pass  # No query strings, evidence, identity headers or notes in logs.

    def response(self, status, value, mime='application/json; charset=utf-8'):
        body = json.dumps(value).encode() if mime.startswith('application/json') else value if isinstance(value, bytes) else value.encode()
        self.send_response(status)
        for k,v in {'Content-Type':mime,'Content-Length':str(len(body)),'Cache-Control':'no-store',
                    'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Referrer-Policy':'no-referrer',
                    'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"}.items():
            self.send_header(k,v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def authorized(self):
        login = self.headers.get('Tailscale-User-Login')
        if login not in self.server.panel.config['allowed_logins'] or self.headers.get_all('Tailscale-User-Login') != [login]:
            self.response(403, {'error':'Owner identity required. Use an authorized, untagged Tailscale device.'})
            return False
        return True

    def route(self):
        path = unquote(urlsplit(self.path).path)
        prefix = self.server.panel.config.get('base_path', '/gym')
        if path == prefix:
            return '/', parse_qs(urlsplit(self.path).query)
        if not path.startswith(prefix + '/'):
            raise KeyError('unknown route')
        return path[len(prefix):], parse_qs(urlsplit(self.path).query)

    def do_GET(self):
        if not self.authorized():
            return
        panel = self.server.panel
        try:
            path, query = self.route()
            if path == '/api/overview':
                self.response(200, panel.overview())
            elif path == '/api/batches' or re.fullmatch(r'/api/batches/(?:catalog|batch-[a-f0-9]{32})', path):
                tail = path.split('/')[-1]
                op = 'list' if tail == 'batches' else 'catalog' if tail == 'catalog' else 'get'
                self.response(200, batch_rpc.call(panel.config, self.headers['Tailscale-User-Login'], op,
                    batch_id=tail if op == 'get' else None, data={'offset':int(query.get('offset',['0'])[0])}))
            elif path == '/api/history':
                self.response(200, panel.history(query))
            elif path == '/api/session':
                # Per-actor CSRF token; no cookies or long-lived client credential.
                actor = self.headers['Tailscale-User-Login']
                token = hmac.new(panel.key, actor.encode(), hashlib.sha256).hexdigest()
                self.response(200, {'csrf':token, 'actor':actor, 'storage':'sqlite'})
            elif path == '/api/health':
                self.response(200, {'panel':'available', 'sources':panel.health(), 'coverage':panel.coverage})
            elif path.startswith('/api/games/'):
                parts = path.strip('/').split('/')
                ref = parts[2]
                if len(parts) == 3:
                    record=panel.get(ref)
                    if panel.config.get('batch_socket') and record.get('game_id'):
                        try:
                            context=batch_rpc.call(panel.config,self.headers['Tailscale-User-Login'],'game_context',data={'game_id':record['game_id']})
                            if context and panel.locations[ref].parent.name==context['attempt_id']:
                                record['managed_batch']=context
                                record['supervisor_termination']=context['supervisor_termination']
                        except batch_rpc.BatchRPCError:
                            record['batch_context_availability']='Unavailable'
                    self.response(200, {**record, 'review_history':panel.changes(ref)})
                elif parts[3] == 'evidence' and len(parts) == 4:
                    self.response(200, panel.evidence(ref, query.get('cursor',[''])[0], query.get('q',[''])[0][:200], query.get('anchor',[None])[0]))
                elif parts[3] == 'artifact' and len(parts) == 4:
                    self.response(200, panel.artifact(ref, query.get('name',[''])[0]), 'text/plain; charset=utf-8')
                else:
                    raise KeyError('unknown route')
            elif path in ('/', '/history', '/triage', '/batches', '/batches/new') or re.fullmatch(r'/(?:games/capture-[a-f0-9]{24}|batches/batch-[a-f0-9]{32})', path):
                self.response(200, (STATIC/'index.html').read_bytes(), 'text/html; charset=utf-8')
            elif path in ('/app.js','/batches.js','/style.css') or re.fullmatch(r'/fonts/[a-z0-9-]+\.(?:woff2|ttf)',path):
                file = STATIC / path.lstrip('/')
                mime = 'text/javascript; charset=utf-8' if path.endswith('.js') else 'text/css; charset=utf-8' if path.endswith('.css') else 'font/ttf' if path.endswith('.ttf') else 'font/woff2'
                self.response(200, file.read_bytes(), mime)
            else:
                raise KeyError('unknown route')
        except KeyError:
            self.response(404, {'error':'Record or route not found'})
        except batch_rpc.BatchRPCError as error:
            self.response(error.status, {'error':str(error)})
        except Conflict as error:
            self.response(409, {'error':str(error)})
        except (ValueError, TypeError, OSError, IndexError, AttributeError, RecursionError):
            self.response(400, {'error':'Source unavailable, unsafe request, or configured read limit reached'})

    def do_POST(self):
        if not self.authorized():
            return
        panel = self.server.panel
        actor = self.headers['Tailscale-User-Login']
        token = hmac.new(panel.key, actor.encode(), hashlib.sha256).hexdigest()
        if self.headers.get('Origin') != panel.config['origin'] or not hmac.compare_digest(self.headers.get('X-CSRF-Token',''), token):
            self.response(403, {'error':'Same-origin request and CSRF token required'})
            return
        try:
            path, query = self.route()
            match = re.fullmatch(r'/api/games/(capture-[a-f0-9]{24})/review', path)
            batch_match = re.fullmatch(r'/api/batches(?:/(batch-[a-f0-9]{32})/(start|pause|resume|finish|cancel|clone|retry))?', path)
            if not match and not batch_match:
                raise KeyError()
            size = int(self.headers.get('Content-Length','0'))
            if not 0 < size <= 16000 or self.headers.get_content_type() != 'application/json':
                raise ValueError()
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError()
            if batch_match:
                batch_id, op = batch_match.groups()
                self.response(200, batch_rpc.call(panel.config, actor, op or 'create', batch_id=batch_id,
                    data=data, key=self.headers.get('Idempotency-Key')))
            else:
                self.response(200, panel.annotate(match[1], data, actor))
        except batch_rpc.BatchRPCError as error:
            self.response(error.status, {'error':str(error)})
        except Conflict as error:
            self.response(409, {'error':str(error)})
        except KeyError:
            self.response(404, {'error':'Unknown record or write route'})
        except (ValueError, TypeError):
            self.response(400, {'error':'Invalid annotation request'})


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # Bound concurrent requests so evidence readers cannot exhaust memory.
    def __init__(self, address, panel):
        self.panel = panel
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(address, Handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        request.settimeout(10)
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    config = read_json(args.config)
    if not config.get('allowed_logins') or not config.get('origin','').startswith('https://'):
        parser.error('explicit owner logins and HTTPS origin are required')
    panel = Panel(config)
    threading.Thread(target=panel.index_loop, daemon=True).start()
    server = Server(('127.0.0.1', int(config['port'])), panel)
    try:
        server.serve_forever()
    finally:
        panel.stop.set()
        server.server_close()


if __name__ == '__main__':
    main()
