from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
import unittest
from unittest.mock import patch

from scripts import run_scripted_paired_screen as screen


class ScriptedPairedScreenTests(unittest.TestCase):
    def test_relative_path_is_rejected_before_private_data_or_provider(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = ["screen", "--capture-dir", str(root), "--corpus-dir", str(root),
                    "--instance-root", str(root), "--catalog", str(root / "catalog"),
                    "--ledger", "relative-ledger", "--output", str(root / "output")]
            with patch("sys.argv", args), patch.object(screen, "_load_reviewed") as load:
                with self.assertRaisesRegex(ValueError, "all paths must be absolute"):
                    screen.main()
                load.assert_not_called()

    def test_concurrent_initial_starts_share_whole_screen_lock(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            ledger = root / "ledger.json"
            entered, release = Event(), Event()
            calls = []

            def fake_run(args):
                calls.append(args.output)
                entered.set()
                self.assertTrue(release.wait(5))
                return 0

            def argv(output):
                return ["screen", "--capture-dir", str(root), "--corpus-dir", str(root),
                        "--instance-root", str(root), "--catalog", str(root / "catalog"),
                        "--ledger", str(ledger), "--output", str(output), "--execute"]

            with patch.object(screen, "_run", side_effect=fake_run):
                with patch("sys.argv", argv(root / "first.jsonl")):
                    first = Thread(target=screen.main)
                    first.start()
                    try:
                        self.assertTrue(entered.wait(5))
                        with patch("sys.argv", argv(root / "second.jsonl")):
                            with self.assertRaisesRegex(ValueError, "another scripted paired screen"):
                                screen.main()
                        self.assertEqual(calls, [root / "first.jsonl"])
                    finally:
                        release.set()
                        first.join(5)
            self.assertFalse(first.is_alive())

    def test_rejects_quarantined_or_changed_source_before_reading_private_data(self):
        with TemporaryDirectory() as temporary:
            manifest = json.loads(screen.MANIFEST.read_text())
            path = Path(temporary) / "manifest.json"
            with patch.object(screen, "MANIFEST", path):
                for digest in (screen.QUARANTINED_TRACE, "0" * 64):
                    manifest["sourceSetSha256"] = digest
                    path.write_text(json.dumps(manifest))
                    with self.assertRaisesRegex(ValueError, "reviewed manifest"):
                        screen._load_reviewed(Path(temporary), Path(temporary),
                                              Path(temporary), Path(temporary) / "missing-catalog")

    def test_ledger_guard_preserves_absolute_ceiling_and_cumulative_count(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "ledger.json"
            manifest = json.loads(screen.MANIFEST.read_text())
            valid = {"schemaVersion": 1, "requests": 590, "maxRequests": 610,
                     "capUsd": 6, "estimatedUsd": 5, "unsettledRequests": 3}
            path.write_text(json.dumps(valid))
            self.assertEqual(screen._read_ledger(path, manifest)["requests"], 590)
            for changed in ({**valid, "requests": 0}, {**valid, "requests": 591},
                            {**valid, "maxRequests": 626}, {**valid, "capUsd": 5}):
                path.write_text(json.dumps(changed))
                with self.assertRaisesRegex(ValueError, "590/610"):
                    screen._read_ledger(path, manifest)


if __name__ == "__main__":
    unittest.main()
