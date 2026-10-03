import json
import unittest

from commander_gym.observation_projection import (
    SPARSE_CARD_VIEW_VERSION, compact_seat_observation, expand_seat_observation,
)


class ObservationProjectionTests(unittest.TestCase):
    def test_sparse_cards_round_trip_exact_defaults_and_keep_native_choices(self):
        observation = {
            "agentToAct": "seat-a", "perspectivePlayerId": "seat-a",
            "legalActions": [{"semanticId": "native-cast", "actionId": 7}],
            "pendingDecision": None,
            "state": {"cards": {
                "visible-1": {"id": "visible-1", "name": "Card A", "isTapped": False,
                              "damage": 0, "targets": [], "metadata": {}, "rider": None},
                "visible-2": {"id": "visible-2", "name": "Card B", "isTapped": True,
                              "damage": 2, "targets": ["visible-1"], "metadata": {}, "rider": None},
            }, "gameLog": [{"type": "spellCast", "spellId": "visible-1"}]},
        }
        before = json.dumps(observation, sort_keys=True)
        compact = compact_seat_observation(observation)
        self.assertEqual(compact["format"], SPARSE_CARD_VIEW_VERSION)
        self.assertEqual(compact["cardDefaults"], {
            "isTapped": False, "targets": [], "metadata": {}, "rider": None,
        })
        self.assertEqual(compact["observation"]["state"]["cards"]["visible-1"]["damage"], 0)
        self.assertEqual(expand_seat_observation(compact), observation)
        self.assertEqual(json.dumps(observation, sort_keys=True), before)
        self.assertEqual(compact["observation"]["legalActions"], observation["legalActions"])
        self.assertNotIn("hidden-card", json.dumps(compact))

    def test_irregular_card_shapes_fall_back_to_full_view(self):
        observation = {"state": {"cards": {
            "a": {"id": "a", "isTapped": False},
            "b": {"id": "b"},
        }}}
        compact = compact_seat_observation(observation)
        self.assertEqual(compact["cardDefaults"], {})
        self.assertEqual(expand_seat_observation(compact), observation)

    def test_inconsistent_empty_defaults_fall_back_per_field(self):
        observation = {"state": {"cards": {
            "a": {"id": "a", "value": []},
            "b": {"id": "b", "value": None},
        }}}
        compact = compact_seat_observation(observation)
        self.assertEqual(compact["cardDefaults"], {})
        self.assertEqual(expand_seat_observation(compact), observation)

    def test_malformed_version_and_defaults_rejected(self):
        compact = compact_seat_observation({"state": {"cards": {"a": {"id": "a", "x": False}}}})
        with self.assertRaises(ValueError):
            expand_seat_observation({**compact, "format": "unknown"})
        with self.assertRaises(ValueError):
            expand_seat_observation({**compact, "cardDefaults": {"x": "unknown"}})


if __name__ == "__main__":
    unittest.main()
