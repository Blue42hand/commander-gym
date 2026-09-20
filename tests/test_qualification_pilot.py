import unittest

from commander_gym.pilot import PilotContractError, choose_for_observation
from commander_gym.qualification_pilot import QualificationAggroPilot


def observation(actions, *, pending=None):
    return {
        "type": "Game",
        "stateDigest": "state-1",
        "perspectivePlayerId": "e0",
        "agentToAct": "e0",
        "pendingDecision": pending,
        "legalActions": actions,
        "terminated": False,
    }


def action(action_id, kind, **extra):
    return {
        "actionId": action_id,
        "semanticId": f"semantic:{action_id}",
        "kind": kind,
        "description": kind,
        "affordable": True,
        **extra,
    }


class QualificationAggroPilotTests(unittest.TestCase):
    def test_attacks_every_candidate_at_first_legal_target(self):
        pilot = QualificationAggroPilot()
        obs = observation(
            [
                action(
                    7,
                    "DeclareAttackers",
                    validAttackers=["creature-a", "creature-b"],
                    validAttackTargets=["e1", "e2"],
                )
            ]
        )

        choice = choose_for_observation(pilot, obs)

        self.assertEqual(choice.action_id, 7)
        self.assertEqual(
            choice.params,
            {"attackers": {"creature-a": "e1", "creature-b": "e1"}},
        )
        self.assertEqual(choice.metadata["policyDecision"], "attack-all-first-target")

    def test_declines_optional_blocks(self):
        pilot = QualificationAggroPilot()
        obs = observation(
            [
                action(
                    8,
                    "DeclareBlockers",
                    validBlockers=["creature-a"],
                    mandatoryBlockerAssignments={},
                )
            ]
        )

        choice = choose_for_observation(pilot, obs)

        self.assertEqual(choice.action_id, 8)
        self.assertEqual(choice.params, {})
        self.assertEqual(choice.metadata["policyDecision"], "declare-no-blockers")

    def test_prefers_land_cast_and_activation_over_pass(self):
        pilot = QualificationAggroPilot()
        for expected_kind, expected_id in (
            ("PlayLand", 2),
            ("CastSpell", 3),
            ("ActivateAbility", 4),
        ):
            actions = [action(1, "PassPriority"), action(expected_id, expected_kind)]
            choice = choose_for_observation(pilot, observation(actions))
            self.assertEqual(choice.action_id, expected_id)

    def test_ignores_mana_abilities_when_selecting_activation(self):
        pilot = QualificationAggroPilot()
        actions = [
            action(1, "PassPriority"),
            action(2, "ActivateAbility", isManaAbility=True),
            action(3, "ActivateAbility", isManaAbility=True),
            action(4, "ActivateAbility", isManaAbility=False),
        ]

        choice = choose_for_observation(pilot, observation(actions))

        self.assertEqual(choice.action_id, 4)
        self.assertEqual(
            choice.metadata["policyDecision"], "activate-non-mana-ability"
        )

    def test_passes_when_only_mana_abilities_are_available(self):
        pilot = QualificationAggroPilot()
        actions = [
            action(1, "PassPriority"),
            action(2, "ActivateAbility", isManaAbility=True),
            action(3, "ActivateAbility", isManaAbility=True),
        ]

        choice = choose_for_observation(pilot, observation(actions))

        self.assertEqual(choice.action_id, 1)

    def test_selects_equivalent_lands_by_semantic_id(self):
        pilot = QualificationAggroPilot()
        actions = [
            action(8, "PlayLand", description="Play Mountain"),
            action(3, "PlayLand", description="Play Mountain"),
            action(1, "PassPriority"),
        ]

        choice = choose_for_observation(pilot, observation(actions))

        self.assertEqual(choice.action_id, 3)
        self.assertEqual(
            choice.metadata["policyDecision"],
            "play-land-deterministic-equivalent",
        )

    def test_fails_closed_for_non_equivalent_lands(self):
        pilot = QualificationAggroPilot()
        actions = [
            action(2, "PlayLand", description="Play Mountain"),
            action(3, "PlayLand", description="Play Valakut"),
        ]

        with self.assertRaisesRegex(PilotContractError, "non-equivalent PlayLand"):
            pilot.choose(observation(actions))

    def test_fails_closed_for_mandatory_blocks(self):
        pilot = QualificationAggroPilot()
        obs = observation(
            [
                action(
                    8,
                    "DeclareBlockers",
                    validBlockers=["creature-a"],
                    mandatoryBlockerAssignments={"creature-a": ["attacker-a"]},
                )
            ]
        )

        with self.assertRaisesRegex(PilotContractError, "mandatory blocker"):
            pilot.choose(obs)

    def test_fails_closed_for_unknown_action_surface(self):
        pilot = QualificationAggroPilot()

        with self.assertRaisesRegex(PilotContractError, "does not support"):
            pilot.choose(observation([action(9, "ChooseSecretMode")]))


if __name__ == "__main__":
    unittest.main()
