from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from commander_gym.openai_run_budget import OpenAIRunBudget, OpenAIRunBudgetError
from scripts.run_two_luna_binding_game import checked_existing_budget


class ExistingGameBudgetTests(unittest.TestCase):
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
