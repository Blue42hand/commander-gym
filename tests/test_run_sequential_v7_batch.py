"""Offline controls for the paid sequential wrapper. No provider or game starts."""

from argparse import Namespace
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from commander_gym.openai_run_budget import OpenAIRunBudget
from scripts import run_sequential_v7_batch as batch


class SequentialV7BatchTests(unittest.TestCase):
    def test_policy_attempt_receipt_counts_recovered_transport_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.jsonl"
            path.write_text(json.dumps({"choice": {"metadata": {
                "provider": "openai", "retryCount": 0,
                "modelIo": {"attempts": [
                    {"attempt": 0, "response": {"transportError": "status=520"}},
                    {"attempt": 1, "response": {"outputText": "{}"}},
                ]},
            }}}) + "\n")
            count, callbacks, _ = batch._policy_attempts(path)
            self.assertEqual((count, callbacks), (2, 1))

    def test_policy_attempt_receipt_counts_exhausted_transport_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.jsonl"
            path.write_text(json.dumps({"choice": {"channel": "error", "metadata": {
                "provider": "openai", "retryCount": 0,
                "modelIo": {"selectedAttempt": None, "attempts": [
                    {"attempt": 0, "response": {"transportError": "status=520"}},
                    {"attempt": 1, "response": {"transportError": "status=520"}},
                ]},
            }}}) + "\n")
            count, callbacks, _ = batch._policy_attempts(path)
            self.assertEqual((count, callbacks), (2, 1))

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.ledger = self.root / "existing-ledger.json"
        self.start = {
            "schemaVersion": 1, "capUsd": 18.0, "maxRequests": 2212,
            "estimatedUsd": 14.870275991999986, "requests": 1732,
            "inputTokens": 100, "outputTokens": 20, "unsettledRequests": 3,
        }
        self.ledger.write_text(json.dumps(self.start))
        self.key = self.root / "key.env"
        self.key.write_text("unused in offline test")
        self.catalog = self.root / "catalog.json"
        self.catalog.write_text("{}")
        self.args = Namespace(
            batch_dir=self.root / "batch", engine_dir=self.root,
            instance_root=self.root, catalog=self.catalog,
            python=Path(sys.executable), api_key_file=self.key,
            budget_ledger=self.ledger, runtime_lock=self.root / "runtime.lock",
            expected_gym_head="gym", expected_engine_head="engine",
            existing_cap_usd=18.0, max_requests=2500,
            expected_start_requests=1732, expected_start_unsettled=3,
            expected_start_usd=14.870275991999986,
            timeout=30, stall_seconds=10,
        )
        self.identity = {
            "schemaVersion": 1, "gymHead": "gym", "engineHead": "engine",
            "profiles": list(batch.PROFILES), "maxRequests": 2500,
            "qualificationPreflight": {"catalogClosureSha256": "qualified"},
        }

    def _runner(self, calls, *, fail_at=None, retry_at=None):
        def run(args, index, log, output_dir, expected_requests, expected_unsettled, cap):
            calls.append(index)
            budget = OpenAIRunBudget(self.ledger, cap, authorized_max_usd=cap,
                                     max_requests=2500)
            before = budget.snapshot()
            self.assertEqual(expected_requests, before["requests"])
            self.assertEqual(expected_unsettled, before["unsettledRequests"])
            self.assertEqual(args.before_estimated_usd, before["estimatedUsd"])
            budget.create(lambda **_: SimpleNamespace(usage={
                "input_tokens": 20, "output_tokens": 1,
                "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 20},
            }), {"model": "gpt-6-luna", "max_output_tokens": budget.MAX_OUTPUT_TOKENS})
            after = budget.snapshot()
            game = output_dir / "native-game"
            game.mkdir(parents=True)
            (game / "policy.jsonl").write_text(json.dumps({"choice": {"metadata": {
                "provider": "openai", "retryCount": 0,
                "modelIo": {"attempts": [{"attempt": 0}]},
            }}}) + "\n")
            terminal = {"nativeGameOver": True}
            for name, key in (("terminal-replay.json", "replaySha256"),
                              ("terminal-state.json", "terminalStateSha256")):
                path = game / name
                path.write_text("{}\n")
                terminal[key] = hashlib.sha256(path.read_bytes()).hexdigest()
            result = {
                "providerRequests": 1, "recordedGameApiRequests": 1,
                "provenanceFinalizationComplete": True,
                "fourSeatProvenanceComplete": True, "expectedSeats": 4,
                "singlePodGame": True, "completed": True,
                "stopReason": "native_complete", "terminalEvidence": {"nativeGameOver": True},
                "terminalArtifact": terminal, "validationRetries": int(index == retry_at),
                "technicalQualified": index != fail_at,
                "budget": {"gameApiRequests": 1, "gameInputTokens": 20,
                           "gameOutputTokens": 1, "cumulativeApiRequests": after["requests"],
                           "unsettledRequests": after["unsettledRequests"], "capUsd": cap},
            }
            log.write_text(
                "TWO_LUNA_QUALIFICATION_PREFLIGHT " + json.dumps(self.identity["qualificationPreflight"]) + "\n"
                + "RUN_ARTIFACTS=" + str(game) + "\n"
                + "TWO_LUNA_DEBUG_RESULT=" + json.dumps(result) + "\n"
            )
            return int(index == fail_at)
        return run

    def test_three_games_preserve_history_and_stop_at_three(self):
        calls = []
        with patch.object(batch, "_identity", return_value=self.identity):
            cursor = batch.run_batch(self.args, runner=self._runner(calls))
            again = batch.run_batch(self.args, runner=self._runner(calls))
        self.assertEqual(cursor, {"status": "ready", "nextGame": 4})
        self.assertEqual(again, cursor)
        self.assertEqual(calls, [1, 2, 3])
        after = json.loads(self.ledger.read_text())
        self.assertEqual(after["requests"], 1735)
        self.assertEqual(after["unsettledRequests"], 3)
        self.assertEqual(after["maxRequests"], 2500)
        self.assertEqual(after["capUsd"], self.start["estimatedUsd"] + 10)
        self.assertTrue(all((self.args.batch_dir / f"game-{i:02d}-receipt.json").is_file()
                            for i in range(1, 4)))

    def test_recovered_retry_stops_before_second_game(self):
        calls = []
        with patch.object(batch, "_identity", return_value=self.identity):
            cursor = batch.run_batch(self.args, runner=self._runner(calls, retry_at=1))
            with self.assertRaisesRegex(batch.BatchError, "stopped or ambiguous"):
                batch.run_batch(self.args, runner=self._runner(calls))
        self.assertEqual(calls, [1])
        self.assertEqual(cursor["status"], "stopped")
        self.assertIn("recovered retry", cursor["reason"][0])

    def test_inflight_exception_never_replays(self):
        calls = []
        def interrupted(*_):
            calls.append(1)
            raise RuntimeError("lost launcher")
        with patch.object(batch, "_identity", return_value=self.identity):
            with self.assertRaisesRegex(RuntimeError, "lost launcher"):
                batch.run_batch(self.args, runner=interrupted)
            with self.assertRaisesRegex(batch.BatchError, "stopped or ambiguous"):
                batch.run_batch(self.args, runner=self._runner(calls))
        self.assertEqual(calls, [1])
        self.assertEqual(json.loads((self.args.batch_dir / "cursor.json").read_text())["status"],
                         "inflight")

    def test_terminal_replay_tamper_stops_before_second_game(self):
        calls = []
        normal = self._runner(calls)
        def corrupted(args, index, log, output_dir, expected_requests,
                      expected_unsettled, cap):
            code = normal(args, index, log, output_dir, expected_requests,
                          expected_unsettled, cap)
            (output_dir / "native-game" / "terminal-replay.json").write_text("tampered\n")
            return code
        with patch.object(batch, "_identity", return_value=self.identity):
            cursor = batch.run_batch(self.args, runner=corrupted)
        self.assertEqual(calls, [1])
        self.assertEqual(cursor["status"], "stopped")
        self.assertTrue(any("terminal-replay.json hash" in reason for reason in cursor["reason"]))

    def test_ledger_drift_refuses_next_dispatch(self):
        calls = []
        with patch.object(batch, "_identity", return_value=self.identity):
            batch.run_batch(self.args, runner=self._runner(calls))
            changed = json.loads(self.ledger.read_text())
            changed["estimatedUsd"] += 0.01
            self.ledger.write_text(json.dumps(changed))
            with self.assertRaisesRegex(batch.BatchError, "changed outside"):
                batch.run_batch(self.args, runner=self._runner(calls))
        self.assertEqual(calls, [1, 2, 3])

    def test_source_mutation_between_games_refuses_second_dispatch(self):
        calls = []
        changed = {**self.identity, "gymHead": "unreviewed"}
        with patch.object(batch, "_identity", side_effect=[self.identity, self.identity, changed]):
            with self.assertRaisesRegex(batch.BatchError, "changed before next game"):
                batch.run_batch(self.args, runner=self._runner(calls))
        self.assertEqual(calls, [1])
        self.assertEqual(json.loads((self.args.batch_dir / "cursor.json").read_text()),
                         {"status": "ready", "nextGame": 2})

    def test_wrong_start_or_existing_artifacts_refuse_before_cap_change(self):
        self.args.batch_dir.mkdir()
        (self.args.batch_dir / "foreign.txt").write_text("do not overwrite")
        with patch.object(batch, "_identity", return_value=self.identity):
            with self.assertRaisesRegex(batch.BatchError, "evidence exists"):
                batch.run_batch(self.args, runner=self._runner([]))
        self.assertEqual(json.loads(self.ledger.read_text()), self.start)

    def test_launcher_forces_reviewed_route_and_cleans_interrupted_child(self):
        class Child:
            pid = 12345
            def wait(self, timeout=None):
                raise InterruptedError("signal")
            def poll(self):
                return None

        log = self.root / "launch.log"
        self.args.before_estimated_usd = self.start["estimatedUsd"]
        with patch.object(batch.subprocess, "Popen", return_value=Child()) as popen, \
             patch.object(batch, "_stop_and_verify_group") as cleanup, \
             patch.dict(batch.os.environ, {"COMMANDER_GYM_CACHE_FRIENDLY_HISTORY": "true"}):
            with self.assertRaises(InterruptedError):
                batch.launch_one_game(self.args, 1, log, self.root / "game-01",
                                      1732, 3, self.start["estimatedUsd"] + 10)
        command = popen.call_args.args[0]
        self.assertEqual(command[1:3], ["-m", "scripts.run_two_luna_binding_game"])
        self.assertIn("--qualified-v7-comparison", command)
        self.assertIn("--supervised-batch-process-group", command)
        self.assertEqual(command.count("--profile"), 4)
        self.assertIn("--expected-ledger-estimated-usd", command)
        self.assertEqual(popen.call_args.kwargs["env"]["COMMANDER_GYM_CACHE_FRIENDLY_HISTORY"],
                         "false")
        cleanup.assert_called_once()

    def test_real_interruption_reaps_two_isolated_services_before_lock_release(self):
        """A dummy launcher and two dummy services stand in for the FFA process tree."""
        original_popen = subprocess.Popen
        service_code = "import time; time.sleep(60)"
        launcher_code = (
            "import subprocess,sys,time; "
            "children=[subprocess.Popen([sys.executable,'-c'," + repr(service_code)
            + "]) for _ in range(2)]; "
            "print('DUMMY_SERVICE_PIDS='+','.join(str(c.pid) for c in children),flush=True); "
            "time.sleep(60)"
        )
        class InterruptedChild:
            def __init__(self, actual):
                self.actual = actual
                self.pid = actual.pid
                self.first_wait = True
            def wait(self, timeout=None):
                if self.first_wait:
                    self.first_wait = False
                    raise InterruptedError("simulated batch termination")
                return self.actual.wait(timeout=timeout)
            def poll(self):
                return self.actual.poll()

        def dummy_popen(_command, **kwargs):
            process = original_popen([sys.executable, "-c", launcher_code], **kwargs)
            try:
                deadline = batch.time.monotonic() + 3
                log_path = self.args.batch_dir / "game-01-launcher.log"
                while "DUMMY_SERVICE_PIDS=" not in log_path.read_text():
                    if batch.time.monotonic() > deadline:
                        raise AssertionError("dummy services did not start")
                    batch.time.sleep(0.01)
            except BaseException:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
                raise
            return InterruptedChild(process)

        def lock_is_still_held(child):
            second = os.open(self.args.runtime_lock, os.O_RDWR)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(second, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(second)
            original_cleanup(child)
            second = os.open(self.args.runtime_lock, os.O_RDWR)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(second, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(second)

        original_cleanup = batch._stop_and_verify_group
        def interrupted_runner(args, index, log, output_dir, expected_requests,
                               expected_unsettled, cap):
            return batch.launch_one_game(args, index, log, output_dir,
                                         expected_requests, expected_unsettled, cap)
        with patch.object(batch, "_identity", return_value=self.identity), \
             patch.object(batch.subprocess, "Popen", side_effect=dummy_popen), \
             patch.object(batch, "_stop_and_verify_group", side_effect=lock_is_still_held):
            with self.assertRaises(InterruptedError):
                batch.run_batch(self.args, runner=interrupted_runner)
        log = (self.args.batch_dir / "game-01-launcher.log").read_text()
        pids = [int(pid) for pid in log.split("DUMMY_SERVICE_PIDS=", 1)[1].splitlines()[0].split(",")]
        for pid in pids:
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
        self.assertEqual(json.loads((self.args.batch_dir / "cursor.json").read_text())["status"],
                         "inflight")

    def test_signal_during_spawn_still_tracks_and_cleans_child(self):
        child = SimpleNamespace(pid=12345)
        def start(*_args, **_kwargs):
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            return child
        self.args.before_estimated_usd = self.start["estimatedUsd"]
        with patch.object(batch.subprocess, "Popen", side_effect=start), \
             patch.object(batch, "_stop_and_verify_group") as cleanup:
            with self.assertRaises(InterruptedError):
                batch.launch_one_game(self.args, 1, self.root / "signal.log",
                                      self.root / "game-01", 1732, 3,
                                      self.start["estimatedUsd"] + 10)
        cleanup.assert_called_once_with(child)

    def test_signal_during_success_cleanup_never_starts_next_game(self):
        class SuccessfulChild:
            pid = 12345
            def wait(self, timeout=None):
                return 0

        for stop_signal in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(stop_signal=stop_signal):
                self.ledger.write_text(json.dumps(self.start))
                self.args.batch_dir = self.root / f"batch-{stop_signal}"
                self.args.runtime_lock = self.root / f"lock-{stop_signal}"
                launches = []
                def start(*_args, **_kwargs):
                    launches.append(1)
                    return SuccessfulChild()
                def cleanup(_child):
                    signal.getsignal(stop_signal)(stop_signal, None)
                with patch.object(batch, "_identity", return_value=self.identity), \
                     patch.object(batch.subprocess, "Popen", side_effect=start), \
                     patch.object(batch, "_stop_and_verify_group", side_effect=cleanup):
                    with self.assertRaises(InterruptedError):
                        batch.run_batch(self.args)
                self.assertEqual(launches, [1])
                self.assertEqual(json.loads((self.args.batch_dir / "cursor.json").read_text())["status"],
                                 "inflight")


if __name__ == "__main__":
    unittest.main()
