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
                        "usage": {"input_tokens": 20, "output_tokens": 4},
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
            )

        self.assertTrue(summary["technicalQualified"])
        self.assertEqual(summary["providerCalls"], 1)
        self.assertEqual(summary["strategicWakesAvoided"], 1)
        self.assertEqual(summary["inputTokens"], 20)
        self.assertEqual(summary["outputTokens"], 4)
        self.assertEqual(summary["communicationErrors"], [])
        self.assertEqual(summary["avoidableStrategicWakes"], [])


if __name__ == "__main__":
    unittest.main()
