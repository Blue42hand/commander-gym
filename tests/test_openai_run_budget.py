from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from commander_gym.openai_run_budget import OpenAIRunBudget, OpenAIRunBudgetError


class OpenAIRunBudgetTests(unittest.TestCase):
    def test_cache_usage_prices_reads_writes_and_long_context(self):
        expected = 2.2 * (300 * 0.10 + 400 * 0.01 + 300 * 0.125 + 100 * 0.50) / 1_000_000
        self.assertAlmostEqual(OpenAIRunBudget._cost(
            1000, 100, cached_tokens=400, cache_write_tokens=300,
        ), expected)
        long_expected = 2.2 * (272001 * 0.25 + 100 * 0.75) / 1_000_000
        self.assertAlmostEqual(OpenAIRunBudget._cost(
            272001, 100, cache_write_tokens=272001,
        ), long_expected)
        with self.assertRaisesRegex(ValueError, "exceed total input"):
            OpenAIRunBudget._cost(100, 10, cached_tokens=80, cache_write_tokens=30)

    def test_cache_usage_settlement_and_strict_missing_details(self):
        with TemporaryDirectory() as temporary:
            budget = OpenAIRunBudget(
                Path(temporary) / "budget.json", 5,
                initialize_new_ledger=True, require_cache_usage_details=True,
            )
            response = SimpleNamespace(usage={
                "input_tokens": 1000, "output_tokens": 100,
                "input_tokens_details": {"cached_tokens": 400, "cache_write_tokens": 300},
            })
            budget.create(lambda **_request: response, self.request())
            snapshot = budget.snapshot()
            self.assertAlmostEqual(snapshot["estimatedUsd"], OpenAIRunBudget._cost(
                1000, 100, cached_tokens=400, cache_write_tokens=300,
            ))
            self.assertEqual(snapshot["unsettledRequests"], 0)

            missing = SimpleNamespace(usage={"input_tokens": 1000, "output_tokens": 100})
            with self.assertRaisesRegex(OpenAIRunBudgetError, "cache usage details") as error:
                budget.create(lambda **_request: missing, self.request())
            self.assertTrue(error.exception.dispatched)
            snapshot = budget.snapshot()
            self.assertEqual(snapshot["requests"], 2)
            self.assertEqual(snapshot["unsettledRequests"], 0)
            self.assertAlmostEqual(snapshot["estimatedUsd"],
                OpenAIRunBudget._cost(1000, 100, cached_tokens=400, cache_write_tokens=300)
                + OpenAIRunBudget._cost(1000, 100, cache_write_tokens=1000))

    def test_negative_provider_usage_keeps_dispatched_response_and_reservation(self):
        for input_tokens, output_tokens in ((-1, 10), (10, -1)):
            with self.subTest(input_tokens=input_tokens, output_tokens=output_tokens):
                with TemporaryDirectory() as temporary:
                    budget = OpenAIRunBudget(
                        Path(temporary) / "budget.json", 5,
                        initialize_new_ledger=True, require_cache_usage_details=True,
                    )
                    response = SimpleNamespace(usage={
                        "input_tokens": input_tokens, "output_tokens": output_tokens,
                        "input_tokens_details": {
                            "cached_tokens": 0, "cache_write_tokens": 0,
                        },
                    })
                    with self.assertRaisesRegex(OpenAIRunBudgetError, "token usage") as error:
                        budget.create(lambda **_request: response, self.request())
                    self.assertTrue(error.exception.dispatched)
                    self.assertIs(error.exception.response, response)
                    snapshot = budget.snapshot()
                    self.assertEqual(snapshot["requests"], 1)
                    self.assertEqual(snapshot["unsettledRequests"], 1)
                    self.assertGreater(snapshot["estimatedUsd"], 0)

    def test_session_subcap_rejects_before_dispatch_without_changing_shared_cap(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "budget.json"
            budget = OpenAIRunBudget(
                path, 5, max_requests=10, initialize_new_ledger=True,
                session_cap_usd=0.001, session_max_requests=1,
            )
            calls = []
            with self.assertRaisesRegex(OpenAIRunBudgetError, "session cap"):
                budget.create(lambda **request: calls.append(request), self.request())
            self.assertEqual(calls, [])
            self.assertEqual(budget.snapshot()["capUsd"], 5)
            budget = OpenAIRunBudget(
                path, 5, max_requests=10, session_max_requests=0,
            )
            with self.assertRaisesRegex(OpenAIRunBudgetError, "session request limit"):
                budget.create(lambda **request: calls.append(request), self.request())
            self.assertEqual(budget.snapshot()["requests"], 0)

    def test_existing_ledger_is_required_by_default_and_legacy_v1_loads(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "budget.json"
            with self.assertRaisesRegex(OpenAIRunBudgetError, "missing or empty"):
                OpenAIRunBudget(path, 5).snapshot()
            path.write_text("")
            with self.assertRaisesRegex(OpenAIRunBudgetError, "missing or empty"):
                OpenAIRunBudget(path, 5).snapshot()
            path.write_text(json.dumps({
                "schemaVersion": 1, "capUsd": 5, "estimatedUsd": 0.7,
                "requests": 42, "inputTokens": 100, "outputTokens": 10,
                "unsettledRequests": 1,
            }))
            self.assertEqual(OpenAIRunBudget(path, 5).snapshot()["requests"], 42)
            path.unlink()
            with self.assertRaisesRegex(OpenAIRunBudgetError, "missing or empty"):
                OpenAIRunBudget(path, 5).snapshot()

    def test_explicit_initialization_rejects_existing_empty_ledger(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "budget.json"
            path.write_text("")
            with self.assertRaisesRegex(OpenAIRunBudgetError, "missing or empty"):
                OpenAIRunBudget(path, 5, initialize_new_ledger=True).snapshot()

    def test_invalid_existing_counters_fail_closed(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "budget.json"
            base = OpenAIRunBudget(path, 5, initialize_new_ledger=True).snapshot()
            for field, invalid in (
                ("requests", -1), ("unsettledRequests", 1),
                ("estimatedUsd", float("nan")),
            ):
                corrupted = {**base, field: invalid}
                path.write_text(json.dumps(corrupted))
                with self.assertRaises(OpenAIRunBudgetError):
                    OpenAIRunBudget(path, 5).snapshot()

    def test_durable_request_limit_requires_explicit_locked_change(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "budget.json"
            budget = OpenAIRunBudget(path, 5, authorized_max_usd=6, initialize_new_ledger=True)
            budget.snapshot()
            with self.assertRaisesRegex(OpenAIRunBudgetError, "request limit does not match"):
                OpenAIRunBudget(path, 5, max_requests=610).snapshot()
            installed = budget.set_request_limit(610)
            self.assertEqual(installed["maxRequests"], 610)
            self.assertEqual(budget.max_requests, 610)
            with self.assertRaisesRegex(OpenAIRunBudgetError, "request limit does not match"):
                OpenAIRunBudget(path, 5, max_requests=611).snapshot()
            changed = budget.set_request_limit(611)
            self.assertEqual(changed["maxRequests"], 611)
            with self.assertRaisesRegex(OpenAIRunBudgetError, "request limit does not match"):
                OpenAIRunBudget(path, 5, max_requests=610).snapshot()
            self.assertEqual(OpenAIRunBudget(path, 5, max_requests=611).snapshot(), changed)

    def test_atomic_replacement_keeps_old_ledger_on_interrupted_write(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "budget.json"
            budget = OpenAIRunBudget(path, 5, initialize_new_ledger=True)
            before = budget.snapshot()
            original = path.read_bytes()
            with patch("commander_gym.openai_run_budget.os.replace", side_effect=OSError("interrupted")):
                with self.assertRaisesRegex(OSError, "interrupted"):
                    budget.snapshot()
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(budget.snapshot(), before)
            self.assertEqual(list(path.parent.glob("budget.json.tmp-*")), [])
            path.unlink()
            with self.assertRaisesRegex(OpenAIRunBudgetError, "missing or empty"):
                budget.snapshot()

    def test_interrupted_request_limit_install_preserves_old_ceiling(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "budget.json"
            budget = OpenAIRunBudget(path, 5, authorized_max_usd=6, initialize_new_ledger=True)
            budget.snapshot()
            before = path.read_bytes()
            with patch("commander_gym.openai_run_budget.os.replace", side_effect=OSError("interrupted")):
                with self.assertRaisesRegex(OSError, "interrupted"):
                    budget.set_request_limit(610)
            self.assertEqual(path.read_bytes(), before)
            self.assertIsNone(budget.max_requests)
            with self.assertRaisesRegex(ValueError, "absolute request limit"):
                budget.increase_cap(6)

    def test_concurrent_reservations_use_stable_lock_after_replacement(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "budget.json"
            budget = OpenAIRunBudget(path, 5, max_requests=8, initialize_new_ledger=True)
            budget.snapshot()
            def respond(_index):
                local = OpenAIRunBudget(path, 5, max_requests=8)
                return local.create(
                    lambda **_request: SimpleNamespace(usage={"input_tokens": 20, "output_tokens": 10}),
                    self.request(),
                )
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(respond, range(8)))
            result = budget.snapshot()
            self.assertEqual(result["requests"], 8)
            self.assertEqual(result["unsettledRequests"], 0)
            with self.assertRaisesRegex(OpenAIRunBudgetError, "absolute request limit"):
                budget.create(lambda **_request: None, self.request())

    def test_cap_increase_is_explicit_monotonic_and_preserves_ledger(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "budget.json"
            with self.assertRaises(ValueError):
                OpenAIRunBudget(path, 6)
            with self.assertRaisesRegex(ValueError, "absolute request limit"):
                OpenAIRunBudget(path, 6, authorized_max_usd=6)
            with self.assertRaisesRegex(OpenAIRunBudgetError, "new ledger"):
                OpenAIRunBudget(
                    Path(temporary) / "new-above-default.json", 6,
                    authorized_max_usd=6, max_requests=10,
                    initialize_new_ledger=True,
                ).snapshot()

            budget = OpenAIRunBudget(path, 5, authorized_max_usd=6, max_requests=10, initialize_new_ledger=True)
            budget.snapshot()
            with self.assertRaisesRegex(ValueError, "absolute request limit"):
                OpenAIRunBudget(path, 5, authorized_max_usd=6).increase_cap(6)
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
            self.assertEqual(OpenAIRunBudget(
                path, 6, authorized_max_usd=6, max_requests=10,
            ).snapshot(), after)

    def test_absolute_request_limit_counts_ambiguous_attempts_across_restarts(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "budget.json"
            budget = OpenAIRunBudget(path, 5, max_requests=1, initialize_new_ledger=True)
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
            budget = OpenAIRunBudget(path, 0.01, initialize_new_ledger=True)
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
            budget = OpenAIRunBudget(Path(temporary) / "budget.json", 0.00001, initialize_new_ledger=True)
            calls = []
            with self.assertRaisesRegex(OpenAIRunBudgetError, "cumulative run cap"):
                budget.create(lambda **request: calls.append(request), self.request("x" * 10000))
            self.assertEqual(calls, [])
            self.assertEqual(budget.snapshot()["requests"], 0)

    def test_rejects_model_or_missing_output_cap_before_dispatch(self):
        with TemporaryDirectory() as temporary:
            budget = OpenAIRunBudget(Path(temporary) / "budget.json", 5, initialize_new_ledger=True)
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
