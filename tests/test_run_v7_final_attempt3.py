"""Offline lineage and single-use checks for the final unplayed v7 slot."""

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

from commander_gym.cache_probe_session import _private_json
from commander_gym.openai_run_budget import OpenAIRunBudget
from scripts import run_v7_batch_continuation as prior
from scripts import run_v7_final_attempt3 as final


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class V7FinalAttemptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.original = root / "original"
        self.continuation = root / "continuation"
        self.original.mkdir(mode=0o700)
        self.continuation.mkdir(mode=0o700)
        self.ledger = root / "ledger.json"
        self.start = {
            "schemaVersion": 1, "capUsd": final.CAP_USD,
            "maxRequests": final.MAX_REQUESTS, "estimatedUsd": prior.START_USD,
            "requests": prior.START_REQUESTS, "inputTokens": 100,
            "outputTokens": 20, "unsettledRequests": 3,
        }
        self.after = {**self.start, "estimatedUsd": final.ESTIMATED_USD,
                      "requests": final.REQUESTS, "inputTokens": 500,
                      "outputTokens": 80}
        _private_json(self.ledger, self.after)
        OpenAIRunBudget(self.ledger, final.CAP_USD,
                        authorized_max_usd=final.CAP_USD,
                        max_requests=final.MAX_REQUESTS).snapshot()
        self.after = json.loads(self.ledger.read_text())

        _private_json(self.original / "manifest.json", {
            "gymHead": prior.GYM_HEAD, "cumulativeCapUsd": final.CAP_USD,
            "maxRequests": final.MAX_REQUESTS,
        })
        _private_json(self.original / "game-01-receipt.json", {
            "index": 1, "qualified": False,
            "stopReasons": ["first game stopped"], "afterLedger": self.start,
        })
        _private_json(self.original / "cursor.json", {
            "status": "stopped", "nextGame": 1, "reason": ["first game stopped"],
        })
        original_hashes = {name: digest(self.original / name)
                           for name in prior.ORIGINAL_HASHES}
        self.patches = [patch.object(prior, "ORIGINAL_BATCH_DIR", self.original),
                        patch.object(prior, "ORIGINAL_HASHES", original_hashes),
                        patch.object(prior, "START_LEDGER_SHA256", "a" * 64),
                        patch.object(final, "CONTINUATION_DIR", self.continuation),
                        patch.object(final, "FINAL_DIR", self.continuation / "game-03-final"),
                        patch.object(final, "FINAL_CLAIM", self.continuation / "game-03-final-claim.json"),
                        patch.object(final, "LEDGER_SHA256", digest(self.ledger))]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        claim = prior._claim_path()
        _private_json(claim, prior._claim_value(self.continuation))
        self.claim_hash = digest(claim)
        self.p = patch.object(final, "CONTINUATION_CLAIM_SHA256", self.claim_hash)
        self.p.start()
        self.addCleanup(self.p.stop)
        _private_json(self.continuation / "manifest.json", {
            "originalBatchDir": str(self.original), "claimPath": str(claim),
            "claimSha256": self.claim_hash, "gymHead": prior.GYM_HEAD,
            "engineHead": prior.ENGINE_HEAD, "startLedger": self.start,
            "startLedgerSha256": prior.START_LEDGER_SHA256,
            "cumulativeCapUsd": final.CAP_USD, "maxRequests": final.MAX_REQUESTS,
        })
        _private_json(self.continuation / "game-02-receipt.json", {
            "index": 2, "qualified": False, "beforeLedger": self.start,
            "beforeLedgerSha256": prior.START_LEDGER_SHA256,
            "afterLedger": self.after, "afterLedgerSha256": final.LEDGER_SHA256,
            "priorReceiptSha256": None, "stopReasons": ["native rejection"],
        })
        receipt_hash = digest(self.continuation / "game-02-receipt.json")
        _private_json(self.continuation / "cursor.json", {
            "status": "stopped", "nextGame": 2, "reason": ["native rejection"],
            "lastReceiptSha256": receipt_hash,
        })
        hashes = {name: digest(self.continuation / name)
                  for name in final.CONTINUATION_HASHES}
        self.p = patch.object(final, "CONTINUATION_HASHES", hashes)
        self.p.start()
        self.addCleanup(self.p.stop)
        self.original_hashes = original_hashes
        self.continuation_hashes = hashes
        self.key = root / "key.env"
        self.key.write_text("unused offline key fixture")
        self.catalog = root / "catalog.json"
        self.catalog.write_text("{}")
        self.server_jar = root / "server.jar"
        self.server_jar.write_bytes(b"server")
        self.adapter_jar = root / "adapter.jar"
        self.adapter_jar.write_bytes(b"adapter")
        self.args = Namespace(
            gym_dir=root, engine_dir=root, instance_root=root,
            catalog=self.catalog, python=Path(sys.executable),
            api_key_file=self.key, budget_ledger=self.ledger,
            runtime_lock=root / "runtime.lock",
            game_server_jar=self.server_jar, adapter_jar=self.adapter_jar,
            expected_control_head="reviewed-controller",
            expected_gym_head=final.GYM_HEAD,
            expected_engine_head=final.ENGINE_HEAD,
            expected_game_server_jar_sha256=digest(self.server_jar),
            expected_adapter_jar_sha256=digest(self.adapter_jar),
            expected_runtime_classpath_sha256="f" * 64,
            timeout=30, stall_seconds=10, max_requests=final.MAX_REQUESTS,
        )
        self.identity = {
            "controllerHead": "reviewed-controller", "gymHead": final.GYM_HEAD,
            "engineHead": final.ENGINE_HEAD,
            "qualificationPreflight": {"catalogClosureSha256": "qualified"},
            "cumulativeCapUsd": final.CAP_USD,
            "maxRequests": final.MAX_REQUESTS,
        }

    def runner(self, calls: list[int], *, qualified: bool = True):
        def run(args, index, log, output_dir, expected_requests, expected_unsettled, cap):
            calls.append(index)
            self.assertEqual((index, expected_requests, expected_unsettled, cap),
                             (3, final.REQUESTS, 3, final.CAP_USD))
            budget = OpenAIRunBudget(self.ledger, cap, authorized_max_usd=cap,
                                     max_requests=final.MAX_REQUESTS)
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
                terminal[key] = digest(path)
            result = {
                "providerRequests": 1, "recordedGameApiRequests": 1,
                "provenanceFinalizationComplete": True,
                "fourSeatProvenanceComplete": True, "expectedSeats": 4,
                "singlePodGame": True, "completed": True,
                "stopReason": "native_complete", "terminalEvidence": {"nativeGameOver": True},
                "terminalArtifact": terminal, "validationRetries": 0,
                "technicalQualified": qualified,
                "budget": {"gameApiRequests": 1, "gameInputTokens": 20,
                           "gameOutputTokens": 1, "cumulativeApiRequests": after["requests"],
                           "unsettledRequests": after["unsettledRequests"], "capUsd": cap},
            }
            log.write_text(
                "TWO_LUNA_QUALIFICATION_PREFLIGHT "
                + json.dumps(self.identity["qualificationPreflight"]) + "\n"
                + "RUN_ARTIFACTS=" + str(game) + "\n"
                + "TWO_LUNA_DEBUG_RESULT=" + json.dumps(result) + "\n"
            )
            return 0 if qualified else 1
        return run

    def run_final(self, calls: list[int], *, qualified: bool = True):
        with patch.object(final, "_identity", return_value=self.identity):
            return final.run_final(self.args, runner=self.runner(calls, qualified=qualified))

    def test_one_game_uses_existing_caps_and_preserves_both_stopped_lineages(self):
        calls = []
        cursor = self.run_final(calls)
        self.assertEqual(calls, [3])
        self.assertEqual(cursor["status"], "completed")
        self.assertEqual(cursor["originalGame"], 3)
        self.assertEqual(json.loads((final.FINAL_DIR / "game-03-receipt.json").read_text())
                         ["priorReceiptSha256"], self.continuation_hashes["game-02-receipt.json"])
        for directory, hashes in ((self.original, self.original_hashes),
                                  (self.continuation, self.continuation_hashes)):
            self.assertEqual({name: digest(directory / name) for name in hashes}, hashes)
        after = json.loads(self.ledger.read_text())
        self.assertEqual((after["capUsd"], after["maxRequests"]),
                         (final.CAP_USD, final.MAX_REQUESTS))
        with patch.object(final, "_identity", return_value=self.identity):
            with self.assertRaisesRegex(final.batch.BatchError, "already claimed"):
                final.run_final(self.args, runner=self.runner(calls))
            with patch.object(final, "FINAL_DIR", self.continuation / "alternate-final-dir"):
                with self.assertRaisesRegex(final.batch.BatchError, "already claimed"):
                    final.run_final(self.args, runner=self.runner(calls))
        self.assertEqual(calls, [3])

    def test_failed_game_stops_without_replay_or_new_budget(self):
        calls = []
        cursor = self.run_final(calls, qualified=False)
        self.assertEqual(cursor["status"], "stopped")
        with patch.object(final, "_identity", return_value=self.identity):
            with self.assertRaisesRegex(final.batch.BatchError, "already claimed"):
                final.run_final(self.args, runner=self.runner(calls))
        self.assertEqual(calls, [3])

    def test_stopped_receipt_drift_refuses_before_claim(self):
        calls = []
        (self.continuation / "game-02-receipt.json").write_bytes(b"changed\n")
        with patch.object(final, "_identity", return_value=self.identity):
            with self.assertRaisesRegex(final.batch.BatchError, "hash changed"):
                final.run_final(self.args, runner=self.runner(calls))
        self.assertEqual(calls, [])
        self.assertFalse(final.FINAL_CLAIM.exists())

    def test_prior_game03_artifact_refuses_even_with_another_final_output_path(self):
        calls = []
        (self.continuation / "game-03").mkdir()
        with patch.object(final, "FINAL_DIR", self.continuation / "alternate-output"), \
             patch.object(final, "_identity", return_value=self.identity):
            with self.assertRaisesRegex(final.batch.BatchError, "game-03 already has artifacts"):
                final.run_final(self.args, runner=self.runner(calls))
        self.assertEqual(calls, [])

    def test_ledger_drift_refuses_before_claim(self):
        calls = []
        self.ledger.write_bytes(self.ledger.read_bytes() + b" ")
        with patch.object(final, "_identity", return_value=self.identity):
            with self.assertRaisesRegex(final.batch.BatchError, "ledger hash changed"):
                final.run_final(self.args, runner=self.runner(calls))
        self.assertEqual(calls, [])
        self.assertFalse(final.FINAL_CLAIM.exists())

    def test_lock_refuses_another_owner(self):
        fd = os.open(self.args.runtime_lock, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with patch.object(final, "_identity", return_value=self.identity):
                with self.assertRaisesRegex(final.batch.BatchError, "another process owns"):
                    final.run_final(self.args, runner=self.runner([]))
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def test_interruption_keeps_inflight_and_parent_evidence(self):
        calls = []
        def interrupted(*_):
            calls.append(3)
            raise InterruptedError("dummy launcher interrupted")
        with patch.object(final, "_identity", return_value=self.identity):
            with self.assertRaisesRegex(InterruptedError, "interrupted"):
                final.run_final(self.args, runner=interrupted)
            with self.assertRaisesRegex(final.batch.BatchError, "already claimed"):
                final.run_final(self.args, runner=self.runner(calls))
        self.assertEqual(calls, [3])
        self.assertEqual(json.loads((final.FINAL_DIR / "cursor.json").read_text())["status"],
                         "inflight")
        self.assertEqual({name: digest(self.continuation / name)
                          for name in self.continuation_hashes}, self.continuation_hashes)

    def test_launcher_interruption_runs_cleanup_while_runtime_lock_is_held(self):
        calls = []
        class InterruptedChild:
            pid = 12345
            def wait(self, timeout=None):
                raise InterruptedError("dummy launcher interrupted")

        def cleanup(_child):
            fd = os.open(self.args.runtime_lock, os.O_RDWR)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(fd)
            calls.append("cleanup")

        def interrupted(args, index, log, output_dir, requests, unsettled, cap):
            calls.append(index)
            return final.batch.launch_one_game(args, index, log, output_dir,
                                               requests, unsettled, cap)

        with patch.object(final, "_identity", return_value=self.identity), \
             patch.object(final.batch.subprocess, "Popen", return_value=InterruptedChild()), \
             patch.object(final.batch, "_stop_and_verify_group", side_effect=cleanup):
            with self.assertRaisesRegex(InterruptedError, "interrupted"):
                final.run_final(self.args, runner=interrupted)
        self.assertEqual(calls, [3, "cleanup"])
        self.assertEqual(json.loads((final.FINAL_DIR / "cursor.json").read_text())["status"],
                         "inflight")

    def test_runtime_identity_binds_repaired_source_and_effective_classpath(self):
        base = {
            "profiles": list(final.batch.PROFILES), "model": "gpt-6-luna",
            "cacheFriendlyHistory": False, "maxAttempts": 2,
            "instanceRoot": str(self.args.instance_root),
            "catalog": str(self.args.catalog), "catalogSha256": digest(self.args.catalog),
            "python": str(self.args.python), "apiKeyFile": str(self.args.api_key_file),
            "ledger": str(self.args.budget_ledger), "runtimeLock": str(self.args.runtime_lock),
            "timeout": self.args.timeout, "stallSeconds": self.args.stall_seconds,
            "qualificationPreflight": self.identity["qualificationPreflight"],
            "gymHead": final.GYM_HEAD, "engineHead": final.ENGINE_HEAD,
            "incrementalCapUsd": 10.0,
        }
        lineage = {"original": {"manifest": base}}
        with patch.object(final.batch, "_head", return_value=self.args.expected_control_head), \
             patch.object(final.batch, "_tracked_clean", return_value=True), \
             patch.object(final.batch, "_identity", return_value=base), \
             patch.object(final.runtime_probe, "fingerprint", return_value={
                 "sha256": self.args.expected_runtime_classpath_sha256, "entryCount": 3}):
            identity = final._identity(self.args, lineage)
            self.assertEqual(identity["runtimeClasspath"]["sha256"], "f" * 64)
            self.assertEqual(identity["gameCount"], 1)
            self.assertEqual(identity["originalGame"], 3)
            self.assertNotIn("incrementalCapUsd", identity)
            self.args.expected_runtime_classpath_sha256 = "e" * 64
            with self.assertRaisesRegex(final.batch.BatchError, "classpath differs"):
                final._identity(self.args, lineage)

    def test_parent_artifact_changes_during_game_leave_inflight(self):
        calls = []
        def changed(*_):
            calls.append(3)
            (self.continuation / "cursor.json").write_bytes(b"changed\n")
            return 0
        with patch.object(final, "_identity", return_value=self.identity):
            with self.assertRaisesRegex(final.batch.BatchError, "hash changed"):
                final.run_final(self.args, runner=changed)
        self.assertEqual(calls, [3])
        self.assertEqual(json.loads((final.FINAL_DIR / "cursor.json").read_text())["status"],
                         "inflight")


if __name__ == "__main__":
    unittest.main()
