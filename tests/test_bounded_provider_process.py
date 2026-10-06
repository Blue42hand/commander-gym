from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from commander_gym.bounded_provider_process import (
    BoundedProcessResponses, BoundedProviderProcessError,
)
from commander_gym.openai_responses_pilot import OpenAIResponsesPilot, OpenAIResponsesPilotError
from commander_gym.openai_run_budget import OpenAIRunBudget
from tests.test_openai_responses_pilot import action_observation


class BoundedProviderProcessTests(unittest.TestCase):
    def test_worker_success_round_trips_response_and_settles_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            budget = OpenAIRunBudget(Path(directory) / "ledger.json", 5,
                                     initialize_new_ledger=True)
            client = type("Client", (), {"responses": BoundedProcessResponses(
                "sk-test-offline", worker_module="tests._deadline_worker_fixture"
            )})()
            with patch.dict("os.environ", {"COMMANDER_GYM_TEST_WORKER_MODE": "success"}):
                choice = OpenAIResponsesPilot(
                    client=client, model="gpt-6-luna", budget=budget,
                    retry_transient_server_errors=True,
                ).choose(action_observation())
            self.assertEqual(choice.action_id, 2)
            self.assertEqual(choice.metadata["modelIo"]["selectedAttempt"], 0)
            self.assertEqual(budget.snapshot()["requests"], 1)
            self.assertEqual(budget.snapshot()["unsettledRequests"], 0)

    def test_worker_server_error_is_a_separately_reserved_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            budget = OpenAIRunBudget(Path(directory) / "ledger.json", 5,
                                     initialize_new_ledger=True)
            client = type("Client", (), {"responses": BoundedProcessResponses(
                "sk-test-offline", worker_module="tests._deadline_worker_fixture"
            )})()
            with patch.dict("os.environ", {"COMMANDER_GYM_TEST_WORKER_MODE": "server520"}):
                with self.assertRaisesRegex(OpenAIResponsesPilotError, "status=520") as caught:
                    OpenAIResponsesPilot(
                        client=client, model="gpt-6-luna", budget=budget,
                        retry_transient_server_errors=True,
                    ).choose(action_observation())
            self.assertEqual(budget.snapshot()["requests"], 2)
            self.assertEqual(budget.snapshot()["unsettledRequests"], 2)
            self.assertEqual(len(caught.exception.model_io["attempts"]), 2)

    def test_slow_trickle_is_killed_at_whole_request_deadline(self):
        self._assert_cancellation("trickle")

    def test_blocked_request_is_killed_at_whole_request_deadline(self):
        self._assert_cancellation("blocked")

    def _assert_cancellation(self, mode):
        worker = BoundedProcessResponses(
            "sk-test-offline", worker_module="tests._deadline_worker_fixture"
        )
        children = []
        real_popen = subprocess.Popen

        def capture(*args, **kwargs):
            child = real_popen(*args, **kwargs)
            children.append(child)
            return child

        with patch.dict("os.environ", {"COMMANDER_GYM_TEST_WORKER_MODE": mode}), \
                patch("commander_gym.bounded_provider_process.subprocess.Popen", side_effect=capture):
            with self.assertRaisesRegex(BoundedProviderProcessError, "deadline"):
                worker.create(model="gpt-6-luna", timeout=0.5)
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll(), "timed-out worker must be reaped")

    def test_cancelled_paid_attempt_keeps_reservation_and_private_failure_trace(self):
        with tempfile.TemporaryDirectory() as directory:
            budget = OpenAIRunBudget(
                Path(directory) / "ledger.json", 5, max_requests=2,
                initialize_new_ledger=True,
            )
            client = type("Client", (), {"responses": BoundedProcessResponses(
                "sk-test-offline", worker_module="tests._deadline_worker_fixture"
            )})()
            with patch.dict("os.environ", {"COMMANDER_GYM_TEST_WORKER_MODE": "trickle"}), \
                    patch("commander_gym.openai_responses_pilot._RECOVERY_CALLBACK_SECONDS", 1.0), \
                    patch("commander_gym.openai_responses_pilot._RECOVERY_REQUEST_SECONDS", 0.5), \
                    patch("commander_gym.openai_responses_pilot._RECOVERY_RETURN_MARGIN_SECONDS", 0.05), \
                    patch("commander_gym.openai_responses_pilot._RECOVERY_MIN_RETRY_SECONDS", 0.1):
                with self.assertRaisesRegex(OpenAIResponsesPilotError, "BoundedProviderProcessError") as caught:
                    OpenAIResponsesPilot(
                        client=client, model="gpt-6-luna", budget=budget,
                        retry_transient_server_errors=True,
                    ).choose(action_observation())
            self.assertEqual(budget.snapshot()["requests"], 1)
            self.assertEqual(budget.snapshot()["unsettledRequests"], 1)
            self.assertEqual(len(caught.exception.model_io["attempts"]), 1)
            self.assertIn("transportError", caught.exception.model_io["attempts"][0]["response"])
