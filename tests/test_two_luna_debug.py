from pathlib import Path
import tempfile
import unittest

from commander_gym.two_luna_debug import _summarize


class TwoLunaDebugReportTests(unittest.TestCase):
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
                            "strategicWakeAvoided": True,
                        }
                    },
                },
            },
        ]
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
        self.assertEqual(summary["strategicWakesAvoided"], 1)
        self.assertEqual(summary["inputTokens"], 20)
        self.assertEqual(summary["cachedInputTokens"], 8)
        self.assertEqual(summary["outputTokens"], 4)
        self.assertEqual(summary["wallTimeSeconds"], 12.5)
        self.assertEqual(summary["communicationErrors"], [])
        self.assertEqual(summary["avoidableStrategicWakes"], [])


if __name__ == "__main__":
    unittest.main()
