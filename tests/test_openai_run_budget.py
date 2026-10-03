from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from commander_gym.openai_run_budget import OpenAIRunBudget, OpenAIRunBudgetError


class OpenAIRunBudgetTests(unittest.TestCase):
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
