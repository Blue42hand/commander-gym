from contextlib import ExitStack
from dataclasses import replace
import json
from pathlib import Path
import signal
import tempfile
import time
import unittest
from unittest.mock import patch, Mock

from commander_gym import human_gui_preflight as preflight
from commander_gym import experimental_wait_preflight as wait
from commander_gym.human_gui_session import HumanGuiSessionGuard, HumanGuiSessionStopped, GuiProvenanceWatch
from commander_gym.pilot import ArgentumActionChoice
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
        with patch.dict('os.environ', {'OPENAI_API_KEY':'ambient-secret','COMMANDER_GYM_PREFIX_TURN_LIMIT':'8'}):
            sidecar, server, frontend = launcher.service_environments(plan,receipt,Path('/tmp/run'),'dummy-key',time.time()+100)
        self.assertEqual(sidecar['OPENAI_API_KEY'],'dummy-key')
        self.assertNotIn('OPENAI_API_KEY',server)
        self.assertNotIn('OPENAI_API_KEY',frontend)
        self.assertNotIn('COMMANDER_GYM_SIDECAR_TOKEN',frontend)
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
            proof={'classpath':['/tmp/offline.class'], 'snapshot':{'unsettledRequests':0},
                   'sessionCapUsd':1,'sessionMaxRequests':5}
            stack.enter_context(patch.object(launcher,'preflight',return_value=proof))
            stack.enter_context(patch.object(launcher,'read_key',return_value='dummy'))
            stack.enter_context(patch.object(launcher,'await_ready'))
            stack.enter_context(patch.object(launcher,'verify_advertised_profiles'))
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
