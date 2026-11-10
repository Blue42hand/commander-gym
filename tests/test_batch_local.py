import concurrent.futures
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from commander_gym.batch_budget import atomic_json, BatchLimitError
from commander_gym.batch_local import LocalBatchTransport
from commander_gym.batch_store import BatchStore
from commander_gym.batch_supervisor import provider_unit
from commander_gym.local_catalog import prepare_local_catalog
from commander_gym.game_server_binding_lmstudio_sidecar import LocalBindingConfig, build_binding_lmstudio_sidecar
from commander_gym.game_server_sidecar import GameServerSidecarConfig
from commander_gym.lmstudio_provider import LMStudioError
from test_game_server_binding_openai_sidecar import synthetic_catalog, free_port
from test_lmstudio import config, FakeTransport, completion


class LocalBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plan = {'name':'Local pod','bindings':['local']*4,'execution_mode':'local',
                     'budget_usd':0,'max_requests':2,'max_tokens':100000,
                     'game_count':1,'wall_seconds':30,'max_decisions':100}
        atomic_json(self.root/'local.json', {'requests':0,'input_tokens':0,'output_tokens':0,'unsettled_requests':0})
        atomic_json(self.root/'tokens.json', {'limit':100000,'reserved_or_used':0,'reported_tokens':0,'unsettled':0})

    def transport(self, *responses, **kwargs):
        return LocalBatchTransport(config(), self.root, self.plan, time.time()+30,
                                   transport=FakeTransport(*responses), **kwargs)

    def test_local_bindings_cannot_be_selected_for_paid_games_or_reverse(self):
        catalog = [{'id':'cloud'}, {'id':'local','execution_modes':['local']}]
        store = BatchStore(self.root/'store', catalog, {'local_inference':asdict(config())})
        self.assertEqual(store.validate(self.plan)['budget_usd'], 0)
        for changes in ({'execution_mode':'paid','budget_usd':1}, {'bindings':['cloud']*4}, {'budget_usd':1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                store.validate({**self.plan, **changes})
        self.assertEqual(provider_unit('local'), 'commander-gym-batch-local.service')

    def test_usage_and_requests_are_shared_across_seats_and_restarts(self):
        response = completion(usage={'prompt_tokens':10,'completion_tokens':2,'total_tokens':12})
        self.transport(response).request('/v1/chat/completions', {}, timeout=2)
        self.transport(response).request('/v1/chat/completions', {}, timeout=2)
        with self.assertRaisesRegex(BatchLimitError,'batch_request_limit'):
            self.transport(response).request('/v1/chat/completions', {}, timeout=2)
        self.assertEqual(json.loads((self.root/'tokens.json').read_text())['reported_tokens'],24)
        self.assertEqual(json.loads((self.root/'local.json').read_text())['input_tokens'],20)

    def test_concurrent_admission_is_bounded(self):
        self.plan['max_requests']=1
        def call(_):
            try:
                self.transport().request('/v1/chat/completions', {}, timeout=2)
                return True
            except BatchLimitError:return False
        with concurrent.futures.ThreadPoolExecutor(4) as pool:
            self.assertEqual(sum(pool.map(call, range(4))),1)

    def test_unknown_usage_and_failure_keep_reservations(self):
        for response in (completion(), LMStudioError('cancelled')):
            transport=self.transport(response)
            try:transport.request('/v1/chat/completions', {}, timeout=2)
            except LMStudioError:pass
        tokens=json.loads((self.root/'tokens.json').read_text())
        self.assertEqual(tokens['unsettled'],2)
        self.assertGreater(tokens['reserved_or_used'],0)

    def test_token_and_deadline_fences_reject_before_dispatch(self):
        transport=self.transport()
        transport.deadline=time.time()-1
        with self.assertRaisesRegex(BatchLimitError,'batch_time_limit'):
            transport.request('/v1/chat/completions', {}, timeout=2)
        self.plan['max_tokens']=1
        atomic_json(self.root/'tokens.json', {'limit':1,'reserved_or_used':0,'reported_tokens':0,'unsettled':0})
        transport=self.transport()
        with self.assertRaisesRegex(BatchLimitError,'batch_token_fence'):
            transport.request('/v1/chat/completions', {}, timeout=2)
        self.assertEqual(transport.transport.calls,[])

    def test_supervisor_prepares_local_recording_and_ledgers_without_cloud_budget(self):
        from commander_gym.batch_supervisor import Supervisor
        inference=asdict(config())
        pins={'engine':'engine','gym':'gym','local_inference':inference,'bindings':{'local':'fingerprint'}}
        cfg={'state_directory':str(self.root/'state'),'runtime_directory':str(self.root/'runtime'),
             'catalog':[{'id':'local','execution_modes':['local']}], 'pins':pins,
             'engine_uid':os.getuid(),'engine_gid':os.getgid(),'provider_uid':os.getuid(),'provider_gid':os.getgid(),
             'local_inference':inference,'local_roster':'catalog.json','roster':'cloud.json',
             'verify_files':{},'recording_pins':{'bindings':{'cloud':'cloud-fingerprint'}},
             'native_port':8879,'provider_port':8880,'java':'java','python_packages':'packages'}
        supervisor=Supervisor(cfg)
        batch=supervisor.store.create(self.plan,'owner','local-draft-command')['batch']
        supervisor.store.command(batch['id'],'start',{'version':1,'fingerprint':batch['fingerprint']},'owner','local-start-command')
        attempt=supervisor.store.new_attempt(batch['id'])
        with patch.object(supervisor,'inactive',return_value=True):
            role=supervisor.prepare(batch,attempt)
        self.assertEqual(role['catalog_root'],'/local-catalog')
        self.assertEqual(role['recording_pins']['models'],inference)
        self.assertEqual(role['recording_pins']['bindings'],{'local':'fingerprint'})
        self.assertFalse(role['recording_pins']['config']['paidProvidersEnabled'])
        self.assertFalse((supervisor.root/'budgets'/batch['id']/'openai.json').exists())
        self.assertEqual(supervisor.budget_snapshot(batch)['estimated_usd'],0)
        supervisor.config['local_inference']={**inference,'model':'changed'}
        with patch.object(supervisor,'inactive',return_value=True),self.assertRaisesRegex(RuntimeError,'not pinned'):
            supervisor.prepare(batch,attempt)

    def test_prepared_catalog_preserves_decks_and_knowledge_and_runs_local_only(self):
        source=self.root/'source';source.mkdir()
        catalog=synthetic_catalog(source, with_knowledge=True)
        before={str(p.relative_to(source)):p.read_bytes() for p in source.rglob('*') if p.is_file()}
        output=self.root/'local'
        registry=prepare_local_catalog(catalog,source,output,config())
        self.assertTrue(all('-local-' in p['id'] for p in registry.profile_payloads()))
        for directory in output.rglob('*'):
            if directory.is_dir():self.assertEqual(directory.stat().st_mode & 0o022,0)
        for name, content in before.items():
            self.assertEqual((source/name).read_bytes(),content)
        from commander_gym.binding_catalog import load_binding_catalog
        from commander_gym.recorder_bridge import _RecordingPilot
        loaded=load_binding_catalog(output/'catalog.json',instance_root=output,
                                    pilot_factory=lambda p,_:_RecordingPilot(p))
        resolved=loaded.resolver.resolve(loaded.binding_ids[0])
        self.assertEqual(resolved.deck_knowledge_content,'Synthetic primer')
        self.assertEqual(resolved.exact_deck_payload['cards']['Forest'],99)
        with patch('commander_gym.game_server_binding_openai_sidecar._default_openai_client',side_effect=AssertionError('cloud client')):
            server=build_binding_lmstudio_sidecar(LocalBindingConfig(
                GameServerSidecarConfig('local',port=free_port()),config(),output/'catalog.json',output),transport=FakeTransport())
            server.server_close()
        with self.assertRaises(FileExistsError):prepare_local_catalog(catalog,source,output,config())
        changed=self.root/'changed'
        other=prepare_local_catalog(catalog,source,changed,replace(config(),timeout_seconds=31))
        self.assertNotEqual(registry.binding_fingerprints,other.binding_fingerprints)
