import unittest

from commander_gym.pilot import (
    ArgentumActionChoice,
    ArgentumDecisionChoice,
    choose_for_observation,
)
from commander_gym.pilot_routing import RoutingPilot


def observation(action, *, pending=None):
    return {
        "type": "Game",
        "schemaHash": "argentum-gym-contract@v1.7-semantic-state-provenance",
        "stateDigest": "state-a",
        "perspectivePlayerId": "player-1",
        "agentToAct": "player-1",
        "pendingDecision": pending,
        "legalActions": [action] if action is not None else [],
        "terminated": False,
    }


def pass_action():
    return {
        "actionId": 0,
        "semanticId": "argentum-action-v1:pass",
        "kind": "PassPriority",
        "description": "Pass priority",
        "affordable": True,
        "isDecisionOption": False,
        "hasXCost": False,
        "minTargets": 0,
        "maxTargets": 0,
    }


class CountingPilot:
    name = "strategic-fixture"
    version = "1"

    def __init__(self, choice=None, error=None):
        self.choice = choice
        self.error = error
        self.calls = 0

    def choose(self, observation):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.choice


class RoutingPilotTests(unittest.TestCase):
    def test_forced_parameterless_pass_avoids_strategic_wake(self):
        strategic = CountingPilot(ArgentumActionChoice(action_id=999))
        router = RoutingPilot(strategic)

        choice = choose_for_observation(router, observation(pass_action()))

        self.assertEqual(strategic.calls, 0)
        self.assertEqual(choice.action_id, 0)
        self.assertEqual(choice.params, {})
        self.assertEqual(choice.metadata["routing"]["path"], "mechanical")
        self.assertEqual(choice.metadata["routing"]["handler"], "forced-parameterless-choice")
        self.assertTrue(choice.metadata["routing"]["strategicWakeAvoided"])

    def test_forced_pass_ignores_generic_target_defaults_from_argentum(self):
        action = pass_action()
        action.update({"minTargets": 1, "maxTargets": 1})
        strategic = CountingPilot(ArgentumActionChoice(action_id=999))
        router = RoutingPilot(strategic)

        choice = choose_for_observation(router, observation(action))

        self.assertEqual(strategic.calls, 0)
        self.assertEqual(choice.action_id, 0)
        self.assertEqual(choice.metadata["routing"]["path"], "mechanical")

    def test_parameter_bearing_action_escalates_to_strategic_pilot(self):
        attack = {
            "actionId": 4,
            "semanticId": "argentum-action-v1:attack",
            "kind": "DeclareAttackers",
            "description": "Declare attackers",
            "affordable": True,
            "isDecisionOption": False,
            "hasXCost": False,
            "minTargets": 0,
            "maxTargets": 0,
            "validAttackers": ["creature-1"],
            "validAttackTargets": ["player-2"],
        }
        strategic = CountingPilot(
            ArgentumActionChoice(
                action_id=4,
                params={"attackers": {"creature-1": "player-2"}},
                metadata={"provider": "fixture"},
            )
        )
        router = RoutingPilot(strategic)

        choice = choose_for_observation(router, observation(attack))

        self.assertEqual(strategic.calls, 1)
        self.assertEqual(choice.params["attackers"], {"creature-1": "player-2"})
        self.assertEqual(choice.metadata["provider"], "fixture")
        self.assertEqual(choice.metadata["routing"]["path"], "strategic")
        self.assertFalse(choice.metadata["routing"]["strategicWakeAvoided"])

    def test_meaningful_structured_decision_escalates(self):
        pending = {
            "decisionId": "routing-1",
            "semanticId": "argentum-decision-v1:yes-no",
            "kind": "YesNoDecision",
            "type": "YesNoDecision",
            "requiresStructuredResponse": True,
        }
        response = {
            "type": "YesNoResponse",
            "decisionId": "routing-1",
            "choice": True,
        }
        strategic = CountingPilot(ArgentumDecisionChoice(response=response))
        router = RoutingPilot(strategic)

        choice = choose_for_observation(router, observation(None, pending=pending))

        self.assertEqual(strategic.calls, 1)
        self.assertEqual(choice.response, response)
        self.assertEqual(choice.metadata["routing"]["path"], "strategic")

    def test_engine_default_damage_and_auto_pay_avoid_strategic_wakes(self):
        cases = [
            (
                {
                    "decisionId": "damage-1",
                    "type": "AssignDamageDecision",
                    "kind": "AssignDamageDecision",
                    "requiresStructuredResponse": True,
                    "defaultAssignments": {"blocker-1": 3},
                },
                {
                    "type": "DamageAssignmentResponse",
                    "decisionId": "damage-1",
                    "assignments": {"blocker-1": 3},
                },
            ),
            (
                {
                    "decisionId": "mana-1",
                    "type": "SelectManaSourcesDecision",
                    "kind": "SelectManaSourcesDecision",
                    "requiresStructuredResponse": True,
                    "autoPaySuggestion": ["island-1"],
                    "canDecline": False,
                },
                {
                    "type": "ManaSourcesSelectedResponse",
                    "decisionId": "mana-1",
                    "selectedSources": [],
                    "autoPay": True,
                    "waterbendPermanents": [],
                    "declined": False,
                },
            ),
        ]
        for pending, expected in cases:
            with self.subTest(kind=pending["kind"]):
                strategic = CountingPilot(error=AssertionError("strategic pilot should not wake"))
                choice = choose_for_observation(
                    RoutingPilot(strategic),
                    observation(None, pending=pending),
                )
                self.assertEqual(strategic.calls, 0)
                self.assertEqual(choice.response, expected)
                self.assertEqual(choice.metadata["routing"]["path"], "mechanical")

    def test_unique_structured_choice_avoids_strategic_wake(self):
        pending = {
            "decisionId": "number-1",
            "type": "ChooseNumberDecision",
            "kind": "ChooseNumberDecision",
            "requiresStructuredResponse": True,
            "minValue": 4,
            "maxValue": 4,
        }
        strategic = CountingPilot(error=AssertionError("strategic pilot should not wake"))

        choice = choose_for_observation(
            RoutingPilot(strategic),
            observation(None, pending=pending),
        )

        self.assertEqual(strategic.calls, 0)
        self.assertEqual(
            choice.response,
            {"type": "NumberChosenResponse", "decisionId": "number-1", "number": 4},
        )
        self.assertEqual(choice.metadata["routing"]["path"], "mechanical")

    def test_no_choice_combat_avoids_strategic_wake(self):
        attack = {
            "actionId": 4,
            "semanticId": "argentum-action-v1:attack-none",
            "kind": "DeclareAttackers",
            "description": "Declare attackers",
            "affordable": True,
            "validAttackers": [],
        }
        strategic = CountingPilot(error=AssertionError("strategic pilot should not wake"))

        choice = choose_for_observation(RoutingPilot(strategic), observation(attack))

        self.assertEqual(strategic.calls, 0)
        self.assertEqual(choice.action_id, 4)
        self.assertEqual(choice.metadata["routing"]["handler"], "no-choice-combat")

    def test_folded_single_decision_option_can_be_certified_mechanical(self):
        folded = {
            "actionId": 9,
            "semanticId": "argentum-response-v1:only-option",
            "kind": "DECISION",
            "description": "Only legal option",
            "affordable": True,
            "isDecisionOption": True,
            "hasXCost": False,
            "minTargets": 0,
            "maxTargets": 0,
        }
        strategic = CountingPilot(ArgentumActionChoice(action_id=999))
        router = RoutingPilot(strategic)

        choice = choose_for_observation(router, observation(folded))

        self.assertEqual(strategic.calls, 0)
        self.assertEqual(choice.action_id, 9)
        self.assertEqual(choice.metadata["routing"]["path"], "mechanical")

    def test_strategic_failure_propagates_fail_closed(self):
        attack = {
            "actionId": 4,
            "semanticId": "argentum-action-v1:attack",
            "kind": "DeclareAttackers",
            "description": "Declare attackers",
            "affordable": True,
            "isDecisionOption": False,
            "validAttackers": ["creature-1"],
        }
        strategic = CountingPilot(error=RuntimeError("provider unavailable"))
        router = RoutingPilot(strategic)

        with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
            router.choose(observation(attack))

        self.assertEqual(strategic.calls, 1)


if __name__ == "__main__":
    unittest.main()
