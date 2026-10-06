"""Offline controls for the two unplayed games in the stopped v7 batch."""

from argparse import Namespace
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from commander_gym.openai_run_budget import OpenAIRunBudget
from scripts import run_v7_batch_continuation as continuation


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class V7BatchContinuationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.original = self.root / "original"
        self.original.mkdir(mode=0o700)
        self.ledger = self.root / "ledger.json"
        self.start = {
            "schemaVersion": 1, "capUsd": continuation.CAP_USD,
            "maxRequests": continuation.MAX_REQUESTS,
            "estimatedUsd": continuation.START_USD,
            "requests": continuation.START_REQUESTS,
            "inputTokens": 100, "outputTokens": 20,
            "unsettledRequests": continuation.START_UNSETTLED,
        }
        write_json(self.ledger, self.start)
        # Match the budget writer's durable canonical JSON before freezing its hash.
        OpenAIRunBudget(self.ledger, continuation.CAP_USD,
                        authorized_max_usd=continuation.CAP_USD,
                        max_requests=continuation.MAX_REQUESTS).snapshot()
        write_json(self.original / "manifest.json", {
            "gymHead": continuation.GYM_HEAD,
            "cumulativeCapUsd": continuation.CAP_USD,
            "maxRequests": continuation.MAX_REQUESTS,
        })
        write_json(self.original / "game-01-receipt.json", {
            "index": 1, "qualified": False, "stopReasons": ["native failure"],
            "afterLedger": self.start,
        })
        write_json(self.original / "cursor.json", {
            "status": "stopped", "nextGame": 1, "reason": ["native failure"],
        })
        self.original_hashes = {
            name: sha(self.original / name)
            for name in continuation.ORIGINAL_HASHES
        }
        self.key = self.root / "key.env"
        self.key.write_text("unused in offline test")
        self.catalog = self.root / "catalog.json"
        self.catalog.write_text("{}")
        self.server_jar = self.root / "server.jar"
        self.server_jar.write_bytes(b"test server")
        self.adapter_jar = self.root / "adapter.jar"
        self.adapter_jar.write_bytes(b"test adapter")
        self.args = Namespace(
            continuation_dir=self.root / "continuation", gym_dir=self.root,
            engine_dir=self.root, instance_root=self.root, catalog=self.catalog,
            python=Path(sys.executable), api_key_file=self.key,
            budget_ledger=self.ledger, runtime_lock=self.root / "runtime.lock",
            game_server_jar=self.server_jar, adapter_jar=self.adapter_jar,
            expected_control_head="reviewed-control", expected_gym_head=continuation.GYM_HEAD,
            expected_engine_head=continuation.ENGINE_HEAD,
            timeout=30, stall_seconds=10, max_requests=continuation.MAX_REQUESTS,
        )
        self.identity = {
            "schemaVersion": 2, "gymHead": continuation.GYM_HEAD,
            "engineHead": continuation.ENGINE_HEAD,
            "qualificationPreflight": {"catalogClosureSha256": "qualified"},
            "cumulativeCapUsd": continuation.CAP_USD,
            "maxRequests": continuation.MAX_REQUESTS,
            "firstOriginalGame": 2, "lastOriginalGame": 3,
        }
        self.original_patch = patch.multiple(
            continuation, ORIGINAL_BATCH_DIR=self.original,
            ORIGINAL_HASHES=self.original_hashes,
            START_LEDGER_SHA256=sha(self.ledger),
        )
        self.original_patch.start()
        self.addCleanup(self.original_patch.stop)

    def runner(self, calls: list[int], *, fail_at: int | None = None):
        def run(args, index, log, output_dir, expected_requests, expected_unsettled, cap):
            calls.append(index)
            budget = OpenAIRunBudget(self.ledger, cap, authorized_max_usd=cap,
                                     max_requests=continuation.MAX_REQUESTS)
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
                terminal[key] = sha(path)
            result = {
                "providerRequests": 1, "recordedGameApiRequests": 1,
                "provenanceFinalizationComplete": True,
                "fourSeatProvenanceComplete": True, "expectedSeats": 4,
                "singlePodGame": True, "completed": True,
                "stopReason": "native_complete", "terminalEvidence": {"nativeGameOver": True},
                "terminalArtifact": terminal, "validationRetries": 0,
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

    def test_two_attempts_preserve_original_and_never_replay(self):
        calls = []
        with patch.object(continuation, "_identity", return_value=self.identity):
            cursor = continuation.run_continuation(self.args, runner=self.runner(calls))
            again = continuation.run_continuation(self.args, runner=self.runner(calls))
        self.assertEqual(cursor, {"status": "ready", "nextGame": 4,
                                  "lastReceiptSha256": sha(self.args.continuation_dir / "game-03-receipt.json")})
        self.assertEqual(again, cursor)
        self.assertEqual(calls, [2, 3])
        self.assertFalse((self.args.continuation_dir / "game-01").exists())
        self.assertEqual(json.loads(self.ledger.read_text())["capUsd"], continuation.CAP_USD)
        self.assertEqual(json.loads(self.ledger.read_text())["maxRequests"], continuation.MAX_REQUESTS)
        self.assertEqual({name: sha(self.original / name) for name in self.original_hashes},
                         self.original_hashes)

    def test_original_hash_or_cursor_tamper_refuses_before_dispatch(self):
        calls = []
        (self.original / "cursor.json").write_text('{"status":"ready","nextGame":2}\n')
        with patch.object(continuation, "_identity", return_value=self.identity):
            with self.assertRaisesRegex(continuation.batch.BatchError, "hash changed"):
                continuation.run_continuation(self.args, runner=self.runner(calls))
        self.assertEqual(calls, [])

    def test_original_stopped_status_is_required_even_with_reviewed_hashes(self):
        calls = []
        write_json(self.original / "cursor.json", {"status": "ready", "nextGame": 2})
        with patch.object(continuation, "ORIGINAL_HASHES", {
            **self.original_hashes, "cursor.json": sha(self.original / "cursor.json")
        }), patch.object(continuation, "_identity", return_value=self.identity):
            with self.assertRaisesRegex(continuation.batch.BatchError, "did not stop"):
                continuation.run_continuation(self.args, runner=self.runner(calls))
        self.assertEqual(calls, [])

    def test_original_unaccounted_later_artifact_refuses_dispatch(self):
        calls = []
        (self.original / "game-02").mkdir()
        with patch.object(continuation, "_identity", return_value=self.identity):
            with self.assertRaisesRegex(continuation.batch.BatchError, "unaccounted later"):
                continuation.run_continuation(self.args, runner=self.runner(calls))
        self.assertEqual(calls, [])

    def test_ledger_hash_or_semantics_drift_refuses_before_dispatch(self):
        calls = []
        changed = {**self.start, "estimatedUsd": self.start["estimatedUsd"] + 0.01}
        write_json(self.ledger, changed)
        with patch.object(continuation, "_identity", return_value=self.identity):
            with self.assertRaisesRegex(continuation.batch.BatchError, "ledger hash"):
                continuation.run_continuation(self.args, runner=self.runner(calls))
        self.assertEqual(calls, [])

    def test_unqualified_attempt_stops_and_cannot_resume(self):
        calls = []
        with patch.object(continuation, "_identity", return_value=self.identity):
            cursor = continuation.run_continuation(self.args,
                                                   runner=self.runner(calls, fail_at=2))
            with self.assertRaisesRegex(continuation.batch.BatchError, "stopped or.*ambiguous"):
                continuation.run_continuation(self.args, runner=self.runner(calls))
        self.assertEqual(calls, [2])
        self.assertEqual(cursor["status"], "stopped")
        self.assertEqual(cursor["nextGame"], 2)
        self.assertEqual(cursor["lastReceiptSha256"],
                         sha(self.args.continuation_dir / "game-02-receipt.json"))

    def test_interrupted_attempt_remains_inflight_and_never_replays(self):
        calls = []
        def interrupted(*_):
            calls.append(2)
            raise InterruptedError("lost launcher")
        with patch.object(continuation, "_identity", return_value=self.identity):
            with self.assertRaisesRegex(InterruptedError, "lost launcher"):
                continuation.run_continuation(self.args, runner=interrupted)
            with self.assertRaisesRegex(continuation.batch.BatchError, "stopped or.*ambiguous"):
                continuation.run_continuation(self.args, runner=self.runner(calls))
        self.assertEqual(calls, [2])
        self.assertEqual(json.loads((self.args.continuation_dir / "cursor.json").read_text())["status"],
                         "inflight")

    def test_completed_receipt_or_cursor_tamper_refuses_replay(self):
        calls = []
        with patch.object(continuation, "_identity", return_value=self.identity):
            continuation.run_continuation(self.args, runner=self.runner(calls))
            path = self.args.continuation_dir / "game-02-receipt.json"
            receipt = json.loads(path.read_text())
            receipt["beforeLedgerSha256"] = "tampered"
            write_json(path, receipt)
            with self.assertRaisesRegex(continuation.batch.BatchError, "receipt lineage"):
                continuation.run_continuation(self.args, runner=self.runner(calls))
        self.assertEqual(calls, [2, 3])

    def test_frozen_start_ledger_tamper_refuses_resume(self):
        calls = []
        with patch.object(continuation, "_identity", return_value=self.identity):
            continuation.run_continuation(self.args, runner=self.runner(calls))
            path = self.args.continuation_dir / "manifest.json"
            manifest = json.loads(path.read_text())
            manifest["startLedger"]["requests"] += 1
            write_json(path, manifest)
            with self.assertRaisesRegex(continuation.batch.BatchError,
                                        "starting ledger changed"):
                continuation.run_continuation(self.args, runner=self.runner(calls))
        self.assertEqual(calls, [2, 3])

    def test_runtime_lock_blocks_second_owner(self):
        self.args.runtime_lock.touch(mode=0o600)
        fd = os.open(self.args.runtime_lock, os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with patch.object(continuation, "_identity", return_value=self.identity):
                with self.assertRaisesRegex(continuation.batch.BatchError, "another process owns"):
                    continuation.run_continuation(self.args, runner=self.runner([]))
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def test_identity_reuses_original_runtime_without_new_allowance(self):
        controller = Path(continuation.__file__).resolve().parent.parent
        self.args.gym_dir = self.root / "qualified-gym"
        self.args.engine_dir = self.root / "fixed-engine"
        self.args.gym_dir.mkdir()
        self.args.engine_dir.mkdir()
        self.args.adapter_jar = self.args.gym_dir / "adapter.jar"
        self.args.game_server_jar = self.args.engine_dir / "server.jar"
        self.args.adapter_jar.write_bytes(b"adapter")
        self.args.game_server_jar.write_bytes(b"server")
        qualified = {"catalogClosureSha256": "qualified"}
        original = {
            "manifest": {
                "profiles": list(continuation.batch.PROFILES), "model": "gpt-6-luna",
                "cacheFriendlyHistory": False, "maxAttempts": 2,
                "instanceRoot": str(self.args.instance_root),
                "catalog": str(self.args.catalog), "catalogSha256": sha(self.args.catalog),
                "python": str(self.args.python), "apiKeyFile": str(self.args.api_key_file),
                "ledger": str(self.args.budget_ledger), "runtimeLock": str(self.args.runtime_lock),
                "timeout": 30, "stallSeconds": 10,
                "qualificationPreflight": qualified,
            }
        }
        with patch.object(continuation.batch, "_head", side_effect=lambda path: {
            controller: self.args.expected_control_head,
            self.args.gym_dir: continuation.GYM_HEAD,
            self.args.engine_dir: continuation.ENGINE_HEAD,
        }[path]), patch.object(continuation.batch, "_tracked_clean", return_value=True), \
             patch.object(continuation.batch, "verify_qualified_v7_catalog", return_value=qualified), \
             patch.object(continuation, "SERVER_JAR_SHA256", sha(self.args.game_server_jar)), \
             patch.object(continuation, "ADAPTER_JAR_SHA256", sha(self.args.adapter_jar)):
            identity = continuation._identity(self.args, original)
        self.assertEqual(identity["gameCount"], 2)
        self.assertEqual(identity["firstOriginalGame"], 2)
        self.assertEqual(identity["cumulativeCapUsd"], continuation.CAP_USD)
        self.assertNotIn("incrementalCapUsd", identity)
        self.assertEqual(identity["gymRoot"], str(self.args.gym_dir))

    def test_game_launcher_uses_pinned_gym_checkout_not_controller(self):
        child = SimpleNamespace(pid=12345, wait=lambda timeout=None: 0)
        self.args.before_estimated_usd = self.start["estimatedUsd"]
        with patch.object(continuation.batch.subprocess, "Popen", return_value=child) as popen, \
             patch.object(continuation.batch, "_stop_and_verify_group"):
            continuation.batch.launch_one_game(
                self.args, 2, self.root / "launch.log", self.root / "game-02",
                continuation.START_REQUESTS, continuation.START_UNSETTLED,
                continuation.CAP_USD,
            )
        self.assertEqual(popen.call_args.kwargs["cwd"], self.args.gym_dir)
        self.assertEqual(popen.call_args.kwargs["env"]["PYTHONPATH"], str(self.args.gym_dir))
        self.assertIn("--expected-ledger-estimated-usd", popen.call_args.args[0])
        self.assertEqual(popen.call_args.kwargs["env"]["COMMANDER_GYM_CACHE_FRIENDLY_HISTORY"],
                         "false")

    def test_reviewed_source_drift_between_attempts_stops_before_second(self):
        calls = []
        changed = {**self.identity, "controllerHead": "unreviewed"}
        with patch.object(continuation, "_identity", side_effect=[self.identity, self.identity, changed]):
            with self.assertRaisesRegex(continuation.batch.BatchError, "reviewed source changed"):
                continuation.run_continuation(self.args, runner=self.runner(calls))
        self.assertEqual(calls, [2])
        self.assertEqual(json.loads((self.args.continuation_dir / "cursor.json").read_text())["nextGame"], 3)

    def test_resume_after_qualified_second_game_runs_only_third(self):
        calls = []
        changed = {**self.identity, "controllerHead": "unreviewed"}
        with patch.object(continuation, "_identity", side_effect=[self.identity, self.identity, changed]):
            with self.assertRaisesRegex(continuation.batch.BatchError, "reviewed source changed"):
                continuation.run_continuation(self.args, runner=self.runner(calls))
        with patch.object(continuation, "_identity", return_value=self.identity):
            cursor = continuation.run_continuation(self.args, runner=self.runner(calls))
        self.assertEqual(calls, [2, 3])
        self.assertEqual(cursor["nextGame"], 4)


if __name__ == "__main__":
    unittest.main()
