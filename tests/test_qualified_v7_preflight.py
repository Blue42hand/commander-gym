import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from commander_gym import qualified_v7_preflight as qualified


class QualifiedV7PreflightTests(unittest.TestCase):
    def test_old_catalog_path_fails_before_any_provider_or_file_read(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(qualified.QualifiedV7PreflightError, "exact roster path"):
                qualified.verify_qualified_v7_catalog(
                    root, root / "rosters/foundation.json", "foundation-a", "foundation-b",
                )

    def test_closure_hash_uses_exact_relative_paths_and_file_bytes(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "rosters").mkdir()
            (root / "bindings").mkdir()
            (root / "rosters/test.json").write_text("{}")
            (root / "bindings/a.json").write_text("first")
            with patch.object(qualified, "ROSTER_PATH", Path("rosters/test.json")), patch.object(
                qualified, "_closure_paths", return_value={
                    Path("rosters/test.json"), Path("bindings/a.json"),
                },
            ):
                first = qualified._closure(root, {})
                (root / "bindings/a.json").write_text("second")
                self.assertNotEqual(first, qualified._closure(root, {}))
                (root / "bindings/a.json").write_text("first")
                self.assertEqual(first, qualified._closure(root, {}))

    def test_stage_copies_only_reviewed_closure_into_private_run(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "source"
            run = Path(temporary) / "run"
            (root / "rosters").mkdir(parents=True)
            (root / "bindings").mkdir()
            run.mkdir()
            roster = root / "rosters/test.json"
            roster.write_text(json.dumps({"bindings": ["bindings/a.json"]}))
            (root / "bindings/a.json").write_text("reviewed")
            (root / "unlisted-secret").write_text("do not copy")
            receipt = {"catalogClosureSha256": "approved"}
            with patch.object(qualified, "ROSTER_PATH", Path("rosters/test.json")), patch.object(
                qualified, "_closure_paths", return_value={
                    Path("rosters/test.json"), Path("bindings/a.json"),
                },
            ), patch.object(qualified, "verify_qualified_v7_catalog", return_value=receipt) as verify:
                staged, catalog, result = qualified.stage_qualified_v7_catalog(
                    root, roster, run, "a", "b",
                )
            self.assertEqual(result, receipt)
            self.assertEqual(verify.call_count, 2)
            self.assertEqual(catalog, staged / "rosters/test.json")
            self.assertEqual((staged / "bindings/a.json").read_text(), "reviewed")
            self.assertFalse((staged / "unlisted-secret").exists())
            self.assertEqual((staged / "bindings/a.json").stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
