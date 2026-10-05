from pathlib import Path
from tempfile import TemporaryDirectory
from contextlib import redirect_stderr
from io import StringIO
import subprocess
import sys
import unittest
from unittest.mock import patch
import venv

from commander_gym.openai_run_budget import OpenAIRunBudget, OpenAIRunBudgetError
from commander_gym.qualified_v7_preflight import QualifiedV7PreflightError
from scripts.run_two_luna_binding_game import checked_existing_budget, main, selected_python


class ExistingGameBudgetTests(unittest.TestCase):
    def test_dry_run_initializes_zero_spend_ledger_before_services(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "dry-run"
            argv = ["game", "--engine-dir", str(root), "--instance-root", str(root),
                    "--catalog", str(root / "rosters/v7.json"),
                    "--output-dir", str(output), "--profile-a", "krenko",
                    "--profile-b", "talrand", "--qualified-v7-comparison",
                    "--dry-run", "--python", sys.executable]
            with patch("sys.argv", argv), patch(
                "scripts.run_two_luna_binding_game.stage_qualified_v7_catalog",
                side_effect=QualifiedV7PreflightError("stop before services"),
            ), patch("scripts.run_two_luna_binding_game.subprocess.Popen") as start_process, \
                    self.assertRaises(QualifiedV7PreflightError):
                main()
            ledger = OpenAIRunBudget(output / "openai-budget.json", 0.000000001,
                                     authorized_max_usd=5).snapshot()
            self.assertEqual(ledger["requests"], 0)
            self.assertEqual(ledger["estimatedUsd"], 0)
            start_process.assert_not_called()

    def test_paid_launcher_qualifies_effective_max_attempts_before_credentials(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            argv = ["game", "--engine-dir", str(root), "--instance-root", str(root),
                    "--catalog", str(root / "rosters/v7.json"),
                    "--output-dir", str(root / "out"), "--api-key-file", str(root / "unused-key"),
                    "--profile-a", "krenko", "--profile-b", "talrand",
                    "--qualified-v7-comparison", "--max-attempts", "3",
                    "--python", sys.executable]
            with patch("sys.argv", argv), patch(
                "scripts.run_two_luna_binding_game.stage_qualified_v7_catalog",
                side_effect=QualifiedV7PreflightError("v7 runtime config digest changed"),
            ) as stage, patch("scripts.run_two_luna_binding_game.read_key") as read_key, patch(
                "scripts.run_two_luna_binding_game.subprocess.Popen"
            ) as start_process, self.assertRaisesRegex(
                QualifiedV7PreflightError, "runtime config digest changed"
            ):
                main()
            self.assertEqual(stage.call_args.args[-1], 3)
            read_key.assert_not_called()
            start_process.assert_not_called()

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
