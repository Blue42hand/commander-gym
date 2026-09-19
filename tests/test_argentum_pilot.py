import unittest

from commander_gym.pilot import (
    ArgentumActionChoice,
    ArgentumDecisionChoice,
    PilotContractError,
    choose_for_observation,
    validate_pilot_choice,
)


def action_observation():
    return {
        "type": "Game",
        "schemaHash": "schema-v1",
        "stateDigest": "state-a",
        "perspectivePlayerId": "player-1",
        "agentToAct": "player-1",
        "pendingDecision": None,
        "legalActions": [
            {"actionId": 7, "kind": "CastSpell", "description": "Cast"},
            {"actionId": 9, "kind": "PassPriority", "description": "Pass"},
        ],
        "terminated": False,
    }


def structured_observation():
    return {
        "type": "Game",
        "schemaHash": "schema-v1",
        "stateDigest": "state-b",
        "perspectivePlayerId": "player-1",
        "agentToAct": "player-1",
        "pendingDecision": {
            "decisionId": "decision-routing-1",
            "kind": "CHOOSE_TARGETS",
            "requiresStructuredResponse": True,
            "shape": {"minSelections": 1, "maxSelections": 2},
        },
        "legalActions": [],
        "terminated": False,
    }


class StaticPilot:
    name = "synthetic"
    version = "1"

    def __init__(self, choice):
        self.choice = choice
        self.seen = None

    def choose(self, observation):
        self.seen = observation
        return self.choice


class ArgentumNativePilotContractTests(unittest.TestCase):
    def test_accepts_live_action_id_and_native_action_params(self):
        choice = ArgentumActionChoice(
            action_id=7,
            params={"targets": ["target-1"], "xValue": 3},
            metadata={"provider": "synthetic"},
        )
        self.assertIs(validate_pilot_choice(choice, action_observation()), choice)

    def test_rejects_stale_or_unknown_action_id(self):
        with self.assertRaisesRegex(PilotContractError, "not legal"):
            validate_pilot_choice(ArgentumActionChoice(action_id=11), action_observation())

    def test_structured_decision_uses_native_response_channel(self):
        choice = ArgentumDecisionChoice(
            response={
                "type": "ChooseTargetsResponse",
                "decisionId": "decision-routing-1",
                "targets": ["target-1"],
            }
        )
        self.assertIs(validate_pilot_choice(choice, structured_observation()), choice)

    def test_structured_decision_rejects_stale_routing_id(self):
        choice = ArgentumDecisionChoice(
            response={
                "type": "ChooseTargetsResponse",
                "decisionId": "old-decision",
                "targets": ["target-1"],
            }
        )
        with self.assertRaisesRegex(PilotContractError, "does not match"):
            validate_pilot_choice(choice, structured_observation())

    def test_response_channel_must_match_observation(self):
        with self.assertRaisesRegex(PilotContractError, "structured"):
            validate_pilot_choice(ArgentumActionChoice(action_id=7), structured_observation())

        with self.assertRaisesRegex(PilotContractError, "legalActions"):
            validate_pilot_choice(
                ArgentumDecisionChoice(
                    response={"decisionId": "decision-routing-1", "choice": True}
                ),
                action_observation(),
            )

    def test_refuses_cross_seat_information_set(self):
        observation = action_observation()
        observation["perspectivePlayerId"] = "player-2"
        with self.assertRaisesRegex(PilotContractError, "must match agentToAct"):
            validate_pilot_choice(ArgentumActionChoice(action_id=7), observation)

    def test_choose_passes_raw_argentum_observation_without_transport_wrapper(self):
        observation = action_observation()
        pilot = StaticPilot(ArgentumActionChoice(action_id=9))

        selected = choose_for_observation(pilot, observation)

        self.assertEqual(selected.action_id, 9)
        self.assertIs(pilot.seen, observation)

    def test_terminal_observation_fails_closed(self):
        observation = action_observation()
        observation["terminated"] = True
        observation["agentToAct"] = None
        observation["legalActions"] = []

        with self.assertRaisesRegex(PilotContractError, "terminal"):
            validate_pilot_choice(ArgentumActionChoice(action_id=7), observation)


if __name__ == "__main__":
    unittest.main()
