import unittest

from commander_gym.pilot import (
    ArgentumActionChoice,
    ArgentumDecisionChoice,
    choose_for_observation,
)
from commander_gym.pilot_routing import (
    AllUnaffordablePassHandler, NativeNoChoiceHandler, RoutingPilot, StandingManaOnlyPassHandler,
)


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
    def test_all_unaffordable_pass_requires_complete_native_false_certificates(self):
        passed = {
            **pass_action(), "actionType": "PassPriority", "isAffordable": True,
            "isManaAbility": False,
            "action": {"type": "PassPriority", "playerId": "player-1"},
        }
        cast = {
            "actionId": 1, "semanticId": "native-cast", "kind": "CastSpell",
            "actionType": "CastSpell", "affordable": False, "isAffordable": False,
            "isManaAbility": False, "isDecisionOption": False,
            "action": {"type": "CastSpell", "playerId": "player-1"},
        }
        kicker = {**cast, "actionId": 2, "semanticId": "native-kicker",
                  "kind": "CastWithKicker", "actionType": "CastWithKicker"}
        ability = {**cast, "actionId": 3, "semanticId": "native-ability",
                   "kind": "ActivateAbility", "actionType": "ActivateAbility",
                   "isManaAbility": True,
                   "action": {"type": "ActivateAbility", "playerId": "player-1"}}
        current = observation(passed)
        current["state"] = {"priorityPlayerId": "player-1"}
        current["legalActions"] = [passed, kicker, cast, ability]
        handler = AllUnaffordablePassHandler()
        self.assertEqual(handler.choose(current).action_id, 0)
        self.assertIsNone(StandingManaOnlyPassHandler(
            ignore_unaffordable_abilities=True,
        ).choose(current))

        mana = {**ability, "actionId": 4, "semanticId": "native-mana",
                "affordable": True, "isAffordable": True}
        standing = {**current, "legalActions": [passed, mana]}
        self.assertEqual(handler.choose(standing).action_id, 0)
        for changed in (
            {**standing, "state": {"priorityPlayerId": "other"}},
            {**standing, "legalActions": [passed, {**mana, "semanticId": passed["semanticId"]}]},
            {**standing, "legalActions": [{**passed, "isAffordable": False}, mana]},
            {**standing, "legalActions": [passed, {**mana, "actionType": "CastSpell"}]},
            {**standing, "legalActions": [passed, {**mana, "isAffordable": None}]},
        ):
            with self.subTest(inherited_fast_path=changed):
                self.assertIsNone(handler.choose(changed))

        for key, value in (("affordable", True), ("affordable", None),
                           ("isAffordable", True), ("isAffordable", None),
                           ("isDecisionOption", None), ("kind", "Unknown"),
                           ("actionType", "Unknown"), ("semanticId", "")):
            with self.subTest(key=key, value=value):
                changed = {**cast, key: value}
                self.assertIsNone(handler.choose({**current, "legalActions": [passed, changed, kicker]}))
        for changed in (
            {**passed, "isAffordable": None},
            {**passed, "action": {"type": "PassPriority", "playerId": "other"}},
            {**passed, "actionId": True},
        ):
            with self.subTest(changed=changed):
                self.assertIsNone(handler.choose({**current, "legalActions": [changed, cast]}))
        for changed in (
            {**current, "pendingDecision": {"kind": "SelectCards"}},
            {**current, "terminated": True},
            {**current, "state": {"priorityPlayerId": "other"}},
            {**current, "state": None},
            {**current, "legalActions": [passed, cast, {**cast, "actionId": 0}]},
        ):
            with self.subTest(changed=changed):
                self.assertIsNone(handler.choose(changed))

    def test_all_unaffordable_pass_certifies_only_plain_unpayable_native_cycling(self):
        passed = {
            **pass_action(), "actionType": "PassPriority", "isAffordable": True,
            "isManaAbility": False,
            "action": {"type": "PassPriority", "playerId": "player-1"},
        }
        cycle = {
            "actionId": 1, "semanticId": "native-cycle", "kind": "CycleCard",
            "actionType": "CycleCard", "affordable": False, "isAffordable": False,
            "isManaAbility": False, "isDecisionOption": False,
            "hasXCost": False, "maxAffordableX": None, "manaCostString": "{2}",
            "hasConvoke": False, "hasDelve": False, "hasHarmonize": False,
            "hasTapForGeneric": False, "additionalCostInfo": None,
            "sourceZone": None, "requiresTargets": False, "validTargets": None,
            "requiresManaColorChoice": False,
            "requiresDamageDistribution": False, "modalEnumeration": None,
            "parameterSpec": {"allowedFields": {}},
            "action": {"type": "CycleCard", "playerId": "player-1", "cardId": "hand-1",
                       "paymentStrategy": {"type": "AutoPay"}, "xValue": None},
        }
        current = observation(passed)
        current["state"] = {
            "priorityPlayerId": "player-1",
            "zones": [{"zoneId": {"ownerId": "player-1", "zoneType": "Hand"},
                       "cardIds": ["hand-1"]}],
        }
        current["legalActions"] = [passed, cycle]
        handler = AllUnaffordablePassHandler(version="2", allow_unaffordable_cycling=True)
        self.assertIsNone(AllUnaffordablePassHandler().choose(current))
        self.assertEqual(handler.choose(current).action_id, 0)
        self.assertIsNone(StandingManaOnlyPassHandler(
            ignore_unaffordable_abilities=True,
        ).choose(current))
        strategic = CountingPilot(error=AssertionError("unavailable cycling woke model"))
        self.assertEqual(RoutingPilot(strategic, handlers=(handler,)).choose(current).action_id, 0)
        self.assertEqual(strategic.calls, 0)

        for field, value in (
            ("affordable", True), ("affordable", None),
            ("isAffordable", True), ("isAffordable", None),
            ("isManaAbility", True), ("isDecisionOption", True),
            ("hasXCost", True), ("maxAffordableX", 0),
            ("hasDelve", True), ("hasConvoke", True),
            ("hasHarmonize", True), ("hasTapForGeneric", True),
            ("additionalCostInfo", {"kind": "discard"}),
            ("sourceZone", "EXILE"), ("requiresTargets", True),
            ("validTargets", ["target"]),
            ("requiresManaColorChoice", True),
            ("requiresDamageDistribution", True),
            ("modalEnumeration", {}),
            ("parameterSpec", {"allowedFields": {"xValue": "INTEGER"}}),
            ("parameterSpec", None), ("manaCostString", ""),
            ("kind", "TypecycleCard"), ("actionType", "TypecycleCard"),
            ("semanticId", ""),
        ):
            with self.subTest(field=field, value=value):
                changed = {**cycle, field: value}
                self.assertIsNone(handler.choose({**current, "legalActions": [passed, changed]}))
        for action in (
            {**cycle["action"], "playerId": "other"},
            {**cycle["action"], "cardId": "not-in-hand"},
            {**cycle["action"], "paymentStrategy": {"type": "Explicit"}},
            {**cycle["action"], "xValue": 1},
            {**cycle["action"], "extra": True},
            {k: v for k, v in cycle["action"].items() if k != "cardId"},
        ):
            with self.subTest(action=action):
                self.assertIsNone(handler.choose({**current, "legalActions": [
                    passed, {**cycle, "action": action},
                ]}))
        for state in (
            {"priorityPlayerId": "other", "zones": current["state"]["zones"]},
            {"priorityPlayerId": "player-1", "zones": None},
            {"priorityPlayerId": "player-1", "zones": []},
            {"priorityPlayerId": "player-1", "zones": [{
                "zoneId": {"ownerId": "other", "zoneType": "Hand"}, "cardIds": ["hand-1"],
            }]},
            {"priorityPlayerId": "player-1", "zones": [
                *current["state"]["zones"], *current["state"]["zones"],
            ]},
        ):
            with self.subTest(state=state):
                self.assertIsNone(handler.choose({**current, "state": state}))
        for changed in (
            {**current, "pendingDecision": {"kind": "SelectCardsDecision"}},
            {**current, "terminated": True},
            {**current, "perspectivePlayerId": "other"},
            {**current, "legalActions": [passed, cycle, {
                "actionId": 2, "semanticId": "native-other", "kind": "CastSpell",
                "actionType": "CastSpell", "affordable": True, "isAffordable": True,
                "isManaAbility": False, "isDecisionOption": False,
                "action": {"type": "CastSpell", "playerId": "player-1"},
            }]},
        ):
            with self.subTest(changed=changed):
                self.assertIsNone(handler.choose(changed))

    def test_opt_in_standing_pass_validates_sole_native_action_before_no_choice(self):
        passed = {
            **pass_action(), "isManaAbility": False,
            "action": {"type": "PassPriority", "playerId": "player-1"},
        }
        handler = StandingManaOnlyPassHandler()
        base = observation(passed)
        self.assertEqual(handler.choose(base).action_id, 0)
        malformed = [
            {**base, "terminated": True},
            {**base, "pendingDecision": {"requiresStructuredResponse": True}},
            {**base, "perspectivePlayerId": "other-seat"},
            {**base, "agentToAct": "other-seat"},
            {**base, "legalActions": [{**passed, "action": {"type": "CastSpell", "playerId": "player-1"}}]},
            {**base, "legalActions": [{**passed, "action": {"type": "ActivateAbility", "playerId": "player-1"}}]},
            {**base, "legalActions": [{**passed, "action": {"type": "PassPriority", "playerId": "other-seat"}}]},
            {**base, "legalActions": [{**passed, "semanticId": ""}]},
            {**base, "legalActions": [{**passed, "affordable": None}]},
            {**base, "legalActions": [{**passed, "affordable": False}]},
            {**base, "legalActions": [{**passed, "isManaAbility": None}]},
            {**base, "legalActions": [{**passed, "isDecisionOption": True}]},
            {**base, "legalActions": [{**passed, "actionId": True}]},
        ]
        for altered in malformed:
            with self.subTest(altered=altered):
                # The qualified no-choice handler is unchanged, but the new
                # opt-in handler must never inherit its lax sole-pass path.
                self.assertIsNone(handler.choose(altered))

    def test_opt_in_standing_pass_keeps_valid_empty_combat_but_checks_its_wire(self):
        handler = StandingManaOnlyPassHandler()
        for kind, candidates, declaration in (
            ("DeclareAttackers", "validAttackers", "attackers"),
            ("DeclareBlockers", "validBlockers", "blockers"),
        ):
            action = {
                "actionId": 0, "semanticId": f"argentum-action-v1:{kind}",
                "kind": kind, "affordable": True, "isDecisionOption": False,
                candidates: [],
                "action": {"type": kind, "playerId": "player-1", declaration: {}},
            }
            with self.subTest(kind=kind):
                current = observation(action)
                self.assertEqual(handler.choose(current).action_id, 0)
                self.assertIsNone(handler.choose({**current, "legalActions": [
                    {**action, "action": {**action["action"], "playerId": "other-seat"}}
                ]}))
                self.assertIsNone(handler.choose({**current, "legalActions": [
                    {**action, "semanticId": ""}
                ]}))

    def test_opt_in_standing_mana_pass_handles_changed_stack_without_model(self):
        passed = {
            **pass_action(), "isManaAbility": False,
            "action": {"type": "PassPriority", "playerId": "player-1"},
        }
        mana = {
            "actionId": 1, "semanticId": "argentum-action-v1:mana",
            "kind": "ActivateAbility", "isManaAbility": True,
            "affordable": False,
            "action": {"type": "ActivateAbility", "playerId": "player-1"},
        }
        strategic = CountingPilot(ArgentumActionChoice(action_id=1))
        pilot = RoutingPilot(strategic, handlers=(StandingManaOnlyPassHandler(),))
        for stack in ([], ["new-opponent-spell"]):
            with self.subTest(stack=stack):
                current = observation(passed)
                current["legalActions"] = [passed, mana]
                current["state"] = {"stack": stack}
                choice = choose_for_observation(pilot, current)
                self.assertEqual(choice.action_id, 0)
                self.assertEqual(choice.metadata["routing"]["handler"], "standing-mana-only-pass")
        self.assertEqual(strategic.calls, 0)
        # The qualified native-no-choice component remains a distinct policy.
        self.assertIsNone(NativeNoChoiceHandler().choose(current))

    def test_opt_in_standing_pass_escalates_for_real_choice_or_malformed_menu(self):
        passed = {
            **pass_action(), "isManaAbility": False,
            "action": {"type": "PassPriority", "playerId": "player-1"},
        }
        mana = {
            "actionId": 1, "semanticId": "argentum-action-v1:mana",
            "kind": "ActivateAbility", "isManaAbility": True, "affordable": True,
            "action": {"type": "ActivateAbility", "playerId": "player-1"},
        }
        handler = StandingManaOnlyPassHandler()
        base = observation(passed)
        base["legalActions"] = [passed, mana]
        variations = [
            {**base, "pendingDecision": {"requiresStructuredResponse": True}},
            {**base, "terminated": True},
            {**base, "perspectivePlayerId": "other-seat"},
            {**base, "legalActions": [passed, mana, {
                **mana, "actionId": 2, "kind": "CastSpell",
                "action": {"type": "CastSpell", "playerId": "player-1"},
            }]},
            {**base, "legalActions": [passed, {**mana, "isManaAbility": False}]},
            {**base, "legalActions": [passed, {**mana, "kind": "CycleCard",
                "action": {"type": "CycleCard", "playerId": "player-1"}}]},
            {**base, "legalActions": [passed, {**mana, "isDecisionOption": True}]},
            {**base, "legalActions": [passed, {**mana, "actionId": 0}]},
            {**base, "legalActions": [passed, {**mana, "actionId": True}]},
            {**base, "legalActions": [passed, {**mana, "semanticId": ""}]},
            {**base, "legalActions": [passed, {**mana, "action": {"type": "ActivateAbility", "playerId": "other-seat"}}]},
            {**base, "legalActions": [passed, {**mana, "isManaAbility": None}]},
            {**base, "legalActions": [passed, None]},
            {**base, "legalActions": [passed, mana, passed]},
            {**base, "legalActions": []},
        ]
        for altered in variations:
            with self.subTest(altered=altered):
                self.assertIsNone(handler.choose(altered))

    def test_versioned_forge_actionability_skips_only_native_unaffordable_ability(self):
        passed = {**pass_action(), "isManaAbility": False,
                  "action": {"type": "PassPriority", "playerId": "player-1"}}
        ability = {"actionId": 1, "semanticId": "native-ability", "kind": "ActivateAbility",
                   "isManaAbility": False, "affordable": False, "isAffordable": False,
                   "action": {"type": "ActivateAbility", "playerId": "player-1"}}
        current = observation(passed)
        current["legalActions"] = [passed, ability]
        old = StandingManaOnlyPassHandler()
        new = StandingManaOnlyPassHandler(
            name="native-unaffordable-ability-pass", version="2",
            ignore_unaffordable_abilities=True,
        )
        self.assertIsNone(old.choose(current))
        self.assertEqual(new.choose(current).action_id, passed["actionId"])
        for changed in (
            {**ability, "affordable": True},
            {**ability, "isAffordable": True},
            {**ability, "affordable": None},
            {**ability, "kind": "CastSpell", "action": {"type": "CastSpell", "playerId": "player-1"}},
        ):
            with self.subTest(changed=changed):
                current["legalActions"] = [passed, changed]
                self.assertIsNone(new.choose(current))

    def test_native_no_choice_never_suppresses_a_mana_choice(self):
        handler = NativeNoChoiceHandler()
        pass_only = observation(pass_action())
        pass_only["legalActions"].append({
            "actionId": 1, "kind": "ActivateAbility", "isManaAbility": True,
            "description": "Add mana",
            "action": {
                "type": "ActivateAbility", "abilityId": "intrinsic_mana_U",
                "targets": [], "costPayment": None, "alternativePayment": None,
                "repeatCount": 1, "opponentTargetsChosen": False,
            },
        })
        # Replay regression: in an own-main pass-plus-mana window the strategic
        # pilot chose to add mana. Legal mana actions are not a forced pass.
        pass_only["state"] = {"turnNumber": 3, "currentStep": "PRECOMBAT_MAIN"}
        self.assertIsNone(handler.choose(pass_only))
        for change in (
            {"isManaAbility": False},
            {"kind": "CastSpell"},
            {"isDecisionOption": True},
        ):
            with self.subTest(change=change):
                altered = {**pass_only, "legalActions": [pass_action(), {**pass_only["legalActions"][1], **change}]}
                self.assertIsNone(handler.choose(altered))
        self.assertIsNone(handler.choose({**pass_only, "pendingDecision": {"kind": "SelectCards"}}))

    def test_native_no_choice_confirms_only_empty_combat(self):
        handler = NativeNoChoiceHandler()
        for kind, field, choice_field in (
            ("DeclareAttackers", "validAttackers", "attackers"),
            ("DeclareBlockers", "validBlockers", "blockers"),
        ):
            action = {
                "actionId": 0, "kind": kind, field: [],
                "action": {"type": kind, choice_field: {}},
            }
            with self.subTest(kind=kind):
                self.assertEqual(handler.choose(observation(action)).action_id, 0)
                self.assertIsNone(handler.choose(observation({**action, field: ["candidate"]})))
                self.assertIsNone(handler.choose(observation({**action, "action": {"type": kind, choice_field: {"candidate": "target"}}})))

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

    def test_structured_decision_always_escalates(self):
        pending = {
            "decisionId": "routing-1",
            "semanticId": "argentum-decision-v1:targets",
            "kind": "CHOOSE_TARGETS",
            "requiresStructuredResponse": True,
        }
        response = {
            "type": "ChooseTargetsResponse",
            "decisionId": "routing-1",
            "targets": ["target-1"],
        }
        strategic = CountingPilot(ArgentumDecisionChoice(response=response))
        router = RoutingPilot(strategic)

        choice = choose_for_observation(router, observation(None, pending=pending))

        self.assertEqual(strategic.calls, 1)
        self.assertEqual(choice.response, response)
        self.assertEqual(choice.metadata["routing"]["path"], "strategic")

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
