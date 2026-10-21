"""No-game local QA contract tests; real privileged peer fixture runs only in CI."""
import copy
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from commander_gym import manual_runtime_qa as qa, manual_runtime_qa_roles as roles
from commander_gym.lmstudio_provider import LMStudioError
from commander_gym.lmstudio_pilot import LMStudioPilot, LMStudioPilotError
from commander_gym.manual_runtime_profile import ManualRuntimeError, ValidatedRuntime, canonical, digest
from commander_gym.qa_local_provider import NativeLegalLocalTransport, LOCAL_TOKEN, LOCAL_PROBES, capture_pins
from commander_gym.qa_local_probes import observation, failure_cancellation, provider_isolation
from commander_gym.qa_redis_variant import role_uid
from test_manual_runtime_local import local_profile
from test_lmstudio import config


class LocalQATests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='.qa-local-', dir=Path.cwd()); self.addCleanup(self.tmp.cleanup)
        self.profile = local_profile(Path(self.tmp.name))
        self.runtime = ValidatedRuntime(canonical(self.profile), digest(self.profile), 2)

    def test_fake_transport_uses_actual_local_parser_and_native_validation_without_network(self):
        for compact in (False, True):
            for stream in (False, True):
                cfg = config(compact_observation=compact, stream=stream)
                transport = NativeLegalLocalTransport(cfg); obs = observation(); before = copy.deepcopy(obs)
                with (patch('commander_gym.lmstudio_provider.LMStudioTransport.request', side_effect=AssertionError('network')),
                      patch('commander_gym.game_server_binding_openai_sidecar._default_openai_client', side_effect=AssertionError('cloud'))):
                    choice = LMStudioPilot(cfg, transport=transport).choose(obs)
                self.assertEqual(choice.action_id, 0); self.assertEqual(obs, before)
                self.assertEqual(transport.calls, 2); self.assertEqual(choice.metadata['usage']['total_tokens'], 0)
                self.assertTrue(choice.metadata['modelIo']['attempts'][0]['response']['responseId'].startswith('qa-local-fixture-'))

    def test_fake_bounds_context_cancel_and_unsupported_offers_fail_closed(self):
        cfg = config(); t = NativeLegalLocalTransport(cfg)
        for timeout in (0, float('inf'), True):
            with self.assertRaisesRegex(LMStudioError, 'deadline_exceeded'): t.request('/api/v1/models', None, timeout=timeout)
        cancel = threading.Event(); cancel.set()
        with self.assertRaisesRegex(LMStudioError, 'cancelled'): t.request('/api/v1/models', None, timeout=1, cancel=cancel)
        t.calls = 256
        with self.assertRaisesRegex(LMStudioError, 'bound'): t.request('/api/v1/models', None, timeout=1)
        for mutation in (lambda o:o['legalActions'].clear(), lambda o:o['legalActions'][0].update(kind='PlayLand', actionType='PlayLand'),
                         lambda o:o.update(schemaHash='wrong')):
            obs=observation();mutation(obs)
            with self.assertRaises(LMStudioPilotError): LMStudioPilot(cfg, transport=NativeLegalLocalTransport(cfg)).choose(obs)
        with self.assertRaisesRegex(LMStudioError, 'unsupported_endpoint'): NativeLegalLocalTransport(cfg).request('/v1/responses', {}, timeout=1)

    def test_keep_and_bottom_fixture_choices_preserve_native_channels(self):
        cfg=config();obs=observation();obs['callbackKind']='decideMulligan'
        obs['legalActions'][0].update(kind='KeepHand',actionType='KeepHand',action={'type':'KeepHand'},semanticId='qa-keep')
        self.assertEqual(LMStudioPilot(cfg,transport=NativeLegalLocalTransport(cfg)).choose(obs).action_id,0)
        obs=observation();obs.update(callbackKind='chooseBottomCards',legalActions=[],pendingDecision={
            'kind':'BottomCards','requiresStructuredResponse':True,'decisionId':'only-native-routing',
            'cardsToPutOnBottom':2,'hand':['b','a','c'],'responseSpec':{
                'responseType':'CardsSelectedResponse','requiredFields':{'selectedCards':'ENTITY_ID_ARRAY'}}})
        result=LMStudioPilot(cfg,transport=NativeLegalLocalTransport(cfg)).choose(obs)
        self.assertEqual(result.response['selectedCards'],['a','b']);self.assertEqual(result.response['decisionId'],'only-native-routing')

    def test_role_order_closure_and_fixed_local_identity(self):
        plain=copy.deepcopy(self.profile);plain.pop('localInference')
        self.assertEqual(qa.roles_order(plain),qa.ROLES_ORDER)
        self.assertEqual(qa.roles_order(self.profile),('recorder','sidecar','local','native','proxy'))
        self.assertEqual(qa.role_closure(plain,self.profile),qa.roles_order(self.profile))
        self.assertEqual(qa.role_closure(self.profile,plain),qa.roles_order(self.profile))
        self.assertEqual(role_uid(self.profile,'local'),10004)
        with self.assertRaisesRegex(ManualRuntimeError,'qa_local_role_disabled'):role_uid(plain,'local')
        self.assertEqual(qa.probes_for(self.profile),(*qa.PROBES,*LOCAL_PROBES));self.assertEqual(qa.probes_for(plain),qa.PROBES)
        self.assertEqual(roles.child_groups('local',self.profile,10004,10002),[10004])
        self.assertEqual(roles.child_groups('recorder',self.profile,10001,10002),[10001,10002,10004])
        self.assertEqual(roles.child_groups('native',self.profile,10001,10002),[10001,10002])
        for args in [('local',10002,10002),('native',10004,10002)]:
            with self.assertRaises(ManualRuntimeError):roles.child_groups(args[0],self.profile,*args[1:])
        bad=copy.deepcopy(self.profile);bad['localInference']['gid']=10002
        with self.assertRaisesRegex(ManualRuntimeError,'qa_local_cloud_group'):roles.child_groups('local',bad,10002,10002)

    def message(self,role='local'):
        return dict(role=role,profile=self.profile,runtimeId=self.runtime.runtime_id,sequence=2,
                    importPaths=[],sidecarGid=10002,localGid=10004)

    def test_local_child_identity_and_environment_guard_before_dispatch(self):
        for uid,gid,groups,changes,env in [(10002,10004,[10004],{},{}),(10004,10002,[10004],{},{}),
                (10004,10004,[10004,10002],{},{}),(10004,10004,[10004],{'localGid':10002},{}),
                (10004,10004,[10004],{}, {'OPENAI_API_KEY':'fixture-only'})]:
            with self.subTest(uid=uid,gid=gid,groups=groups),patch.object(roles.os,'getuid',return_value=uid),patch.object(roles.os,'geteuid',return_value=uid),patch.object(roles.os,'getgid',return_value=gid),patch.object(roles.os,'getgroups',return_value=groups),patch.dict(os.environ,env,clear=True),patch('commander_gym.qa_local_provider.build_local_server') as build:
                with self.assertRaises(ManualRuntimeError):roles.run_child({**self.message(),**changes})
                build.assert_not_called()
        with patch.object(roles.os,'getuid',return_value=10004),patch.object(roles.os,'geteuid',return_value=10004),patch.object(roles.os,'getgid',return_value=10004),patch.object(roles.os,'getgroups',return_value=[10004]),patch.dict(os.environ,{},clear=True),patch.object(roles.signal,'signal'),patch('commander_gym.qa_local_provider.build_local_server') as build,patch.object(roles,'serve') as serve:
            roles.run_child(self.message());build.assert_called_once();serve.assert_called_once_with(build.return_value,unittest.mock.ANY,notify=True)
        message=self.message();message.pop('localGid')
        with self.assertRaisesRegex(ManualRuntimeError,'qa_role_message'):roles.run_child(message)

    def test_native_configtree_uses_distinct_literal_tokens_only_when_enabled(self):
        uid=self.profile['identities']['nativeUid']
        with patch.object(roles.os,'getuid',return_value=uid),patch.object(roles.os,'geteuid',return_value=uid),patch.dict(os.environ,{},clear=True),patch.object(roles.signal,'signal'),patch.object(roles,'_private_role_directory'),patch.object(Path,'iterdir',return_value=iter([])),patch.object(roles,'_literal_file') as literal,patch('commander_gym.manual_runtime_launch.native_spec') as native,patch('commander_gym.qa_local_provider.require_local_ready'),patch.object(roles.os,'execve'):
            native.return_value=__import__('commander_gym.manual_runtime_launch',fromlist=['LaunchSpec']).LaunchSpec(('java',),{})
            roles.run_child(self.message('native'))
        self.assertEqual([c.args[0].name for c in literal.call_args_list],['commander-gym.sidecar.token','commander-gym.local.token'])
        self.assertEqual([c.args[1] for c in literal.call_args_list],[roles.TOKEN.encode(),LOCAL_TOKEN.encode()]);self.assertNotEqual(roles.TOKEN,LOCAL_TOKEN)

    def test_native_refuses_unready_local_before_token_files_or_exec(self):
        uid=self.profile['identities']['nativeUid']
        with patch.object(roles.os,'getuid',return_value=uid),patch.object(roles.os,'geteuid',return_value=uid),patch.dict(os.environ,{},clear=True),patch.object(roles.signal,'signal'),patch('commander_gym.qa_local_provider.require_local_ready',side_effect=ManualRuntimeError('qa_local_not_ready')),patch.object(roles,'_literal_file') as literal,patch.object(roles.os,'execve') as execute:
            with self.assertRaisesRegex(ManualRuntimeError,'qa_local_not_ready'):roles.run_child(self.message('native'))
            literal.assert_not_called();execute.assert_not_called()

    def test_fake_capture_pins_keep_configured_identity_and_explicit_actual_execution(self):
        pins={'models':{'model':'qwen'},'config':{'sealed':'original'}}
        result=capture_pins(pins,self.profile)
        self.assertEqual(result['models']['configured'],pins['models']);self.assertEqual(result['config']['sealed'],'original')
        self.assertEqual(result['config']['qaExecution']['localProvider'],'fake-local-native-legal')
        self.assertNotIn('qaExecution',pins['config'])

    def test_only_exact_synthetic_stale_local_token_can_be_removed_on_disable(self):
        path=Path(self.tmp.name)/'stale-local.token';path.write_bytes(LOCAL_TOKEN.encode());path.chmod(0o600)
        roles._remove_literal_file(path,LOCAL_TOKEN.encode());self.assertFalse(path.exists())
        path.write_bytes(b'foreign-token');path.chmod(0o600)
        with self.assertRaisesRegex(ManualRuntimeError,'qa_role_stale_token'):roles._remove_literal_file(path,LOCAL_TOKEN.encode())
        self.assertEqual(path.read_bytes(),b'foreign-token')
        path.unlink();path.symlink_to(Path(self.tmp.name)/'outside')
        with self.assertRaisesRegex(ManualRuntimeError,'qa_role_stale_token'):roles._remove_literal_file(path,LOCAL_TOKEN.encode())
        self.assertTrue(path.is_symlink())

    def test_local_probe_results_are_bound_to_gate_nonce_profile_and_fake_execution(self):
        from commander_gym.qa_local_provider import validate_probe_result,LOCAL_GATES
        value={'kind':'isolated-local-qa-observation','gate':LOCAL_GATES[LOCAL_PROBES[0]],'nonce':'a'*32,
               'runtimeId':self.runtime.runtime_id,'observations':{'synthetic':'source-contract-only'},
               'externalProviderCalls':0,'productionQualificationIssued':False,'activationEligible':False}
        self.assertEqual(validate_probe_result(LOCAL_PROBES[0],value,self.profile,'a'*32),value)
        for key,item in [('nonce','b'*32),('runtimeId','0'*64),('gate',LOCAL_GATES[LOCAL_PROBES[1]]),
                         ('externalProviderCalls',True),('externalProviderCalls',1),('activationEligible',True),
                         ('productionQualificationIssued',True),('observations',{}),('extra',True)]:
            with self.subTest(key=key,item=item),self.assertRaisesRegex(ManualRuntimeError,'qa_local_probe_identity'):
                validate_probe_result(LOCAL_PROBES[0],{**value,key:item},self.profile,'a'*32)

    def test_unexpected_server_return_is_a_failure_after_cleanup(self):
        server=Mock();server.serve_forever.return_value=None
        with self.assertRaisesRegex(ManualRuntimeError,'qa_role_server_stopped_unexpectedly'):
            roles.serve(server,threading.Event())
        server.server_close.assert_called_once()

    def test_real_failure_cancellation_probe_reaps_http_callback_without_game(self):
        result=failure_cancellation(self.profile)
        self.assertTrue(result['acceptedCallbackCancelled']);self.assertTrue(result['callbackThreadsReaped'])
        self.assertTrue(result['badContextWithheldReadiness']);self.assertLess(result['elapsedSeconds'],4)

    def test_overlong_peer_socket_path_fails_before_fixture_access_or_fork(self):
        from commander_gym.qa_local_probes import recorder_peer
        with patch.object(qa,'ROOT',Path('/'+'x'*90)),patch('commander_gym.qa_local_probes.os.getuid',return_value=0),patch.object(Path,'lstat') as inspect,patch('commander_gym.qa_local_probes.multiprocessing.get_context') as context:
            with self.assertRaisesRegex(ManualRuntimeError,'qa_local_peer_socket_path'):recorder_peer(self.profile,'a'*32)
            inspect.assert_not_called();context.assert_not_called()

    def test_ready_notification_failure_closes_bound_server(self):
        server=Mock()
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaisesRegex(ManualRuntimeError,'qa_local_notify_socket'):roles.serve(server,threading.Event(),notify=True)
        server.server_close.assert_called_once();server.serve_forever.assert_not_called()


if __name__=='__main__':unittest.main()
