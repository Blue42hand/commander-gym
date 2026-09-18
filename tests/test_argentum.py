import copy
import unittest

from commander_gym import (
    ArgentumCanonicalDecision,
    ArgentumCanonicalError,
    PilotProvenance,
    StaleArgentumDecisionError,
)


def observation(action_ids=(7, 11), *, digest="state-a"):
    return {
        "type": "Game",
        "schemaHash": "schema-v1",
        "perspectivePlayerId": 101,
        "agentToAct": 101,
        "turnNumber": 3,
        "phase": "PRECOMBAT_MAIN",
        "step": "PRECOMBAT_MAIN",
        "activePlayerId": 101,
        "priorityPlayerId": 101,
        "players": [
            {"id": 101, "name": "Seat A", "handSize": 7, "isPerspective": True},
            {"id": 202, "name": "Seat B", "handSize": 2, "isPerspective": False},
        ],
        "zones": [
            {
                "ownerId": 101,
                "zoneType": "HAND",
                "hidden": False,
                "size": 1,
                "cards": [{"entityId": 501, "name": "Mountain"}],
            },
            {
                "ownerId": 202,
                "zoneType": "HAND",
                "hidden": True,
                "size": 2,
                "cards": [],
            },
        ],
        "stack": [],
        "pendingDecision": None,
        "legalActions": [
            {
                "actionId": action_ids[0],
                "kind": "PlayLand",
                "description": "Play Mountain",
                "affordable": True,
                "sourceEntityId": 501,
                "targetEntityIds": [],
                "manaCost": None,
                "hasXCost": False,
                "isDecisionOption": False,
            },
            {
                "actionId": action_ids[1],
                "kind": "PassPriority",
                "description": "Pass priority",
                "affordable": True,
                "sourceEntityId": None,
                "targetEntityIds": [],
                "manaCost": None,
                "hasXCost": False,
                "isDecisionOption": False,
            },
        ],
        "terminated": False,
        "winnerId": None,
        "stateDigest": digest,
    }


class ArgentumCanonicalDecisionTests(unittest.TestCase):
    def test_semantic_ids_survive_ephemeral_argentum_id_regeneration(self):
        first = ArgentumCanonicalDecision.from_observation(observation((7, 11)))
        second_obs = observation((91, 103))
        second = ArgentumCanonicalDecision.from_observation(second_obs)

        self.assertEqual(first.decision_id, second.decision_id)
        self.assertEqual(
            [a.action_id for a in first.legal_actions],
            [a.action_id for a in second.legal_actions],
        )
        self.assertEqual(
            first.resolve_for_current_observation(first.legal_actions[0].action_id, second_obs),
            91,
        )

    def test_rejects_stale_unknown_and_ambiguous_actions(self):
        packet = ArgentumCanonicalDecision.from_observation(observation())
        chosen = packet.legal_actions[0].action_id
        with self.assertRaises(StaleArgentumDecisionError):
            packet.resolve_for_current_observation(chosen, observation(digest="state-b"))
        with self.assertRaises(ArgentumCanonicalError):
            packet.resolve_for_current_observation("invented", observation())

        duplicate = observation()
        duplicate["legalActions"].append(copy.deepcopy(duplicate["legalActions"][0]))
        duplicate["legalActions"][-1]["actionId"] = 99
        with self.assertRaises(ArgentumCanonicalError):
            ArgentumCanonicalDecision.from_observation(duplicate)

    def test_record_preserves_seat_projection_and_argentum_provenance(self):
        packet = ArgentumCanonicalDecision.from_observation(observation())
        chosen = packet.legal_actions[0].action_id
        record = packet.to_training_record(
            chosen_action_id=chosen,
            game_id="argentum-game-1",
            seat=0,
            pilot=PilotProvenance(source="test", implementation="direct-pilot", version="v1"),
            deck_id="synthetic-supported-deck",
            deck_version="fixture-v1",
            primer_version="primer-v1",
        )

        self.assertEqual(record.metadata["engine"], "argentum")
        self.assertEqual(record.metadata["argentum_selected_action_id"], 7)
        opponent_hand = next(
            zone
            for zone in record.observation["zones"]
            if zone["ownerId"] == 202 and zone["zoneType"] == "HAND"
        )
        self.assertTrue(opponent_hand["hidden"])
        self.assertEqual(opponent_hand["cards"], [])

    def test_structured_decision_without_folded_actions_fails_closed(self):
        obs = observation()
        obs["legalActions"] = []
        obs["pendingDecision"] = {
            "decisionId": "native-decision-1",
            "kind": "CHOOSE_TARGETS",
            "playerId": 101,
            "prompt": "Choose two targets",
            "requiresStructuredResponse": True,
            "shape": {"minSelections": 2, "maxSelections": 2},
        }
        with self.assertRaisesRegex(ArgentumCanonicalError, "Structured Argentum decisions"):
            ArgentumCanonicalDecision.from_observation(obs)


if __name__ == "__main__":
    unittest.main()
