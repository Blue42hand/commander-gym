from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import run_scripted_paired_screen as screen


class ScriptedPairedScreenTests(unittest.TestCase):
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
