from pathlib import Path
from argparse import Namespace
import hashlib
import json
import stat
import tempfile
import unittest
from unittest.mock import patch

from commander_gym.openai_run_budget import OpenAIRunBudget, OpenAIRunBudgetError
from commander_gym.two_luna_debug import (
    _ProgressGuard, _capture_terminal_replay, _natural_terminal_game,
    _provenance_size, _run_budget, _summarize,
)


class TwoLunaDebugReportTests(unittest.TestCase):
    def test_captures_replay_and_native_terminal_frame_before_server_cleanup(self):
        replay = {"metadata": {"gameId": "game-1", "snapshotCount": 3,
                               "stateReproducible": True},
                  "initialSnapshot": {}, "deltas": [{}, {}]}
        state = {"gameOver": True, "winnerId": "ai-a", "turnNumber": 13}
        # The pre-fix status could omit nativeGameOver yet still identify the
        # completed match. Replay capture must remain independent of that DTO.
        finished = [{"gameSessionId": "game-1", "winnerId": "ai-a",
                     "nativeGameOver": False, "finalTurnNumber": None}]
        with tempfile.TemporaryDirectory() as directory:
            with patch("commander_gym.two_luna_debug._request_json",
                       side_effect=[replay, state]) as request:
                receipt = _capture_terminal_replay("http://127.0.0.1:1234", finished, Path(directory))
            request.assert_any_call("http://127.0.0.1:1234/api/public/replays/game-1", timeout=120)
            request.assert_any_call(
                "http://127.0.0.1:1234/api/public/replays/game-1/frames/2/full-state",
                timeout=120,
            )
            self.assertEqual(receipt["finalTurnNumber"], 13)
            for name, expected_hash in (("terminal-replay.json", receipt["replaySha256"]),
                                        ("terminal-state.json", receipt["terminalStateSha256"])):
                path = Path(directory) / name
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected_hash)
            self.assertEqual(json.loads((Path(directory) / "terminal-state.json").read_text()), state)

    def test_capture_rejects_a_nonterminal_final_frame(self):
        replay = {"metadata": {"gameId": "game-1", "snapshotCount": 1}}
        with tempfile.TemporaryDirectory() as directory:
            with patch("commander_gym.two_luna_debug._request_json",
                       side_effect=[replay, {"gameOver": False, "turnNumber": 13}]):
                with self.assertRaisesRegex(RuntimeError, "native game-over"):
                    _capture_terminal_replay(
                        "http://127.0.0.1:1234",
                        [{"gameSessionId": "game-1", "winnerId": "ai-a"}], Path(directory),
                    )
            self.assertTrue((Path(directory) / "terminal-replay.json").is_file())
            self.assertFalse((Path(directory) / "terminal-state.json").exists())

    def test_existing_elevated_cumulative_budget_is_read_without_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            original = OpenAIRunBudget(path, 5, authorized_max_usd=8,
                                       max_requests=610, initialize_new_ledger=True)
            original.snapshot()
            original.set_request_limit(906)
            original.increase_cap(8)
            args = Namespace(budget_ledger=str(path), budget_cap=8,
                             budget_authorized_max=8, budget_max_requests=906)
            self.assertEqual(_run_budget(args).snapshot()["maxRequests"], 906)
            self.assertEqual(_run_budget(args).snapshot()["requests"], 0)
            args.budget_max_requests = 907
            with self.assertRaises(OpenAIRunBudgetError):
                _run_budget(args).snapshot()

    def test_progress_guard_counts_callbacks_and_api_reservations_but_expires_old_inflight(self):
        guard = _ProgressGuard(stall_seconds=300, last_change=0)
        # The third field is the cumulative reservation count. A request that
        # takes two minutes is activity even before provenance is written.
        self.assertFalse(guard.observe(("turn-4", 100, 10), 0))
        self.assertFalse(guard.observe(("turn-4", 100, 10), 250))
        self.assertFalse(guard.observe(("turn-4", 100, 11), 251))
        self.assertFalse(guard.observe(("turn-4", 150, 11), 500))
        self.assertTrue(guard.observe(("turn-4", 150, 11), 800))
        # Unsettled counts are deliberately absent: three pre-existing
        # reservations cannot keep a stalled game alive indefinitely.

    def test_provenance_size_reports_missing_and_written_callbacks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.jsonl"
            self.assertEqual(_provenance_size(path), 0)
            path.write_text("{}\n")
            self.assertEqual(_provenance_size(path), 3)
            self.assertIsNone(_provenance_size(None))

    def test_requires_one_played_native_terminal_match(self):
        played = {
            "gameSessionId": "game-1",
            "winnerId": "ai-a",
            "isDraw": False,
            "isSimulated": False,
            "nativeGameOver": True,
            "finalTurnNumber": 8,
        }
        self.assertEqual(_natural_terminal_game([played]), played)
        self.assertIsNone(_natural_terminal_game([]))
        self.assertIsNone(_natural_terminal_game([played, played]))
        for change in (
            {"gameSessionId": None},
            {"nativeGameOver": False},
            {"isSimulated": True},
            {"winnerId": None},
        ):
            with self.subTest(change=change):
                self.assertIsNone(_natural_terminal_game([{**played, **change}]))
        self.assertIsNotNone(_natural_terminal_game([{**played, "winnerId": None, "isDraw": True}]))

    def test_reports_mechanical_wakes_provider_usage_and_clean_qualification(self):
        records = [
            {
                "callback": "chooseAction",
                "playerId": "ai-a",
                "observation": {
                    "agentToAct": "ai-a",
                    "perspectivePlayerId": "ai-a",
                    "terminated": False,
                    "pendingDecision": None,
                    "knownDeck": {"cards": {"Island": 20}},
                    "legalActions": [],
                },
                "choice": {
                    "channel": "action",
                    "metadata": {
                        "routing": {
                            "path": "strategic",
                            "strategicWakeAvoided": False,
                        },
                        "provider": "openai",
                        "retryCount": 0,
                        "usage": {
                            "input_tokens": 20,
                            "input_tokens_details": {"cached_tokens": 8},
                            "output_tokens": 4,
                        },
                    },
                },
            },
            {
                "callback": "chooseAction",
                "playerId": "ai-b",
                "observation": {
                    "agentToAct": "ai-b",
                    "perspectivePlayerId": "ai-b",
                    "terminated": False,
                    "pendingDecision": {
                        "decisionId": "damage-1",
                        "type": "AssignDamageDecision",
                        "requiresStructuredResponse": True,
                        "defaultAssignments": {"x": 2},
                    },
                    "knownDeck": {"cards": {"Mountain": 20}},
                    "legalActions": [],
                },
                "choice": {
                    "channel": "decision",
                    "metadata": {
                        "routing": {
                            "path": "mechanical",
                            "handler": "certified-native-decision",
                            "strategicWakeAvoided": True,
                        }
                    },
                },
            },
        ]
        records.append({
            "callback": "chooseAction", "playerId": "ai-a",
            "observation": records[0]["observation"],
            "choice": {"channel": "action", "metadata": {
                "routing": {"path": "composed"},
                "delegatedPass": {"leaseId": "lease-1", "strategicWakeAvoided": True},
            }},
        })
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "server.log"
            log.write_text("normal game log\n")
            summary = _summarize(
                records,
                completed=True,
                lobby_id="lobby-1",
                game_ids=["game-1"],
                max_turn=8,
                log_path=log,
                wall_time_seconds=12.5,
            )

        self.assertTrue(summary["technicalQualified"])
        self.assertTrue(summary["provenanceComplete"])
        self.assertEqual(summary["policySeats"], ["ai-a", "ai-b"])
        self.assertEqual(summary["providerCalls"], 1)
        self.assertEqual(summary["strategicWakesAvoided"], 2)
        self.assertEqual(summary["delegatedPasses"], 1)
        self.assertEqual(summary["strategicByKind"], {"chooseAction": 1})
        self.assertEqual(summary["mechanicalByHandler"], {"certified-native-decision": 1})
        self.assertEqual(summary["inputTokens"], 20)
        self.assertEqual(summary["cachedInputTokens"], 8)
        self.assertEqual(summary["outputTokens"], 4)
        self.assertEqual(summary["wallTimeSeconds"], 12.5)
        self.assertEqual(summary["communicationErrors"], [])
        self.assertEqual(summary["avoidableStrategicWakes"], [])


if __name__ == "__main__":
    unittest.main()
