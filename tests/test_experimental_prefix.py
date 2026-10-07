import argparse
from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
import os
import sys
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from commander_gym.experimental_prefix import PrefixGuard, ExperimentalPrefixStopped, exclusive_runtime_lock
from commander_gym.game_server_binding_openai_sidecar import (
    _CanonicalBindingPilot, binding_openai_game_server_config_from_environment,
)
from commander_gym.game_server_openai_sidecar import OpenAIGameServerSidecarConfigurationError
from commander_gym.openai_run_budget import OpenAIRunBudget, OpenAIRunBudgetError
from commander_gym.two_luna_debug import run
from scripts.run_two_luna_binding_game import validate_prefix_args, WAIT_PROFILES
from scripts.run_two_luna_binding_game import main as launch_main


class ExperimentalPrefixTests(unittest.TestCase):
    def test_launcher_wires_session_and_prefix_before_services_and_holds_lock_through_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ledger = root / "ledger.json"
            OpenAIRunBudget(ledger, 5, max_requests=100, initialize_new_ledger=True).snapshot()
            lock = root / "runtime.lock"
            out = root / "output"
            argv = ["launch", "--engine-dir", str(root), "--instance-root", str(root),
                    "--catalog", str(root / "catalog.json"), "--output-dir", str(out),
                    "--api-key-file", str(root / "dummy-unused"), "--python", sys.executable,
                    "--budget-ledger", str(ledger), "--budget-max-requests", "100",
                    "--expected-ledger-requests", "0", "--expected-unsettled-requests", "0",
                    "--expected-ledger-estimated-usd", "0", "--experimental-wait-prefix",
                    "--session-cap-usd", ".75", "--session-max-requests", "2",
                    "--runtime-lock", str(lock), "--expected-gym-head", "fixture",
                    "--expected-engine-head", "fixture", "--timeout", "900",
                    *(item for profile in WAIT_PROFILES for item in ("--profile", profile))]
            def assert_locked(*_args):
                with self.assertRaises(RuntimeError):
                    with exclusive_runtime_lock(lock):
                        self.fail("runtime lock released too early")
            def runner(command, **kwargs):
                assert_locked()
                self.assertIn("--session-max-requests", command)
                self.assertIn("--prefix-turn-limit", command)
                self.assertNotIn("OPENAI_API_KEY", kwargs["env"])
                self.assertLessEqual(kwargs["timeout"], 900)
                return SimpleNamespace(returncode=1)
            with patch("sys.argv", argv), patch("scripts.run_two_luna_binding_game.subprocess.check_output",
                    side_effect=["fixture", "", "fixture", ""]), \
                    patch("scripts.run_two_luna_binding_game.stage_experimental_wait_catalog",
                          return_value=(root, root / "catalog.json", {"qualification": False})), \
                    patch("scripts.run_two_luna_binding_game.read_key", return_value="sk-test-offline"), \
                    patch("scripts.run_two_luna_binding_game.free_port", return_value=12345), \
                    patch("scripts.run_two_luna_binding_game.await_ready"), \
                    patch("scripts.run_two_luna_binding_game.subprocess.Popen") as started, \
                    patch("scripts.run_two_luna_binding_game.subprocess.run", side_effect=runner), \
                    patch("scripts.run_two_luna_binding_game._stop_and_verify_group", side_effect=assert_locked) as cleanup, \
                    redirect_stdout(io.StringIO()):
                started.return_value.poll.return_value = 0
                self.assertEqual(launch_main(), 1)
            self.assertEqual(cleanup.call_count, 2)
            sidecar_env = started.call_args_list[0].kwargs["env"]
            config = binding_openai_game_server_config_from_environment(sidecar_env)
            self.assertEqual(config.prefix_guard.turn_limit, 8)
            self.assertEqual(config.budget.session_max_requests, 2)
            self.assertFalse(config.cache_friendly_history)
            provider = Mock(return_value=SimpleNamespace(usage={"input_tokens": 1, "output_tokens": 1}))
            request = {"model": "gpt-6-luna", "max_output_tokens": 2048, "input": "offline"}
            for _ in range(2):
                config.budget.create(provider, request)
            with self.assertRaisesRegex(OpenAIRunBudgetError, "session request limit"):
                config.budget.create(provider, request)
            self.assertEqual(provider.call_count, 2)
            self.assertEqual(config.budget.snapshot()["maxRequests"], 100)
            receipt = next(out.glob("game-*/prefix-run-receipt.json"))
            self.assertTrue(json.loads(receipt.read_text())["cleanupVerified"])
            self.assertFalse(json.loads((out / "prefix-manifest.json").read_text())["qualification"])
            with exclusive_runtime_lock(lock):
                pass
            # A new launcher must never replay into this evidence directory.
            with patch("sys.argv", argv), patch("scripts.run_two_luna_binding_game.subprocess.check_output",
                    side_effect=["fixture", "", "fixture", ""]), self.assertRaisesRegex(RuntimeError, "request count changed"):
                launch_main()

    def test_turn_guard_stops_before_any_composed_choice_and_preserves_masked_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            guard = PrefixGuard(8, 200, Path(temporary) / "stop.json")
            delegate = Mock()
            pilot = _CanonicalBindingPilot(delegate=delegate, pilot=Mock(), binding=Mock(), prefix_guard=guard)
            obs = {"state": {"turnNumber": 9}}
            with patch("commander_gym.experimental_prefix.time.time", return_value=100), \
                    self.assertRaisesRegex(ExperimentalPrefixStopped, "prefix_turn_limit"):
                pilot.choose(obs)
            delegate.choose.assert_not_called()
            receipt = json.loads(guard.receipt_path.read_text())
            self.assertEqual(receipt, {"reason": "prefix_turn_limit", "turnNumber": 9, "turnLimit": 8})
            self.assertEqual(guard.receipt_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(obs, {"state": {"turnNumber": 9}})

    def test_guard_admits_opening_callbacks_and_turn_eight_but_rejects_missing_turn(self):
        with tempfile.TemporaryDirectory() as temporary, patch(
            "commander_gym.experimental_prefix.time.time", return_value=100
        ):
            guard = PrefixGuard(8, 200, Path(temporary) / "stop.json")
            for obs in ({"state": {"turnNumber": 8}}, {"state": {"mulligan": {}}},
                        {"state": {}, "pendingDecision": {"kind": "BottomCards"}}):
                guard.check(obs)
            with self.assertRaisesRegex(ExperimentalPrefixStopped, "turnNumber"):
                guard.check({"state": {}})

    def test_wall_stop_is_durable_and_never_overwrites_first_stop(self):
        with tempfile.TemporaryDirectory() as temporary, patch(
            "commander_gym.experimental_prefix.time.time", return_value=201
        ):
            guard = PrefixGuard(8, 200, Path(temporary) / "stop.json")
            for _ in range(2):
                with self.assertRaisesRegex(ExperimentalPrefixStopped, "prefix_wall_limit"):
                    guard.check({"state": {"turnNumber": 3}})
            self.assertEqual(json.loads(guard.receipt_path.read_text())["reason"], "prefix_wall_limit")

    def test_shared_runtime_lock_rejects_second_launcher_and_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "runtime.lock"
            with exclusive_runtime_lock(path):
                with self.assertRaisesRegex(RuntimeError, "another process"):
                    with exclusive_runtime_lock(path):
                        self.fail("second launcher admitted")
            alias = Path(temporary) / "alias"
            alias.symlink_to(path)
            with self.assertRaises(ValueError):
                with exclusive_runtime_lock(alias):
                    self.fail("symlink admitted")

    def test_sidecar_environment_wires_all_bounds_and_rejects_partial_prefix(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = {"COMMANDER_GYM_SIDECAR_TOKEN": "dummy", "OPENAI_API_KEY": "sk-test-offline",
                    "COMMANDER_GYM_BINDING_CATALOG": str(root / "catalog.json"),
                    "COMMANDER_GYM_INSTANCE_ROOT": str(root),
                    "COMMANDER_GYM_OPENAI_BUDGET_LEDGER": str(root / "budget.json"),
                    "COMMANDER_GYM_OPENAI_BUDGET_CAP_USD": "5",
                    "COMMANDER_GYM_OPENAI_BUDGET_MAX_REQUESTS": "100",
                    "COMMANDER_GYM_OPENAI_SESSION_CAP_USD": ".75",
                    "COMMANDER_GYM_OPENAI_SESSION_MAX_REQUESTS": "60",
                    "COMMANDER_GYM_PREFIX_TURN_LIMIT": "8",
                    "COMMANDER_GYM_PREFIX_DEADLINE_UNIX": "200",
                    "COMMANDER_GYM_PREFIX_STOP_RECEIPT": str(root / "stop.json")}
            config = binding_openai_game_server_config_from_environment(base)
            self.assertEqual((config.budget.session_cap_usd, config.budget.session_max_requests), (.75, 60))
            self.assertEqual(config.budget.dispatch_deadline_unix, 200)
            self.assertEqual(config.prefix_guard.turn_limit, 8)
            for name in ("COMMANDER_GYM_PREFIX_DEADLINE_UNIX", "COMMANDER_GYM_OPENAI_SESSION_MAX_REQUESTS"):
                partial = {k: v for k, v in base.items() if k != name}
                with self.assertRaises(OpenAIGameServerSidecarConfigurationError):
                    binding_openai_game_server_config_from_environment(partial)

    def test_budget_deadline_refuses_dispatch_before_or_after_durable_reservation(self):
        for times, requests in (([201], 0), ([100, 201], 1)):
            with self.subTest(times=times), tempfile.TemporaryDirectory() as temporary:
                budget = OpenAIRunBudget(Path(temporary) / "ledger.json", 5,
                                        initialize_new_ledger=True, dispatch_deadline_unix=200)
                provider = Mock()
                with patch("commander_gym.openai_run_budget.time.time", side_effect=times), \
                        self.assertRaises(OpenAIRunBudgetError) as caught:
                    budget.create(provider, {"model": "gpt-6-luna", "max_output_tokens": 2048, "input": "offline"})
                self.assertFalse(caught.exception.dispatched)
                provider.assert_not_called()
                self.assertEqual(budget.snapshot()["requests"], requests)
                self.assertEqual(budget.snapshot()["unsettledRequests"], requests)

    def test_validation_refuses_broader_session_and_turn_limits(self):
        base = dict(qualified_v7_comparison=False, dry_run=False, supervised_batch_process_group=False,
                    profiles=WAIT_PROFILES, profile_a=WAIT_PROFILES[0], profile_b=WAIT_PROFILES[1],
                    budget_ledger=Path("/offline/ledger"), runtime_lock=Path("/offline/lock"),
                    expected_gym_head="frozen", expected_engine_head="frozen",
                    expected_ledger_requests=2423, expected_unsettled_requests=4,
                    expected_ledger_estimated_usd=22.136852484999963, session_cap_usd=22.886852484999963,
                    session_max_requests=2483, timeout=900, prefix_turn_limit=8, max_attempts=2,
                    budget_max_requests=2500, budget_cap=24.870275991999986,
                    budget_authorized_max=24.870275991999986)
        parser = Mock()
        parser.error.side_effect = ValueError
        validate_prefix_args(SimpleNamespace(**base), parser)
        for change in ({"session_max_requests": 2484}, {"session_cap_usd": 22.9},
                       {"prefix_turn_limit": 9}, {"timeout": 901}, {"budget_cap": 25},
                       {"profiles": WAIT_PROFILES[::-1]}, {"qualified_v7_comparison": True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_prefix_args(SimpleNamespace(**{**base, **change}), parser)

    def test_prefix_runner_stops_on_native_turn_nine_without_qualifying(self):
        with tempfile.TemporaryDirectory() as temporary:
            ids = ["a", "b", "c", "d"]
            profiles = {"profiles": [{"id": i, "deck": {"commander": "Fixture", "cards": {"Forest": 99}}} for i in ids]}
            status = {"complete": False, "gameMode": "FREE_FOR_ALL", "ffaGamesPlayed": 1,
                      "liveGames": [{"gameSessionId": "game", "playerCount": 4, "turnNumber": 9}]}
            snapshot = {"capUsd": 5, "estimatedUsd": .1, "requests": 1, "inputTokens": 10,
                        "outputTokens": 1, "unsettledRequests": 0}
            args = SimpleNamespace(profiles=ids, sidecar_url="http://offline", server_url="http://offline",
                                   timeout=10, stall_seconds=10, provenance=None, server_log=None,
                                   terminal_evidence_dir=None, prefix_turn_limit=8,
                                   prefix_stop_receipt=str(Path(temporary) / "stop.json"),
                                   session_max_requests=60, session_cap_usd=.75)
            output = io.StringIO()
            with patch.dict(os.environ, {"COMMANDER_GYM_SIDECAR_TOKEN": "dummy"}), \
                    patch("commander_gym.two_luna_debug._request_json", side_effect=[profiles, {"lobbyId": "lobby"}, status]), \
                    patch("commander_gym.two_luna_debug._run_budget", return_value=SimpleNamespace(snapshot=lambda: snapshot)), \
                    redirect_stdout(output):
                self.assertEqual(run(args), 1)
            result = json.loads(output.getvalue().split("TWO_LUNA_DEBUG_RESULT=")[1])
            self.assertEqual(result["stopReason"], "prefix_turn_limit")
            self.assertTrue(result["experimentalPrefix"])
            self.assertFalse(result["technicalQualified"])


if __name__ == "__main__":
    unittest.main()
