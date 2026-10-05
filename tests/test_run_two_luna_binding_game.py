from pathlib import Path
from tempfile import TemporaryDirectory
from contextlib import redirect_stderr
from io import StringIO
import subprocess
import unittest
from unittest.mock import patch
import venv

from commander_gym.openai_run_budget import OpenAIRunBudget, OpenAIRunBudgetError
from scripts.run_two_luna_binding_game import checked_existing_budget, main, selected_python


class ExistingGameBudgetTests(unittest.TestCase):
    def test_paid_launcher_requires_explicit_v7_qualification_before_setup(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "not-created"
            argv = ["game", "--engine-dir", str(root), "--instance-root", str(root),
                    "--catalog", str(root / "old.json"), "--output-dir", str(output),
                    "--api-key-file", str(root / "unused-key"),
                    "--profile-a", "foundation-a", "--profile-b", "foundation-b"]
            with patch("sys.argv", argv), redirect_stderr(StringIO()), self.assertRaises(SystemExit) as caught:
                main()
            self.assertEqual(caught.exception.code, 2)
            self.assertFalse(output.exists())

    def test_explicit_venv_interpreter_retains_its_environment(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            venv.EnvBuilder(with_pip=False, symlinks=True).create(root / "venv")
            interpreter = root / "venv/bin/python"
            self.assertTrue(interpreter.is_symlink())
            selected = selected_python(root, interpreter)
            self.assertEqual(selected, interpreter)
            result = subprocess.run(
                [str(selected), "-c", "import sys; print(sys.prefix != sys.base_prefix)"],
                capture_output=True, text=True, check=True,
            )
            self.assertEqual(result.stdout.strip(), "True")

    def test_launcher_reuses_elevated_ledger_without_reset_and_rejects_drift(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "ledger.json"
            original = OpenAIRunBudget(path, 5, authorized_max_usd=8,
                                       max_requests=610, initialize_new_ledger=True)
            original.snapshot()
            original.set_request_limit(906)
            original.increase_cap(8)
            snapshot = checked_existing_budget(path, 8, 8, 906, 0, 0)
            self.assertEqual(snapshot["requests"], 0)
            self.assertEqual(snapshot["maxRequests"], 906)
            with self.assertRaisesRegex(RuntimeError, "request count changed"):
                checked_existing_budget(path, 8, 8, 906, 606, 0)
            with self.assertRaisesRegex(RuntimeError, "unsettled reservations changed"):
                checked_existing_budget(path, 8, 8, 906, 0, 3)
            with self.assertRaises(OpenAIRunBudgetError):
                checked_existing_budget(path, 8, 8, 610, 0, 0)


if __name__ == "__main__":
    unittest.main()
