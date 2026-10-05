"""Keep observed wake counts separate from counterfactual replay opportunities."""

import json
from pathlib import Path
import tempfile
import unittest

from scripts.analyze_early_wakes import analyze


class EarlyWakeAnalysisTests(unittest.TestCase):
    def test_standing_pass_replay_is_labeled_counterfactual(self):
        passed = {
            "actionId": 0, "semanticId": "native-pass", "kind": "PassPriority",
            "isManaAbility": False, "affordable": True,
            "action": {"type": "PassPriority", "playerId": "seat-a"},
        }
        mana = {
            "actionId": 1, "semanticId": "native-mana", "kind": "ActivateAbility",
            "isManaAbility": True, "affordable": False,
            "action": {"type": "ActivateAbility", "playerId": "seat-a"},
        }
        record = {
            "observation": {
                "state": {"turnNumber": 1, "stack": ["new-spell"]},
                "agentToAct": "seat-a", "perspectivePlayerId": "seat-a",
                "terminated": False, "pendingDecision": None,
                "legalActions": [passed, mana],
            },
            "choice": {"actionId": 0, "metadata": {"modelIo": {"attempts": [{}]}}},
        }
        with tempfile.TemporaryDirectory() as folder:
            trace = Path(folder) / "policy.jsonl"
            trace.write_text(json.dumps(record) + "\n")
            baseline = analyze([trace], through_turn=8)["games"][0]
            experiment = analyze([trace], through_turn=8, standing_mana_only=True)["games"][0]
        self.assertEqual(baseline["total"]["eligible"], 0)
        self.assertEqual(experiment["total"]["eligible"], 1)
        self.assertEqual(experiment["total"]["matched"], 1)
        self.assertEqual(experiment["observed"]["total"]["modelAttempts"], 1)

    def test_reports_observed_provider_and_pass_menu_counts(self):
        def row(turn, legal, action_id, metadata):
            return {
                "observation": {
                    "state": {"turnNumber": turn}, "legalActions": legal,
                    "pendingDecision": None,
                },
                "choice": {"actionId": action_id, "metadata": metadata},
            }

        passed = {"actionId": 0, "kind": "PassPriority", "affordable": True}
        mana = {"actionId": 1, "kind": "ActivateAbility", "isManaAbility": True}
        cycle = {"actionId": 2, "kind": "CycleCard"}
        records = [
            row(1, [passed, mana], 0, {
                "modelIo": {"attempts": [{"attempt": 0}]},
                "priorityDelegation": {"until": "phase_end", "reason": "Reviewed"},
                "routing": {"handledBy": {"implementation": "delegated-autopass"}},
            }),
            row(1, [passed], 0, {
                "routing": {"handledBy": {"implementation": "native-no-choice"}},
            }),
            row(2, [passed, mana, cycle], 0, {
                "modelIo": {"attempts": [{"attempt": 0}, {"attempt": 1}]},
                "priorityDelegationRejected": "invalid boundary",
                "routing": {"handledBy": {"implementation": "delegated-autopass"}},
            }),
        ]
        with tempfile.TemporaryDirectory() as folder:
            trace = Path(folder) / "policy.jsonl"
            trace.write_text("".join(json.dumps(record) + "\n" for record in records))
            result = analyze([trace], through_turn=8)["games"][0]

        self.assertEqual(result["observed"]["total"], {
            "modelAttempts": 3, "nativeNoChoice": 1, "leaseRequests": 1,
            "leaseRejected": 1, "delegatedPasses": 0, "selectedPasses": 3,
            "passPlusManaOnly": 2, "passWithNonmanaChoice": 1,
        })
        self.assertEqual(result["total"]["callbacks"], 3)
        self.assertEqual(result["observed"]["turns"]["1"]["nativeNoChoice"], 1)


if __name__ == "__main__":
    unittest.main()
