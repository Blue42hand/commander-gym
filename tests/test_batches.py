import concurrent.futures
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from commander_gym.batch_store import BatchStore, BatchConflict
from commander_gym.batch_budget import TokenFence, ChoiceGuard, atomic_json
from commander_gym.batch_supervisor import Supervisor, UNITS


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.catalog=[{'id':'approved','deck':{'label':'Test deck','cards':{'Forest':99}},'displayName':'Test'}]
        self.store=BatchStore(self.root,self.catalog,{'engine':'exact-engine','gym':'exact-gym'})
        self.plan={'name':'Investigation','bindings':['approved']*4,'game_count':2,'wall_seconds':30,'max_decisions':100,
                   'budget_usd':1,'max_requests':20,'max_tokens':100000,'failure_policy':'pause','execution_mode':'fixture'}
        self.actor='owner';self.key=0
    def command(self,op,batch=None,**data):
        self.key+=1
        return self.store.command(batch['id'] if batch else None,op,data,self.actor,'test-command-key-'+str(self.key))['batch']
    def draft(self):return self.command('create',plan=self.plan)
    def start(self,b):return self.command('start',b,version=b['version'],fingerprint=b['fingerprint'])
    def test_no_work_before_explicit_start_and_fingerprint(self):
        b=self.draft();self.assertEqual(b['state'],'Draft')
        with self.assertRaises(BatchConflict):self.store.new_attempt(b['id'])
        with self.assertRaises(BatchConflict):self.command('start',b,version=1,fingerprint='wrong')
        b=self.start(b);self.assertEqual(b['state'],'Queued')
    def test_concurrent_start_and_stale_tab(self):
        b=self.draft()
        def start(n):
            try:return self.store.command(b['id'],'start',{'version':1,'fingerprint':b['fingerprint']},self.actor,'concurrent-start-'+str(n))
            except BatchConflict:return None
        with concurrent.futures.ThreadPoolExecutor(2) as pool:results=list(pool.map(start,range(2)))
        self.assertEqual(sum(r is not None for r in results),1)
    def test_idempotent_create_and_changed_key_payload(self):
        raw={'plan':self.plan};key='stable-browser-key'
        a=self.store.command(None,'create',raw,self.actor,key)
        b=self.store.command(None,'create',raw,self.actor,key)
        self.assertEqual(a,b);self.assertEqual(self.store.list()['total'],1)
        with self.assertRaises(BatchConflict):self.store.command(None,'create',{'plan':{**self.plan,'name':'Other'}},self.actor,key)
    def test_pause_before_and_during_attempt(self):
        b=self.start(self.draft());b=self.command('pause',b,version=b['version'])
        self.assertEqual(b['state'],'Paused')
        with self.assertRaises(BatchConflict):self.store.new_attempt(b['id'])
        b=self.command('resume',b,version=b['version']);a=self.store.new_attempt(b['id']);b=self.store.get(b['id'])
        b=self.command('pause',b,version=b['version']);self.assertEqual(b['state'],'Draining');self.assertEqual(a['state'],'Starting')
    def test_restart_preserves_uncertain_create_and_requires_reconciliation(self):
        b=self.start(self.draft());a=self.store.new_attempt(b['id']);a['create_intent_unix']=time.time();self.store.record_attempt(a)
        reopened=BatchStore(self.root,self.catalog,self.store.pins);reopened.recover();b=reopened.get(b['id'])
        self.assertEqual(b['state'],'Needs reconciliation');self.assertEqual(b['attempts'][0]['state'],'Needs reconciliation')
        with self.assertRaises(BatchConflict):reopened.new_attempt(b['id'])
    def test_retry_is_linked_new_draft(self):
        b=self.start(self.draft());a=self.store.new_attempt(b['id']);a['state']='Interrupted';self.store.record_attempt(a)
        b=self.store.get(b['id']);retry=self.command('retry',b,version=b['version'],attempt_id=a['id'])
        self.assertEqual(retry['state'],'Draft');self.assertEqual(retry['parent_attempt_id'],a['id']);self.assertNotEqual(retry['id'],b['id'])
        self.assertEqual(self.store.get(b['id'])['attempts'][0]['state'],'Interrupted')
    def test_unknown_bindings_unbounded_values_and_injected_paths_rejected(self):
        for changes in [{'bindings':['/etc/passwd']*4},{'game_count':9999},{'budget_usd':float('nan')},{'command':'shutdown'},{'wall_seconds':True}]:
            with self.subTest(changes=changes),self.assertRaises(ValueError):self.store.validate({**self.plan,**changes})
    def test_unknown_usage_retains_token_reservation_across_restart(self):
        path=self.root/'tokens.json';atomic_json(path,{'limit':10000,'reserved_or_used':0,'reported_tokens':0,'unsettled':0})
        class Client:
            @property
            def responses(self):return self
            def create(self,**request):raise TimeoutError()
        with self.assertRaises(TimeoutError):TokenFence(Client(),path,10000).create(input='masked')
        data=json.loads(path.read_text());self.assertGreater(data['reserved_or_used'],6000);self.assertEqual(data['unsettled'],1)
        with self.assertRaises(ValueError):TokenFence(Client(),path,10000).create(input='masked')
        self.assertEqual(json.loads(path.read_text()),data)
    def test_settled_tokens_and_missing_ledger_fail_closed(self):
        path=self.root/'tokens.json';atomic_json(path,{'limit':10000,'reserved_or_used':0,'reported_tokens':0,'unsettled':0})
        client=SimpleNamespace(responses=SimpleNamespace(create=lambda **_:SimpleNamespace(usage=SimpleNamespace(total_tokens=17))))
        TokenFence(client,path,10000).create(input='masked');self.assertEqual(json.loads(path.read_text())['reserved_or_used'],17)
        path.unlink()
        with self.assertRaises(FileNotFoundError):TokenFence(client,path,10000).create(input='masked')
    def test_decision_limit_is_shared_across_seats(self):
        pilot=SimpleNamespace(name='fixture',version='1',choose=lambda _: 'legal-choice')
        guard=ChoiceGuard(1,time.time()+60,self.root/'progress.json')
        a,b=guard.wrap(pilot),guard.wrap(pilot)
        self.assertEqual(a.choose(None),'legal-choice')
        with self.assertRaises(ValueError):b.choose(None)
        self.assertEqual(json.loads((self.root/'progress.json').read_text())['state'],'Limit reached')
    def test_zero_usage_concurrent_settlement_never_invalidates_ledger(self):
        from commander_gym.openai_run_budget import OpenAIRunBudget
        budget=OpenAIRunBudget(self.root/'openai.json',1,max_requests=100,initialize_new_ledger=True)
        budget.snapshot()
        import threading
        barrier=threading.Barrier(4)
        def provider(**request):
            barrier.wait(timeout=5)
            return SimpleNamespace(usage=SimpleNamespace(input_tokens=0,output_tokens=0))
        def call(n):
            return budget.create(provider,{'model':'gpt-6-luna','max_output_tokens':2048,'input':'masked'*(n+1)})
        with concurrent.futures.ThreadPoolExecutor(4) as pool:list(pool.map(call,range(4)))
        self.assertGreaterEqual(budget.snapshot()['estimatedUsd'],0)
        self.assertEqual(budget.snapshot()['unsettledRequests'],0)

    def test_finish_uses_latest_pause_and_no_empty_resume(self):
        b=self.start(self.draft());attempt=self.store.new_attempt(b['id'])
        b=self.store.get(b['id']);self.command('pause',b,version=b['version'])
        attempt.update(state='Completed',message='Native terminal observed')
        self.store.finish_attempt(attempt)
        self.assertEqual(self.store.get(b['id'])['state'],'Paused')
        b=self.store.get(b['id']);b=self.command('resume',b,version=b['version']);a=self.store.new_attempt(b['id'])
        a.update(state='Failed',message='Explicit worker failure');self.store.finish_attempt(a)
        b=self.store.get(b['id']);self.assertEqual(b['state'],'Failed')
        with self.assertRaises(BatchConflict):self.command('resume',b,version=b['version'])

    def test_supervisor_cannot_address_human_units(self):
        supervisor=object.__new__(Supervisor)
        with self.assertRaises(ValueError):supervisor.unit('stop','argentum-play.service')
        self.assertTrue(all(u.startswith('commander-gym-batch-') for u in UNITS))

if __name__=='__main__':unittest.main()
