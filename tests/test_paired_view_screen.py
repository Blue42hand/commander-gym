from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import time
from types import ModuleType
import unittest
from unittest.mock import patch

from commander_gym.openai_responses_pilot import OpenAIResponsesPilotError
from commander_gym.openai_run_budget import OpenAIRunBudget, OpenAIRunBudgetError
from commander_gym.pilot import ArgentumActionChoice
from scripts import run_paired_view_screen as screen


def _quick_worker(sender):
    sender.send({"ok": True})
    sender.close()


def _ambiguous_worker(sender, path):
    budget = OpenAIRunBudget(path, 5, max_requests=1)
    request = {
        "model": "gpt-6-luna", "input": "test",
        "max_output_tokens": 2048,
    }
    budget.create(lambda **_request: time.sleep(10), request)
    sender.send({"unexpected": True})
    sender.close()


class _Sender:
    def __init__(self):
        self.result = None

    def send(self, result):
        self.result = result

    def close(self):
        pass


class PairedViewScreenTests(unittest.TestCase):
    def test_exact_source_hash_rejects_same_named_modified_trace(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "policy.jsonl"
            path.write_bytes(b"approved\n")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            screen._verify_trace(path, "game/private/policy.jsonl", digest)
            path.write_bytes(b"modified\n")
            with self.assertRaisesRegex(ValueError, "trace content"):
                screen._verify_trace(path, "game/private/policy.jsonl", digest)

    def test_semantic_comparison_ignores_prose_but_preserves_commands(self):
        first = {
            "channel": "action", "semanticId": "pass", "params": {},
            "priorityDelegation": {"until": "turn_end", "reason": "hold mana", "watchOpponents": True},
            "thenCast": {"cardId": "c1", "reason": "tempo"},
        }
        second = json.loads(json.dumps(first))
        second["priorityDelegation"]["reason"] = "wait"
        second["thenCast"]["reason"] = "value"
        self.assertEqual(screen._semantic_choice(first), screen._semantic_choice(second))
        second["thenCast"]["cardId"] = "c2"
        self.assertNotEqual(screen._semantic_choice(first), screen._semantic_choice(second))
        self.assertEqual(first["priorityDelegation"]["reason"], "hold mana")

    def test_watchdog_returns_completed_worker_and_kills_ambiguous_attempt(self):
        self.assertEqual(screen._run_with_watchdog(_quick_worker, (), time.monotonic() + 5), {"ok": True})
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "budget.json"
            OpenAIRunBudget(path, 5, max_requests=1, initialize_new_ledger=True).snapshot()
            with self.assertRaisesRegex(TimeoutError, "hard wall deadline"):
                screen._run_with_watchdog(
                    _ambiguous_worker, (path,), time.monotonic() + 2,
                )
            restarted = OpenAIRunBudget(path, 5, max_requests=1)
            self.assertEqual(restarted.snapshot()["unsettledRequests"], 1)
            self.assertEqual(restarted.snapshot()["requests"], 1)
            with self.assertRaisesRegex(OpenAIRunBudgetError, "absolute request limit"):
                restarted.create(lambda **_request: None, {
                    "model": "gpt-6-luna", "input": "test", "max_output_tokens": 2048,
                })

    def test_invalid_provider_model_io_is_preserved_privately(self):
        with TemporaryDirectory() as temporary:
            artifact = Path(temporary) / "failed.model-io.json"
            fake_openai = ModuleType("openai")
            fake_openai.OpenAI = lambda **_kwargs: object()
            captured = {"attempts": [{"request": {"input": "private"},
                                       "response": {"validationError": "invalid"}}]}

            class FailingPilot:
                def choose(self, _observation):
                    raise OpenAIResponsesPilotError("invalid", model_io=captured)

            sender = _Sender()
            observation = {
                "agentToAct": "p", "perspectivePlayerId": "p",
                "terminated": False, "legalActions": [{"actionId": 1, "semanticId": "pass"}],
            }
            with patch.dict("sys.modules", {"openai": fake_openai}), patch.object(
                screen, "_pilot", return_value=FailingPilot(),
            ):
                screen._screen_worker(sender, Path(temporary) / "budget.json",
                                      observation, False, time.monotonic() + 5, artifact)
            self.assertIn("OpenAIResponsesPilotError", sender.result["error"])
            self.assertEqual(sender.result["modelIoArtifact"], str(artifact))
            self.assertEqual(json.loads(artifact.read_text()), captured)
            self.assertEqual(stat.S_IMODE(os.stat(artifact).st_mode), 0o600)

    def test_native_validation_failure_also_preserves_model_io(self):
        with TemporaryDirectory() as temporary:
            artifact = Path(temporary) / "native-failure.model-io.json"
            fake_openai = ModuleType("openai")
            fake_openai.OpenAI = lambda **_kwargs: object()
            captured = {"attempts": [{"request": {"input": "private"},
                                       "response": {"usage": {"input_tokens": 10, "output_tokens": 2}}}]}

            class InvalidPilot:
                def choose(self, _observation):
                    return ArgentumActionChoice(action_id=999, metadata={"modelIo": captured})

            sender = _Sender()
            observation = {
                "agentToAct": "p", "perspectivePlayerId": "p",
                "terminated": False, "legalActions": [{"actionId": 1, "semanticId": "pass"}],
            }
            with patch.dict("sys.modules", {"openai": fake_openai}), patch.object(
                screen, "_pilot", return_value=InvalidPilot(),
            ):
                screen._screen_worker(sender, Path(temporary) / "budget.json",
                                      observation, False, time.monotonic() + 5, artifact)
            self.assertIsNotNone(sender.result["error"])
            self.assertEqual(json.loads(artifact.read_text()), captured)

    def test_valid_choice_preserves_full_model_io_and_usage(self):
        with TemporaryDirectory() as temporary:
            artifact = Path(temporary) / "valid.model-io.json"
            fake_openai = ModuleType("openai")
            fake_openai.OpenAI = lambda **_kwargs: object()
            captured = {"attempts": [{
                "request": {"input": "private"},
                "response": {"outputText": "private response", "usage": {
                    "input_tokens": 10, "output_tokens": 2,
                }, "providerWallTimeMs": 42},
            }]}

            class ValidPilot:
                def choose(self, _observation):
                    return ArgentumActionChoice(action_id=1, metadata={"modelIo": captured})

            sender = _Sender()
            observation = {
                "agentToAct": "p", "perspectivePlayerId": "p", "terminated": False,
                "legalActions": [{"actionId": 1, "semanticId": "pass"}],
            }
            with patch.dict("sys.modules", {"openai": fake_openai}), patch.object(
                screen, "_pilot", return_value=ValidPilot(),
            ):
                screen._screen_worker(sender, Path(temporary) / "budget.json",
                                      observation, False, time.monotonic() + 5, artifact)
            self.assertIsNone(sender.result["error"])
            self.assertEqual(sender.result["choice"]["semanticId"], "pass")
            self.assertEqual(sender.result["usage"]["input_tokens"], 10)
            self.assertEqual(json.loads(artifact.read_text()), captured)


if __name__ == "__main__":
    unittest.main()
