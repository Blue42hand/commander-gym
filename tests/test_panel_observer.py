import hashlib
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from commander_gym import panel_observer as observer
from commander_gym.batch_store import BatchStore
from commander_gym.control_panel import Panel


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.human={'id':'human','name':'Human play','scope':'All human-play sessions','unit':'human.service','socket':'unused','uid':os.getuid()}
        self.batch={'id':'batches','name':'Batch play','scope':'All managed sessions','unit':'native.service','socket':'unused','uid':os.getuid(),
                    'worker_units':['native.service','recorder.service','provider.service','fixture.service']}
    def native(self,**changes):
        return {'running':0,'starting':0,'observed_unix':time.time(),'recordingHealthy':True,'acceptingNewGames':True,
                'recoveryComplete':True,'pendingActivities':0,**changes}
    def test_status_uses_only_fixed_read_message_and_omits_extra_fields(self):
        path=self.root/'status.sock';requests=[]
        server=socket.socket(socket.AF_UNIX);server.bind(str(path));server.listen(1)
        def serve():
            with server.accept()[0] as client:
                requests.append(json.loads(client.makefile('rb').readline()))
                client.sendall(json.dumps({'protocol':1,'ok':True,'activeGames':3,'inFlightAdmissions':1,
                    'observedUnix':time.time(),'privateChoices':['MUST NOT LEAK'],'recordingHealthy':True}).encode()+b'\n')
        thread=threading.Thread(target=serve);thread.start()
        try:result=observer.status(path,os.getuid())
        finally:thread.join(3);server.close()
        self.assertEqual(requests,[{'protocol':1,'op':'status'}])
        self.assertEqual(result['running'],2);self.assertEqual(result['starting'],1)
        self.assertNotIn('MUST NOT LEAK',json.dumps(result))
    def test_active_service_does_not_prove_idle_when_source_fails(self):
        with patch.object(observer,'status',side_effect=OSError()):r=observer.runtime(self.human,{'human.service':'active'})
        self.assertIsNone(r['running']);self.assertFalse(r['complete']);self.assertEqual(r['state'],'Unavailable')
    def test_active_native_session_and_unhealthy_recorder_are_independent(self):
        with patch.object(observer,'status',return_value=self.native(running=2,recordingHealthy=False)):
            r=observer.runtime(self.human,{'human.service':'active'})
        self.assertEqual(r['running'],2);self.assertEqual(r['state'],'Running');self.assertEqual(r['recorder_state'],'Warning')
    def test_stopped_batch_runtime_needs_queue_and_broker_evidence_to_be_idle(self):
        states={u:'inactive' for u in self.batch['worker_units']}
        with patch.object(observer,'status',side_effect=OSError()):
            r=observer.runtime(self.batch,states,{'available':True,'active_count':0,'queued':0},{'available':True})
            self.assertEqual(r['state'],'Idle');self.assertEqual(r['running'],0)
            r=observer.runtime(self.batch,states,{'available':True,'active_count':1,'queued':0},{'available':True})
            self.assertIsNone(r['running'])
            r=observer.runtime(self.batch,states,{'available':True,'active_count':0,'queued':2},{'available':True})
            self.assertEqual(r['state'],'Queued')
    def test_database_inventory_finds_active_work_older_than_first_history_page(self):
        catalog=[{'id':'a','deck':{'label':'Approved deck','cards':{'Forest':99}}}]
        store=BatchStore(self.root/'batch-state',catalog,{'engine':'pinned'})
        plan={'name':'Older live batch','bindings':['a']*4,'game_count':1,'wall_seconds':30,'max_decisions':10,
              'budget_usd':1,'max_requests':10,'max_tokens':100000,'execution_mode':'fixture'}
        b=store.create(plan,'owner','old-draft-command')['batch']
        b=store.command(b['id'],'start',{'version':1,'fingerprint':b['fingerprint']},'owner','old-start-command')['batch']
        attempt=store.new_attempt(b['id']);attempt.update(state='Running',game_id='private-game',observation={
            'observed_unix':time.time(),'live_games':[{'gameSessionId':'private-game','turnNumber':7,'secret':'MUST NOT LEAK'}]})
        store.record_attempt(attempt)
        for n in range(35):store.create({**plan,'name':'New draft '+str(n)},'owner','newer-draft-key-'+str(n))
        before=hashlib.sha256(store.path.read_bytes()).hexdigest()
        def read_page(config,actor,op,**kwargs):
            self.assertEqual(op,'list')
            return {**store.list(offset=kwargs['data']['offset']),'health':{'supervisor':'Available','observed_unix':time.time()}}
        with patch('commander_gym.batch_rpc.call',side_effect=read_page):
            result,health=observer.batch_metadata({'control_socket':'fixture','actor':'owner'})
        self.assertTrue(result['metadata_complete']);self.assertTrue(health['available'])
        self.assertEqual(result['active_count'],1);self.assertEqual(result['active'][0]['game_id'],'private-game')
        self.assertEqual(result['active'][0]['turn'],7);self.assertNotIn('MUST NOT LEAK',json.dumps(result))
        self.assertEqual(hashlib.sha256(store.path.read_bytes()).hexdigest(),before)
    def test_changing_read_inventory_reports_unknown_queue_instead_of_zero(self):
        call_count=0
        def page(*args,**kwargs):
            nonlocal call_count
            call_count+=1
            return {'total':call_count+10,'batches':[], 'health':{'supervisor':'Available','observed_unix':time.time()}}
        with patch('commander_gym.batch_rpc.call',side_effect=page):
            result,_=observer.batch_metadata({'control_socket':'fixture','actor':'owner'})
        self.assertFalse(result['metadata_complete']);self.assertIsNone(result['queued']);self.assertIsNone(result['active_count'])
        self.assertLessEqual(call_count,8)

    def panel(self,snapshot):
        captures=self.root/'captures';captures.mkdir(exist_ok=True)
        path=self.root/'live.json';path.write_text(json.dumps(snapshot))
        return Panel({'capture_roots':[str(captures)],'state_directory':str(self.root/'panel'), 'live_snapshot':str(path)})
    def snapshot(self,**changes):
        stamp=time.time()
        return {'schema_version':1,'observed_unix':stamp,'runner_state':'Idle','scope':'Two configured runtimes',
                'running':0,'running_known':0,'coverage_complete':True,
                'runtimes':[{'id':n,'available':True,'complete':True,'running':0,'observed_unix':stamp,'recorder_state':'Healthy'} for n in ('human','batches')],
                'batch':{'available':True,'active':[],'issues':[],'recent_totals':{'completed':0,'failed':1},'queued':0},**changes}
    def test_fresh_then_stale_snapshot_cannot_keep_reporting_idle_or_zero(self):
        data=self.snapshot();panel=self.panel(data)
        self.assertEqual(panel.overview()['counts']['running'],0)
        data['observed_unix']=time.time()-30
        panel=self.panel(data);live=panel.live_snapshot()
        self.assertIsNone(live['running']);self.assertEqual(live['runner_state'],'Stale observation')
        self.assertFalse(live['batch']['available'])
    def test_partial_runtime_coverage_retains_known_count_without_complete_total(self):
        data=self.snapshot(running=2,runner_state='Running')
        data['runtimes'][0]['running']=2;data['runtimes'][1].update(available=False,complete=False,running=None)
        panel=self.panel(data);live=panel.live_snapshot()
        self.assertIsNone(live['running']);self.assertEqual(live['running_known'],2)
    def test_supervisor_failures_appear_without_inventing_native_outcomes(self):
        panel=self.panel(self.snapshot());overview=panel.overview()
        self.assertEqual(overview['counts']['failed'],1);self.assertEqual(overview['counts']['completed'],0)
        self.assertEqual(overview['recent'],[])

if __name__=='__main__':unittest.main()
