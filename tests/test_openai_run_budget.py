from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from commander_gym.openai_run_budget import OpenAIRunBudget, OpenAIRunBudgetError


class OpenAIRunBudgetTests(unittest.TestCase):
    def test_cap_increase_is_explicit_monotonic_and_preserves_ledger(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "budget.json"
            with self.assertRaises(ValueError):
                OpenAIRunBudget(path, 6)
            with self.assertRaisesRegex(OpenAIRunBudgetError, "new ledger"):
                OpenAIRunBudget(path, 6, authorized_max_usd=6).snapshot()

            budget = OpenAIRunBudget(path, 5, authorized_max_usd=6)
            budget.snapshot()
            def failure(**_request):
                raise RuntimeError("ambiguous provider outcome")
            with self.assertRaises(RuntimeError):
                budget.create(failure, self.request())
            before = budget.snapshot()
            with self.assertRaises(ValueError):
                budget.increase_cap(6.01)
            after = budget.increase_cap(6)
            self.assertEqual(after["capUsd"], 6)
            for key in ("requests", "estimatedUsd", "unsettledRequests", "inputTokens", "outputTokens"):
                self.assertEqual(after[key], before[key])
            with self.assertRaisesRegex(OpenAIRunBudgetError, "cap does not match"):
                OpenAIRunBudget(path, 5).snapshot()
            self.assertEqual(OpenAIRunBudget(path, 6, authorized_max_usd=6).snapshot(), after)

    def test_absolute_request_limit_counts_ambiguous_attempts_across_restarts(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "budget.json"
            budget = OpenAIRunBudget(path, 5, max_requests=1)
            calls = []
            def failure(**request):
                calls.append(request)
                raise RuntimeError("ambiguous provider outcome")
            with self.assertRaises(RuntimeError):
                budget.create(failure, self.request())
            restarted = OpenAIRunBudget(path, 5, max_requests=1)
            with self.assertRaisesRegex(OpenAIRunBudgetError, "absolute request limit"):
                restarted.create(failure, self.request())
            self.assertEqual(len(calls), 1)
            self.assertEqual(restarted.snapshot()["unsettledRequests"], 1)

    def request(self, input_text="small input"):
        return {
            "model": "gpt-6-luna",
            "input": input_text,
            "max_output_tokens": OpenAIRunBudget.MAX_OUTPUT_TOKENS,
        }

    def test_settled_usage_and_failures_share_persistent_cap(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "budget.json"
            budget = OpenAIRunBudget(path, 0.01)
            calls = []

            def success(**request):
                calls.append(request)
                return SimpleNamespace(usage=SimpleNamespace(input_tokens=20, output_tokens=10))

            budget.create(success, self.request())
            first = budget.snapshot()
            self.assertEqual(first["requests"], 1)
            self.assertEqual(first["inputTokens"], 20)
            self.assertEqual(first["outputTokens"], 10)
            self.assertEqual(first["unsettledRequests"], 0)
            self.assertEqual(len(calls), 1)

            def failure(**_request):
                raise RuntimeError("transport failed after dispatch")

            with self.assertRaisesRegex(RuntimeError, "transport failed"):
                OpenAIRunBudget(path, 0.01).create(failure, self.request())
            second = OpenAIRunBudget(path, 0.01).snapshot()
            self.assertEqual(second["requests"], 2)
            self.assertEqual(second["unsettledRequests"], 1)
            self.assertGreater(second["estimatedUsd"], first["estimatedUsd"])
            self.assertEqual(json.loads(path.read_text())["requests"], 2)

    def test_rejects_request_before_dispatch_when_cumulative_cap_exceeded(self):
        with TemporaryDirectory() as temporary:
            budget = OpenAIRunBudget(Path(temporary) / "budget.json", 0.00001)
            calls = []
            with self.assertRaisesRegex(OpenAIRunBudgetError, "cumulative run cap"):
                budget.create(lambda **request: calls.append(request), self.request("x" * 10000))
            self.assertEqual(calls, [])
            self.assertEqual(budget.snapshot()["requests"], 0)

    def test_rejects_model_or_missing_output_cap_before_dispatch(self):
        with TemporaryDirectory() as temporary:
            budget = OpenAIRunBudget(Path(temporary) / "budget.json", 5)
            calls = []
            for request in (
                {**self.request(), "model": "another-model"},
                {**self.request(), "max_output_tokens": None},
            ):
                with self.assertRaises(OpenAIRunBudgetError):
                    budget.create(lambda **kwargs: calls.append(kwargs), request)
            self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
