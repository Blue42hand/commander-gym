import unittest
from copy import deepcopy

from commander_gym.records import DecisionRecord
from commander_gym.sparse_features import encode_decision_record, policy_family_for_decision_type


def fixture_decision():
    return {
        "schema_version": 1,
        "game_id": "game-1",
        "decision_id": "decision-semantic-7",
        "decision_type": "priority_action",
        "seat": 2,
        "observation_schema": "argentum-gym/fixture-v1",
        "observation": {
            "turn": 4,
            "phase": "main1",
            "seat_private": {"hand": ["example-card"]},
            "battlefield": [{"name": "Example Permanent", "tapped": False}],
        },
        "legal_actions": [
            {
                "action_id": "semantic:pass",
                "payload": {"command": "pass"},
                "label": "Pass priority",
            },
            {
                "action_id": "semantic:cast:example",
                "payload": {"card": "example-card", "command": "cast"},
                "label": "Cast Example Card",
            },
        ],
        "chosen_action_id": "semantic:cast:example",
        "pilot": {"source": "human", "implementation": "fixture-pilot", "version": "test"},
        "deck_id": "fixture-deck",
        "deck_version": "fixture-deck/v1",
        "primer_version": "fixture-primer/v1",
        "outcome": {"winner_seat": 2, "placement": 1},
        "metadata": {"transport": "fixture"},
    }


class SparseFeatureTests(unittest.TestCase):
    def encode(self, value=None, feature_space=2_000_000):
        return encode_decision_record(
            DecisionRecord.from_dict(value or fixture_decision()),
            feature_space=feature_space,
        )

    def test_stable_under_mapping_key_order(self):
        first = fixture_decision()
        second = deepcopy(first)
        second["observation"] = dict(reversed(list(second["observation"].items())))
        second["legal_actions"][1]["payload"] = {
            "command": "cast",
            "card": "example-card",
        }
        self.assertEqual(self.encode(first).to_dict(), self.encode(second).to_dict())

    def test_post_decision_and_provenance_fields_do_not_leak_into_features(self):
        first = fixture_decision()
        second = deepcopy(first)
        second["chosen_action_id"] = "semantic:pass"
        second["outcome"] = {"winner_seat": 0, "placement": 4, "future_label": "changed"}
        second["metadata"] = {"transport": "different", "debug_hidden_hand": ["must-not-leak"]}
        second["pilot"] = {
            "source": "model",
            "implementation": "future-specialist",
            "version": "999",
            "model": "example",
        }
        second["deck_id"] = "other-deck-label"
        second["deck_version"] = "other-deck/v99"
        second["primer_version"] = "other-primer/v2"
        self.assertEqual(self.encode(first).to_dict(), self.encode(second).to_dict())

    def test_preserves_recorded_action_ids_and_policy_family(self):
        encoded = self.encode()
        self.assertEqual(encoded.policy_family, "priority")
        self.assertEqual(
            set(encoded.action_indices),
            {"semantic:pass", "semantic:cast:example"},
        )
        self.assertGreater(len(encoded.state_indices), 0)
        self.assertGreater(len(encoded.action_indices["semantic:cast:example"]), 0)

    def test_candidate_payload_changes_candidate_features_not_state_features(self):
        first = fixture_decision()
        second = deepcopy(first)
        second["legal_actions"][1]["payload"]["card"] = "different-card"
        encoded_first = self.encode(first)
        encoded_second = self.encode(second)
        self.assertEqual(encoded_first.state_indices, encoded_second.state_indices)
        self.assertEqual(
            encoded_first.action_indices["semantic:pass"],
            encoded_second.action_indices["semantic:pass"],
        )
        self.assertNotEqual(
            encoded_first.action_indices["semantic:cast:example"],
            encoded_second.action_indices["semantic:cast:example"],
        )

    def test_rejects_non_positive_feature_space(self):
        with self.assertRaises(ValueError):
            self.encode(feature_space=0)

    def test_policy_family_mapping(self):
        cases = {
            "mulligan": "mulligan",
            "declare_attackers": "combat",
            "mana_payment": "payment",
            "trigger_order": "ordering",
            "yes_no": "binary",
            "target_selection": "selection",
            "priority_action": "priority",
            "novel_future_decision": "other",
        }
        for decision_type, expected in cases.items():
            with self.subTest(decision_type=decision_type):
                self.assertEqual(policy_family_for_decision_type(decision_type), expected)


if __name__ == "__main__":
    unittest.main()
