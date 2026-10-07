from contextlib import ExitStack
from dataclasses import replace
import json
import hashlib
from pathlib import Path
import signal
import shutil
import subprocess
import os
import tempfile
import time
import unittest
from unittest.mock import patch, Mock

from commander_gym import human_gui_preflight as preflight
from commander_gym import experimental_wait_preflight as wait
from commander_gym.human_gui_session import HumanGuiSessionGuard, HumanGuiSessionStopped, GuiProvenanceWatch
from commander_gym.pilot import ArgentumActionChoice, ArgentumDecisionChoice
from commander_gym.identity import Binding
from commander_gym.openai_run_budget import OpenAIRunBudget
from commander_gym.game_server_binding_openai_sidecar import (
    BindingOpenAIGameServerConfig, build_binding_openai_game_server_sidecar,
    binding_openai_game_server_config_from_environment,
)
from commander_gym.game_server_openai_sidecar import OpenAIGameServerSidecarConfig
from commander_gym.game_server_sidecar import UnknownProfileError
from tests import test_experimental_wait_preflight as fixture_module
from tests.test_game_server_binding_openai_sidecar import FakeClient, write_json, free_port
from scripts import run_human_three_luna_gui as launcher


class HumanGuiTests(unittest.TestCase):
    def fixture(self, root, stack):
        fixture_module.ExperimentalWaitPreflightTests().fixture(root, stack)
        roster = json.loads((root / wait.ROSTER_PATH).read_bytes())
        roster.update(roster_id='human-krenko-three-luna-explicit-wait-recovery-v8',
                      roster_revision='2026-10-07.1', source_roster=str(wait.ROSTER_PATH),
                      active_bindings=list(preflight.PROFILES))
        write_json(root / preflight.ROSTER_PATH, roster)
        stack.enter_context(patch.object(preflight, 'ROSTER_SHA256', preflight.v7._sha((root / preflight.ROSTER_PATH).read_bytes())))
        stack.enter_context(patch.object(preflight, 'BINDING_FINGERPRINTS', tuple(
            Binding.from_dict(json.loads((root / f'bindings/{name}.json').read_bytes())).fingerprint()
            for name in preflight.PROFILES)))

    def test_minimized_package_excludes_other_bindings_history_and_human_deck(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root, out = Path(tmp)/'source', Path(tmp)/'session'
            self.fixture(root, stack)
            (root/'raw-prompt.txt').write_text('must not copy')
            preflight.export_session_catalog(root, out)
            manifest_sha = preflight.v7._sha((out/preflight.SESSION_MANIFEST).read_bytes())
            proof = preflight.verify_session_catalog(out, manifest_sha)
            self.assertEqual(proof['profiles'], list(preflight.PROFILES))
            files = list(out.rglob('*'))
            self.assertFalse(any('krenko' in p.name or 'raw-prompt' in p.name for p in files))
            self.assertEqual(len(list((out/'bindings').glob('*.json'))), 3)
            self.assertEqual(len(list((out/'pilots').rglob('pilot.json'))), 1)
            self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in files if p.is_file()))
            (out/'unrelated.txt').write_text('reject')
            with self.assertRaisesRegex(ValueError, 'unrelated'):
                preflight.verify_session_catalog(out, manifest_sha)

    def test_three_ai_profiles_distinct_masked_routes_and_no_human_fallback(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root = Path(tmp)
            self.fixture(root, stack)
            guard = HumanGuiSessionGuard(time.time()+100, root/'stop.json')
            sink = []
            config = BindingOpenAIGameServerConfig(
                sidecar=OpenAIGameServerSidecarConfig(token='offline-token', api_key='sk-test-offline', port=free_port()),
                instance_root=root, catalog_path=root/preflight.ROSTER_PATH, human_gui_guard=guard,
                budget=OpenAIRunBudget(root/"budget.json", 5, initialize_new_ledger=True))
            server = build_binding_openai_game_server_sidecar(config, client=FakeClient(), provenance_sink=lambda *v: sink.append(v))
            try:
                self.assertEqual(tuple(p['id'] for p in server.controller_profiles), preflight.PROFILES)
                adapters = []
                for i, profile in enumerate(preflight.PROFILES):
                    seat_id = f'ai-{i}'
                    seat = server.resolve_seat(seat_id, profile)
                    adapters.append(seat)
                    frontier = next(s for s in seat._pilot.delegate.subsystems if s.spec.role=='frontier_escalation')
                    strategic = frontier.implementation.player.strategic_pilot
                    self.assertTrue(strategic.explicit_wait_guidance)
                    self.assertTrue(strategic.retry_transient_server_errors)
                    self.assertFalse(strategic.cache_friendly_history)
                    observed = []
                    def scripted(obs):
                        observed.append(obs)
                        return ArgentumActionChoice(action_id=0, params={'attackers':{}})
                    with patch.object(type(strategic), 'choose', side_effect=scripted):
                        result = seat.choose_action({'viewingPlayerId':seat_id, 'turnNumber':20},
                        [{'kind':'DeclareAttackers', 'actionType':'DeclareAttackers', 'validAttackers':[],
                          'action':{'type':'DeclareAttackers', 'playerId':seat_id, 'attackers':{}}}], None)
                    self.assertEqual(result.action['attackers'], {})
                    self.assertEqual(observed[0]['perspectivePlayerId'], seat_id)
                self.assertEqual(len({id(a._pilot.delegate) for a in adapters}), 3)
                with self.assertRaises(UnknownProfileError):
                    server.resolve_seat('human', 'human')
                self.assertEqual(guard._seats, {'ai-0','ai-1','ai-2'})
                self.assertTrue(all('human' not in str(event) for event in sink))
            finally:
                server.server_close()

    def test_guard_allows_human_thinking_and_late_turn_but_stops_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            guard = HumanGuiSessionGuard(time.time()+1000, Path(tmp)/'stop.json')
            for seat in ('a','b','c'):
                guard.check({'perspectivePlayerId':seat, 'state':{'mulligan':{}}})
            guard.check({'perspectivePlayerId':'a', 'state':{'turnNumber':30}})
            guard.check({'perspectivePlayerId':'b', 'state':{'turnNumber':30}})
            with self.assertRaises(HumanGuiSessionStopped):
                guard.check({'perspectivePlayerId':'d', 'state':{'turnNumber':1}})
            self.assertEqual(json.loads(guard.receipt_path.read_bytes())['reason'], 'human_gui_replacement_game_or_extra_ai')
            self.assertEqual(set(json.loads(guard.receipt_path.read_bytes())), {'reason'})

    def test_guard_deadline_and_opening_after_play_stop_without_action(self):
        for expired in (False, True):
            with tempfile.TemporaryDirectory() as tmp:
                guard = HumanGuiSessionGuard(time.time()+100, Path(tmp)/'stop.json')
                guard.check({'perspectivePlayerId':'a', 'state':{'turnNumber':1}})
                if expired:
                    guard.deadline_unix = time.time()-1
                with self.assertRaises(HumanGuiSessionStopped):
                    guard.check({'perspectivePlayerId':'a', 'state':{'mulligan':{}}})

    def test_environment_keeps_credentials_out_of_server_and_frontend(self):
        plan = dict(serverPort=18080,sidecarPort=18083,catalogRoot='/tmp/catalog',budgetLedger='/tmp/ledger',
                    cumulativeCapUsd=5,cumulativeMaxRequests=100)
        receipt = dict(sessionCapUsd=2,sessionMaxRequests=20)
        with patch.dict('os.environ', {'OPENAI_API_KEY':'ambient-secret','COMMANDER_GYM_PREFIX_TURN_LIMIT':'8',
                                     'COMMANDER_GYM_SIDECAR_TOKEN':'offline-sidecar-token-marker',
                                     'OFFLINE_RAW_MESSAGE':'offline-raw-message-marker'}):
            sidecar, server, frontend = launcher.service_environments(plan,receipt,Path('/tmp/run'),'dummy-key',time.time()+100)
        self.assertEqual(sidecar['OPENAI_API_KEY'],'dummy-key')
        self.assertNotIn('OPENAI_API_KEY',server)
        self.assertNotIn('OPENAI_API_KEY',frontend)
        self.assertNotIn('COMMANDER_GYM_SIDECAR_TOKEN',frontend)
        self.assertNotIn('OFFLINE_RAW_MESSAGE',frontend)
        self.assertNotIn('OFFLINE_RAW_MESSAGE',server)
        self.assertNotEqual(server['COMMANDER_GYM_SIDECAR_TOKEN'],'offline-sidecar-token-marker')
        self.assertNotIn('COMMANDER_GYM_PREFIX_TURN_LIMIT',sidecar)
        config = binding_openai_game_server_config_from_environment(sidecar)
        self.assertEqual(config.budget.session_max_requests,20)
        self.assertEqual(config.human_gui_guard.deadline_unix, config.budget.dispatch_deadline_unix)
        self.assertIsNone(config.prefix_guard)

    def test_supervisor_never_creates_game_and_cleans_under_lock_on_operator_stop(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root=Path(tmp)
            plan={gate:True for gate in launcher.GATES}
            plan.update(runtimeLock=str(root/'runtime.lock'),wallSeconds=100,serverPort=18080,
                        sidecarPort=18083,frontendPort=15175,engineDir=str(root/'engine'),
                        catalogRoot=str(root/'catalog'),budgetLedger=str(root/'budget'),
                        cumulativeCapUsd=5,cumulativeMaxRequests=100)
            proof={'classpath':['/tmp/offline.class'], 'snapshot':{'unsettledRequests':0,'requests':0,'estimatedUsd':0},
                   'sessionCapUsd':1,'sessionMaxRequests':5}
            stack.enter_context(patch.object(launcher,'preflight',return_value=proof))
            stack.enter_context(patch.object(launcher,'read_key',return_value='sk-test-offline-secret-marker'))
            stack.enter_context(patch.object(launcher,'await_ready'))
            stack.enter_context(patch.object(launcher,'verify_advertised_profiles'))
            budget = OpenAIRunBudget(root/'budget',5,max_requests=100,initialize_new_ledger=True)
            budget.snapshot()
            process=Mock(); process.poll.return_value=None
            start=stack.enter_context(patch.object(launcher.subprocess,'Popen',return_value=process))
            stack.enter_context(patch.object(launcher.GuiProvenanceWatch,'scan',side_effect=InterruptedError))
            def cleanup(_p):
                self.assertEqual(signal.getsignal(signal.SIGTERM),signal.SIG_IGN)
                with self.assertRaises(RuntimeError):
                    with launcher.exclusive_runtime_lock(Path(plan['runtimeLock'])):
                        pass
            stop=stack.enter_context(patch.object(launcher,'_stop_and_verify_group',side_effect=cleanup))
            run=root/'run'
            launcher.supervise(plan,root,run,root/'dummy-key',Path('/fake/python'))
            self.assertEqual(start.call_count,3)
            java = start.call_args_list[1].args[0]
            self.assertEqual(java[:4], ['java',
                '-Dlogging.level.com.wingedsheep.gameserver.handler.ConnectionHandler=WARN',
                '-Dlogging.level.com.wingedsheep.gameserver.websocket.GameWebSocketHandler=INFO', '-cp'])
            self.assertEqual([a for a in java if a.startswith('-Dlogging.level.')], list(launcher.JAVA_LOGGING_ARGUMENTS))
            preview = start.call_args_list[2].args[0]
            self.assertEqual(preview[1:4], ['preview','--configLoader','native'])
            self.assertEqual(preview[4:6], ['--config',str(root/'scripts/human_gui_preview.config.mjs')])
            for call in start.call_args_list:
                self.assertNotIn('sk-test-offline-secret-marker', ' '.join(call.args[0]))
                self.assertNotIn('offline-sidecar-token-marker', ' '.join(call.args[0]))
                self.assertNotIn('offline-raw-message-marker', ' '.join(call.args[0]))
            for call in start.call_args_list[1:]:
                self.assertNotIn('OPENAI_API_KEY', call.kwargs['env'])
            self.assertEqual(stop.call_count,3)
            self.assertFalse(any('two_luna_debug' in str(c) or 'ai-tournament' in str(c) for c in start.call_args_list))
            self.assertEqual(json.loads((run/'result.json').read_bytes())['reason'],'operator_stop')
            self.assertFalse(json.loads((run/'result.json').read_bytes())['naturalTerminalVerified'])

    def test_paid_gate_fail_closed_before_preflight_or_key(self):
        with patch.object(launcher,'preflight') as check, patch.object(launcher,'read_key') as key:
            with self.assertRaisesRegex(ValueError,'approval'):
                launcher.supervise({},Path('/tmp'),Path('/tmp/not-created'),Path('/tmp/key'),Path('/fake/python'))
            check.assert_not_called(); key.assert_not_called()

    def test_provenance_partial_line_has_no_idle_stall_and_detects_callback_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'policy.jsonl'; watch=GuiProvenanceWatch(path)
            watch.scan(); self.assertFalse(watch.failed)
            path.write_text('{"choice":{"channel":"error"}}')
            watch.scan(); self.assertEqual(watch.callbacks,0)
            with path.open('a') as f: f.write('\n')
            watch.scan(); self.assertTrue(watch.failed)

    def test_final_receipt_keeps_unsettled_and_partial_provenance_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            budget=OpenAIRunBudget(root/'ledger',5,max_requests=100,initialize_new_ledger=True)
            before=budget.snapshot()
            try:
                budget.create(lambda **_request: (_ for _ in ()).throw(RuntimeError('offline transport failure')),
                              {'model':'gpt-6-luna','input':'offline','max_output_tokens':OpenAIRunBudget.MAX_OUTPUT_TOKENS})
            except RuntimeError:
                pass
            (root/'policy.jsonl').write_text('{"choice":')
            status=launcher.final_receipt({'budgetLedger':str(root/'ledger'),'cumulativeCapUsd':5,'cumulativeMaxRequests':100},
                {'snapshot':before,'sessionCapUsd':1,'sessionMaxRequests':5},root,'operator_stop',None)
            receipt=json.loads((root/'result.json').read_bytes())
            self.assertEqual(status,1)
            self.assertEqual(receipt['newUnsettledRequests'],1)
            self.assertFalse(receipt['provenanceReconciled'])
            self.assertIn('incomplete trailing',receipt['provenanceError'])
            self.assertEqual(receipt['requestsUsed'],1)

    def test_runtime_failure_returns_nonzero_even_with_complete_zero_spend(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            budget=OpenAIRunBudget(root/'ledger',5,max_requests=100,initialize_new_ledger=True)
            before=budget.snapshot()
            self.assertEqual(launcher.final_receipt(
                {'budgetLedger':str(root/'ledger'),'cumulativeCapUsd':5,'cumulativeMaxRequests':100},
                {'snapshot':before,'sessionCapUsd':1,'sessionMaxRequests':5},root,'callback_or_native_failure',None),1)
            self.assertTrue(json.loads((root/'result.json').read_bytes())['provenanceReconciled'])

    def test_three_profiles_opening_and_typed_decision_use_only_own_masked_hand(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root=Path(tmp); self.fixture(root,stack)
            config=BindingOpenAIGameServerConfig(
                sidecar=OpenAIGameServerSidecarConfig(token='offline',api_key='sk-test-offline',port=free_port()),
                instance_root=root,catalog_path=root/preflight.ROSTER_PATH,
                budget=OpenAIRunBudget(root/'budget',5,initialize_new_ledger=True),
                human_gui_guard=HumanGuiSessionGuard(time.time()+100,root/'stop.json'))
            server=build_binding_openai_game_server_sidecar(config,client=FakeClient())
            try:
                for index, profile in enumerate(preflight.PROFILES):
                    seat=server.resolve_seat(f'ai-{index}',profile)
                    frontier=next(s for s in seat._pilot.delegate.subsystems if s.spec.role=='frontier_escalation')
                    strategic=frontier.implementation.player.strategic_pilot
                    def scripted(obs):
                        self.assertEqual(obs['perspectivePlayerId'],f'ai-{index}')
                        self.assertNotIn('snapshot',obs)
                        if 'mulligan' in obs['state']:
                            return ArgentumActionChoice(0)
                        return ArgentumDecisionChoice({'type':'CardsSelectedResponse',
                            'decisionId':obs['pendingDecision']['decisionId'], 'selectedCards':[f'own-{index}']})
                    with patch.object(type(strategic),'choose',side_effect=scripted):
                        self.assertTrue(seat.decide_mulligan({'hand':[f'own-{index}']}))
                        self.assertEqual(seat.choose_bottom_cards({'hand':[f'own-{index}'],
                                        'cardsToPutOnBottom':1}),[f'own-{index}'])
            finally:
                server.server_close()

    def test_runtime_classpath_verifies_order_content_and_required_compiled_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); gym=root/'gym'; engine=root/'engine'
            paths=[gym/'jvm-adapter/build/classes/kotlin/main',gym/'jvm-adapter/build/classes/kotlin/test',
                   engine/'game-server/build/classes/kotlin/main',engine/'rules-engine/build/classes/kotlin/main']
            for p in paths:
                p.mkdir(parents=True); (p/'Mock.class').write_bytes(b'offline fixture')
            names=[str(p) for p in paths]; path=root/'classpath.json'; path.write_text(json.dumps(names))
            sha=hashlib.sha256(json.dumps([[str(p),launcher._hash_entry(p)] for p in paths],
                              sort_keys=True,separators=(',',':')).encode()).hexdigest()
            self.assertEqual(launcher.runtime_classpath(path,sha,gym,engine),names)
            path.write_text(json.dumps(names[::-1]))
            with self.assertRaisesRegex(ValueError,'changed'):
                launcher.runtime_classpath(path,sha,gym,engine)
            path.write_text(json.dumps(names)); (paths[0]/'Mock.class').unlink()
            with self.assertRaisesRegex(ValueError,'compiled'):
                launcher.runtime_classpath(path,sha,gym,engine)

    @unittest.skipUnless(shutil.which('node'), 'Node is required for the native preview config check')
    def test_native_preview_config_keeps_http_websocket_proxy_and_rejects_remote_backend(self):
        config=(Path(launcher.__file__).parent/'human_gui_preview.config.mjs').as_uri()
        script='import('+json.dumps(config)+').then(m=>console.log(JSON.stringify(m.default)))'
        with tempfile.TemporaryDirectory() as tmp:
            env={'PATH':os.environ['PATH'], 'GAME_SERVER_URL':'http://127.0.0.1:18080'}
            result=subprocess.run(['node','--input-type=module','-e',script],cwd=tmp,env=env,
                                  capture_output=True,text=True,check=True)
            parsed=json.loads(result.stdout)
            self.assertEqual(Path(parsed['root']).resolve(),Path(tmp).resolve())
            self.assertEqual(parsed['preview']['proxy']['/game'],
                             {'target':'http://127.0.0.1:18080','ws':True,'changeOrigin':True})
            self.assertEqual(parsed['preview']['proxy']['/api'],
                             {'target':'http://127.0.0.1:18080','changeOrigin':True})
            for backend in ('', 'http://example.invalid:18080', 'https://127.0.0.1:18080',
                            'http://127.0.0.1:18080/private', 'http://user:secret@127.0.0.1:18080',
                            'http://127.0.0.1:18080?raw=offline-marker', 'http://127.0.0.1:65536'):
                env['GAME_SERVER_URL']=backend
                rejected=subprocess.run(['node','--input-type=module','-e',script],cwd=tmp,env=env,
                                        capture_output=True,text=True)
                self.assertNotEqual(rejected.returncode,0)
                self.assertIn('plain HTTP loopback',rejected.stderr)
                self.assertNotIn('user:secret',rejected.stderr)
                self.assertNotIn('raw=offline-marker',rejected.stderr)
