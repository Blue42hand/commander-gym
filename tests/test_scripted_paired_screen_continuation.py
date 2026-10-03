from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import run_scripted_paired_screen_continuation as continuation


class ScriptedPairedContinuationTests(unittest.TestCase):
    def test_legal_semantic_difference_is_flagged_for_review(self):
        self.assertEqual(continuation._manifest()["semanticDivergencePolicy"], "record_and_continue")
        self.assertFalse(continuation._pair_review_flag(
            {"channel": "action", "semanticId": "same"},
            {"channel": "action", "semanticId": "same"},
        ))
        self.assertTrue(continuation._pair_review_flag(
            {"channel": "decision", "response": {"selectedCards": ["a"]}},
            {"channel": "decision", "response": {"selectedCards": ["b"]}},
        ))

    def test_reviewed_manifest_rejects_changed_source_or_bounds(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "continuation.json"
            original = json.loads(continuation.MANIFEST.read_text())
            with patch.object(continuation, "MANIFEST", path):
                for change in ({"sourceSetSha256": "0" * 64},
                               {"startPosition": 0}, {"maxNewRequests": 16}):
                    path.write_text(json.dumps({**original, **change}))
                    with self.assertRaisesRegex(ValueError, "manifest changed"):
                        continuation._manifest()

    def test_ledger_must_retain_596_requests_and_three_old_reservations(self):
        manifest = continuation._manifest()
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "ledger.json"
            valid = {"schemaVersion": 1, "requests": 596, "maxRequests": 610,
                     "capUsd": 6, "estimatedUsd": 5.0120125, "unsettledRequests": 3}
            path.write_text(json.dumps(valid))
            self.assertEqual(continuation._verify_ledger(path, manifest)["requests"], 596)
            for changed in ({**valid, "requests": 590}, {**valid, "requests": 597},
                            {**valid, "maxRequests": 626},
                            {**valid, "unsettledRequests": 2}):
                path.write_text(json.dumps(changed))
                with self.assertRaisesRegex(ValueError, "596/610"):
                    continuation._verify_ledger(path, manifest)

    def test_already_existing_output_refuses_replay_before_source_or_budget(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            os.chmod(root, 0o700)
            output = root / "continuation.jsonl"
            output.write_text("previous attempt")
            args = ["continuation", "--capture-dir", str(root), "--corpus-dir", str(root),
                    "--instance-root", str(root), "--catalog", str(root / "catalog"),
                    "--ledger", str(root / "ledger"), "--prior-results", str(root / "prior"),
                    "--output", str(output), "--execute"]
            with patch("sys.argv", args):
                with self.assertRaisesRegex(ValueError, "no replay"):
                    continuation.main()

    def test_changed_prior_receipt_is_rejected_before_model_io(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            os.chmod(root, 0o700)
            path = root / continuation._manifest()["priorResultsName"]
            path.write_text("altered\n")
            with self.assertRaisesRegex(ValueError, "six-call receipt changed"):
                continuation._verify_prior(path, continuation._manifest(), {})

    def test_whole_screen_lock_refuses_simultaneous_continuation(self):
        with TemporaryDirectory() as temporary:
            ledger = Path(temporary) / "budget.json"
            with continuation._exclusive_screen_lock(ledger):
                with self.assertRaisesRegex(ValueError, "another scripted paired screen"):
                    with continuation._exclusive_screen_lock(ledger):
                        pass
            with continuation._exclusive_screen_lock(ledger):
                pass


if __name__ == "__main__":
    unittest.main()
