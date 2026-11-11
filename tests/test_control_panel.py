from __future__ import annotations
import hashlib
import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from commander_gym.control_panel import Panel, Server, Conflict, sanitized
from commander_gym.game_journal import PrivateGameJournal, PIN_KEYS
from commander_gym.record_codec import encode_record
TRANSITION = {'game_id':'g','native_schema':'synthetic-v1','action':{'type':'PassPriority'},'events':[],
              'result':{'status':'applied'},'before_state_digest':'before','after_state_digest':'after'}


class ControlPanelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / 'captures'
        self.root.mkdir(mode=0o700)
        self.config = {'capture_roots':[str(self.root)], 'state_directory':str(self.base/'state'),
                       'allowed_logins':['owner@example.test'], 'origin':'https://panel.example.test',
                       'port':8787, 'base_path':'/gym'}
        self.panel = Panel(self.config)
        self.server = None

    def tearDown(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        self.temp.cleanup()

    def fixture(self, name='completed', outcome='native_terminal', closed=True, compress=True, winner='a', draw=False):
        pins = {k:None for k in PIN_KEYS}
        pins.update(engine='a'*40, gym='b'*40, decks={'players':[{'playerId':'a','name':'Alice','commanderCardName':'Lathril','deck':{}}]})
        directory = self.root/name
        with PrivateGameJournal(directory, 'run-'+name, pins, game_id='game-'+name, compress_records=compress) as writer:
            writer.append('native_transition', {**TRANSITION,'game_id':'game-'+name,'message':'<img src=x onerror="alert(1)">','padding':'test '*1200}, source='native', source_sequence=0)
            if closed:
                writer.finish({'kind':outcome, 'winner_id':winner, 'draw':draw},expected_sources={'native':1},gaps=[])
        return directory

    def indexed(self):
        self.panel.index_once()
        return self.panel.history({'limit':['100']})['records']

    def note(self, record, **kw):
        return dict(version=record['review']['version'], state='Investigating',tags=['Incomplete evidence'],
                    note='Selected event deserves inspection',anchor='event:1',next_action='Compare native result',
                    source_revision=record['source_revision'], **kw)

    def request(self, path, method='GET', data=None, headers=None):
        if not self.server:
            self.server=Server(('127.0.0.1',0), self.panel)
            threading.Thread(target=self.server.serve_forever,daemon=True).start()
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_address[1],timeout=5)
        conn.request(method,path,body=json.dumps(data) if data else None,headers=headers or {})
        response=conn.getresponse();raw=response.read();code=response.status
        conn.close()
        return code,json.loads(raw) if raw.startswith(b'{') else raw

    def test_finalized_and_unsealed_and_malformed_remain_visible(self):
        self.fixture()
        self.fixture('open',closed=False)
        directory=self.root/'broken';directory.mkdir(mode=0o700)
        (directory/'manifest.json').write_text('{invalid')
        records=self.indexed()
        self.assertEqual(len(records),3)
        self.assertTrue(any(r['verification']=='verified' for r in records))
        self.assertTrue(any(r['verification']=='unsealed' for r in records))
        malformed=next(r for r in records if not r.get('run_id'))
        self.assertTrue(malformed['ref'].startswith('capture-'))
        self.assertEqual(malformed['lifecycle'],'Unknown')
        self.assertEqual(self.panel.coverage['state'],'complete')

    def test_readable_action_routes_are_authorized_bounded_and_plaintext(self):
        self.fixture();record=self.indexed()[0];ref=record['ref']
        for suffix in ('actions','record'):
            self.assertEqual(self.request('/gym/api/games/'+ref+'/'+suffix)[0],403)
        headers={'Tailscale-User-Login':'owner@example.test'}
        code,page=self.request('/gym/api/games/'+ref+'/actions?routine=1',headers=headers)
        self.assertEqual(code,200);self.assertLessEqual(len(page['actions']),50)
        self.assertTrue(page['caught_up']);self.assertEqual(page['actions'][0]['anchor'],'event:1')
        code,body=self.request('/gym/api/games/'+ref+'/record',headers=headers)
        self.assertEqual(code,200);self.assertIn(b'Commander Gym',body);self.assertIn(b'event:1',body)
        self.assertNotIn(b'<img',body)
        self.assertEqual(self.request('/gym/actions.js',headers=headers)[0],200)
        self.assertEqual(self.request('/gym/api/games/'+ref+'/actions?before=bad',headers=headers)[0],400)

    def test_interrupted_game_wall_time_uses_batch_timestamps_without_seal(self):
        self.fixture(closed=False);r=self.indexed()[0]
        batch='batch-'+'a'*32;attempt='attempt-'+'b'*32
        r['batch_ref']={'batch_id':batch,'attempt_id':attempt}
        self.panel.records[r['ref']]=r
        self.panel.locations[r['ref']]=self.root/attempt/r['game_id']
        self.panel.config['batch_socket']='/test-only'
        value={'attempts':[{'id':attempt,'game_id':r['game_id'],'started_unix':1000,'ended_unix':1461.2,'state':'Interrupted'}]}
        with patch('commander_gym.batch_rpc.call',return_value=value) as rpc:
            result=self.panel.history({},'owner@example.test')['records'][0]
            self.assertAlmostEqual(result['wall_time'],461.2)
            self.assertIsNone(result['duration']);self.assertEqual(result['wall_time_state'],'final')
            self.assertEqual(result['lifecycle'],'Unknown')
            self.panel.history({},'owner@example.test');self.assertEqual(rpc.call_count,1)
        with patch('commander_gym.batch_rpc.call',return_value=value) as rpc:
            self.panel.locations[r['ref']]=self.root/'different-attempt'/r['game_id']
            self.assertIsNone(self.panel.history({},'owner@example.test')['records'][0]['wall_time'])
            rpc.assert_not_called()

    def test_bad_game_timestamps_never_become_zero_wall_time(self):
        r={'ref':'r','game_id':'g','duration':None,'batch_ref':{'batch_id':'batch-'+'a'*32,'attempt_id':'attempt-'+'b'*32}}
        self.panel.locations['r']=self.root/r['batch_ref']['attempt_id']/'g';self.panel.config['batch_socket']='/test'
        for start,end in ((None,1200),(2000,1000),(1000,float('nan')),(True,1200)):
            self.panel.batch_timing_cache.clear()
            with patch('commander_gym.batch_rpc.call',return_value={'attempts':[{'id':r['batch_ref']['attempt_id'],
                'game_id':'g','started_unix':start,'ended_unix':end,'state':'Interrupted'}]}):
                self.assertIsNone(self.panel.attach_wall_time(dict(r),'owner@example.test')['wall_time'])

    def test_batch_reference_survives_old_cache_and_fresh_live_elapsed_is_qualified(self):
        from commander_gym.panel_worker import metadata
        batch='batch-'+'a'*32;attempt='attempt-'+'b'*32
        result=metadata({'payload':{'pins':{'config':{'gym':{'batchId':batch,'attemptId':attempt}}}}})
        self.assertEqual(result['batch_ref'],{'batch_id':batch,'attempt_id':attempt})
        r={'ref':'r','game_id':'g','duration':None,'batch_ref':result['batch_ref']}
        self.panel.locations['r']=self.root/attempt/'g';self.panel.config['batch_socket']='/test'
        with patch('commander_gym.batch_rpc.call',return_value={'attempts':[{'id':attempt,'game_id':'g','started_unix':time.time()-30,'state':'Running'}]}):
            with patch.object(self.panel,'live_snapshot',return_value={'fresh':True,'batch':{'active':[{'id':attempt,'game_id':'g','native_observed_unix':time.time()}]}}):
                value=self.panel.attach_wall_time(dict(r),'owner@example.test')
                self.assertAlmostEqual(value['wall_time'],30,delta=1);self.assertEqual(value['wall_time_state'],'live')
            with patch.object(self.panel,'live_snapshot',return_value={'fresh':False}):
                self.assertIsNone(self.panel.attach_wall_time(dict(r),'owner@example.test')['wall_time'])

    def test_completed_failed_interrupted_and_unknown_result_separate(self):
        self.fixture('a','native_terminal');self.fixture('b','failed');self.fixture('c','session_closed')
        records=self.indexed()
        self.assertEqual({r['lifecycle'] for r in records},{'Completed','Failed','Interrupted'})
        for r in records:
            self.assertFalse(r['recording_complete'])
            self.assertTrue(r['ready_for_analysis'])
            self.assertEqual(r['review']['state'],'Unreviewed')


    def test_persistence_change_history_and_optimistic_conflict(self):
        self.fixture();r=self.indexed()[0]
        saved=self.panel.annotate(r['ref'],self.note(r),'owner@example.test')
        self.assertEqual(saved['version'],1)
        with self.assertRaises(Conflict):self.panel.annotate(r['ref'],self.note(r),'owner@example.test')
        reopened=Panel(self.config)
        self.assertEqual(reopened.review(r['ref']),saved)
        self.assertEqual(reopened.changes(r['ref'])[0],saved)
        self.assertEqual(saved['artifact_hash'],r['artifact_hash'])

    def test_changed_source_requires_review_and_rejects_stale_source_save(self):
        directory=self.fixture(closed=False);r=self.indexed()[0]
        self.panel.annotate(r['ref'],{**self.note(r),'state':'Reviewed'},'owner@example.test')
        (directory/'new-diagnostic.txt').write_text('additional evidence')
        with self.assertRaises(Conflict):self.panel.annotate(r['ref'],{**self.note(r),'version':1},'owner@example.test')
        self.panel.index_once()
        changed=self.panel.get(r['ref'])
        self.assertTrue(changed['new_evidence'])
        self.assertTrue(self.panel.needs_inspection(changed))
        with self.assertRaises(Conflict):self.panel.annotate(r['ref'],{**self.note(r),'version':1},'owner@example.test')

    def test_source_bytes_metadata_and_writer_lock_are_unchanged(self):
        directory=self.fixture()
        inventory=lambda:{str(p.relative_to(directory)):(p.stat().st_size,p.stat().st_mtime_ns,p.stat().st_mode,hashlib.sha256(p.read_bytes()).hexdigest()) for p in directory.iterdir() if p.is_file()}
        before=inventory()
        with patch('commander_gym.game_journal.PrivateGameJournal.__init__',side_effect=AssertionError('writer must not be instantiated')):
            r=self.indexed()[0];self.panel.evidence(r['ref']);self.panel.annotate(r['ref'],self.note(r),'owner@example.test')
        self.assertEqual(before,inventory())

    def test_compressed_and_plain_evidence_and_escaping_boundary(self):
        for compress in (False,True):
            self.fixture(str(compress),compress=compress)
        for r in self.indexed():
            result=self.panel.evidence(r['ref'])
            self.assertEqual(len(result['events']),3)
            self.assertIn('<img',result['events'][1]['excerpt'])
            # JSON response is inert; the browser must escape before HTML interpolation.
            frontend=Path('commander_gym/panel_static/app.js').read_text()
            self.assertIn('esc(e.excerpt)',frontend)
            self.assertEqual(result['verification'],'verified')

    def test_large_record_is_bounded_and_diagnostic(self):
        directory=self.root/'large';directory.mkdir(mode=0o700)
        (directory/'000000.jsonl').write_bytes(b'x'*(2*1024*1024+10)+b'\n')
        (directory/'000000.jsonl').chmod(0o600)
        r=self.indexed()[0]
        self.assertEqual(r['verification'],'too-large')
        evidence=self.panel.evidence(r['ref'])
        self.assertIn('2 MiB',evidence['diagnostic'])
        self.assertEqual(evidence['events'],[])

    def test_compressed_oversized_and_unsupported_frames_are_distinct(self):
        for name, data in [('oversized', encode_record(b'{"data":"'+b'x'*(3*1024*1024)+b'"}\n')),
                           ('unsupported', b'{"recordCodec":9,"encoding":"unknown","decodedBytes":10,"data":"bad"}\n')]:
            directory=self.root/name;directory.mkdir(mode=0o700)
            (directory/'000000.jsonl').write_bytes(data)
            (directory/'000000.jsonl').chmod(0o600)
        records=self.indexed()
        self.assertEqual({r['verification'] for r in records},{'too-large','damaged'})

    def test_pagination_cursors_signed_and_evidence_scope_bounded(self):
        pins={k:None for k in PIN_KEYS}
        with PrivateGameJournal(self.root/'many','run',pins,game_id='game') as w:
            for i in range(45):w.append('native_transition',{**TRANSITION,'game_id':'game','text':'event'},source='native',source_sequence=i)
        r=self.indexed()[0]
        first=self.panel.evidence(r['ref']);self.assertEqual(len(first['events']),20)
        second=self.panel.evidence(r['ref'],first['next_cursor'])
        self.assertEqual(second['events'][0]['sequence'],20)
        with self.assertRaises(ValueError):self.panel.evidence(r['ref'],'../../etc/passwd')

    def test_filtering_and_review_disposition(self):
        self.fixture();r=self.indexed()[0]
        self.assertEqual(self.panel.history({'q':['lathril']})['total'],1)
        self.assertEqual(self.panel.history({'q':['nonexistent']})['total'],0)
        self.assertEqual(self.panel.history({'lifecycle':['Failed']})['total'],0)
        saved=self.panel.annotate(r['ref'],{**self.note(r),'state':'Reviewed'},'owner@example.test')
        self.assertFalse(self.panel.needs_inspection(self.panel.get(r['ref'])))
        self.panel.annotate(r['ref'],{**self.note(r),'state':'Needs follow-up','version':saved['version']},'owner@example.test')
        self.assertTrue(self.panel.needs_inspection(self.panel.get(r['ref'])))

    def test_missing_and_unrecognized_identities_denied_for_all_reads(self):
        for path in ('/gym/','/gym/api/overview','/gym/api/history','/gym/api/games/capture-123/evidence','/gym/app.js'):
            self.assertEqual(self.request(path)[0],403)
            self.assertEqual(self.request(path,headers={'Tailscale-User-Login':'other@example.test'})[0],403)
        self.assertEqual(self.request('/gym/api/session',headers={'Tailscale-User-Login':'owner@example.test'})[0],200)

    def test_csrf_and_same_origin_write_required(self):
        self.fixture();r=self.indexed()[0];headers={'Tailscale-User-Login':'owner@example.test','Content-Type':'application/json'}
        path='/gym/api/games/'+r['ref']+'/review'
        self.assertEqual(self.request(path,'POST',self.note(r),headers)[0],403)
        session=self.request('/gym/api/session',headers=headers)[1]
        headers.update({'X-CSRF-Token':session['csrf'],'Origin':'https://evil.example.test'})
        self.assertEqual(self.request(path,'POST',self.note(r),headers)[0],403)
        headers['Origin']=self.config['origin']
        self.assertEqual(self.request(path,'POST',self.note(r),headers)[0],200)
        self.assertEqual(self.request(path,'POST',self.note(r),headers)[0],409)

    def test_path_traversal_and_symlink_artifacts_rejected(self):
        directory=self.fixture();r=self.indexed()[0]
        for name in ('../../etc/passwd','/etc/passwd','../manifest.json'):
            with self.assertRaises(KeyError):self.panel.artifact(r['ref'],name)
        (directory/'escape').symlink_to('/etc/passwd')
        self.panel.index_once()
        self.assertEqual(self.panel.get(r['ref'])['source_revision'],'unreadable')
        headers={'Tailscale-User-Login':'owner@example.test'}
        self.assertEqual(self.request('/gym/%2e%2e/etc/passwd',headers=headers)[0],404)

    def test_health_fresh_stale_and_unavailable_do_not_infer_game_state(self):
        health=self.base/'health.json'
        self.panel.config['health_snapshots']={'native':str(health)}
        health.write_text(json.dumps({'observedUnix':time.time(),'recordingHealthy':True}))
        self.assertEqual(self.panel.health()['recorder_state'],'Healthy')
        self.assertIsNone(self.panel.health()['running'])
        self.assertEqual(self.panel.health()['runner_state'],'Unknown')
        health.write_text(json.dumps({'observedUnix':time.time()-3600,'recordingHealthy':True}))
        self.assertEqual(self.panel.health()['recorder_state'],'Stale observation')
        health.unlink()
        self.assertEqual(self.panel.health()['recorder_state'],'Unavailable')

    def test_secret_fields_and_transport_text_excluded(self):
        value=sanitized({'headers':{'Authorization':'bad'},'env':{'password':'oops'},'text':'Bearer abc123 token API_KEY=secretvalue'})
        self.assertNotIn('bad',json.dumps(value));self.assertNotIn('oops',json.dumps(value))
        self.assertNotIn('abc123',value['text']);self.assertNotIn('secretvalue',value['text'])

    def test_unknown_winner_is_not_a_draw_without_explicit_evidence(self):
        self.fixture('unknown-winner', winner=None)
        self.fixture('explicit-draw', winner=None, draw=True)
        records=self.indexed()
        self.assertEqual({r['result'] for r in records}, {'Unknown','Draw'})

    def test_verification_budget_and_baseline_coverage_do_not_create_urgent_incidents(self):
        record={'review':{'state':'Unreviewed','tags':[]},'lifecycle':'Completed',
                'verification':'budget-limited','coverage':['canonical_training_evidence_unavailable'],
                'issues':['Inspection time budget reached; verification incomplete']}
        self.assertFalse(self.panel.needs_inspection(record))
        record['issues'].append('Missing finalized manifest; outcome is not confirmed')
        self.assertTrue(self.panel.needs_inspection(record))

    def test_grouped_batch_captures_are_visible_and_do_not_follow_symlinks(self):
        grouped=self.base/'batch-captures';grouped.mkdir(mode=0o700)
        attempt=grouped/'attempt-a';attempt.mkdir(mode=0o700)
        original=self.fixture('batch-game')
        original.rename(attempt/'game-a')
        (grouped/'unsafe').symlink_to(self.root)
        self.panel=Panel({**self.config,'grouped_capture_roots':[str(grouped)]})
        records=self.indexed()
        self.assertEqual(len(records),1)
        self.assertEqual(records[0]['game_id'],'game-batch-game')

    def test_batch_routes_require_owner_csrf_and_use_narrow_rpc(self):
        code,_=self.request('/gym/api/batches');self.assertEqual(code,403)
        headers={'Tailscale-User-Login':'owner@example.test'}
        code,_=self.request('/gym/api/batches','POST',{'plan':{}},headers);self.assertEqual(code,403)
        code,session=self.request('/gym/api/session',headers=headers)
        headers.update({'Origin':self.config['origin'],'X-CSRF-Token':session['csrf'],'Content-Type':'application/json','Idempotency-Key':'stable-test-command'})
        with patch('commander_gym.batch_rpc.call',return_value={'accepted':True}) as rpc:
            code,result=self.request('/gym/api/batches','POST',{'plan':{'name':'draft'}},headers)
            self.assertEqual(code,200);self.assertTrue(result['accepted'])
            self.assertEqual(rpc.call_args.args[2],'create')
            self.assertEqual(rpc.call_args.kwargs['key'],'stable-test-command')
        code,_=self.request('/gym/api/batches/../../stop','POST',{},headers);self.assertEqual(code,404)

    def test_state_inside_capture_root_rejected(self):
        with self.assertRaises(ValueError):Panel({**self.config,'state_directory':str(self.root/'notes')})


if __name__=='__main__':unittest.main()
