from pathlib import Path
import tempfile
import unittest

from commander_gym.two_luna_debug import _ProgressGuard, _natural_terminal_game, _provenance_size, _summarize


class TwoLunaDebugReportTests(unittest.TestCase):
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
