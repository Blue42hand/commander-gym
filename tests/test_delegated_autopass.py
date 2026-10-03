import copy
import json
import unittest

from commander_gym.delegated_autopass import DelegatedAutopassPilot
from commander_gym.openai_responses_pilot import OpenAIResponsesPilot
from commander_gym.pilot import ArgentumActionChoice, ArgentumDecisionChoice


def observation(*, turn=1, step="PRECOMBAT_MAIN", legal=None):
    return {
        "agentToAct": "p1", "perspectivePlayerId": "p1", "terminated": False,
        "pendingDecision": None,
        "legalActions": legal if legal is not None else [
            {"actionId": 0, "kind": "PassPriority"},
            {"actionId": 1, "kind": "ActivateAbility", "isManaAbility": True},
        ],
        "state": {
            "turnNumber": turn, "currentStep": step, "currentPhase": (
                "COMBAT" if step in {"DECLARE_ATTACKERS", "DECLARE_BLOCKERS"} else step
            ), "activePlayerId": "p1",
            "priorityPlayerId": "p1",
            "players": [
                {"playerId": "p1", "life": 40, "poisonCounters": 0,
                 "commanderDamage": [], "manaPool": {"red": 0, "restrictedMana": []}},
                {"playerId": "p2", "life": 40, "poisonCounters": 0,
                 "commanderDamage": [], "manaPool": {"blue": 0, "restrictedMana": []}},
            ],
            "zones": [
                {"zoneId": {"ownerId": "p1", "zoneType": "Hand"}, "cardIds": ["h1"]},
                {"zoneId": {"ownerId": "p1", "zoneType": "Battlefield"}, "cardIds": ["b1"]},
                {"zoneId": {"ownerId": "p2", "zoneType": "Battlefield"}, "cardIds": []},
                {"zoneId": {"ownerId": "p1", "zoneType": "Stack"}, "cardIds": []},
            ],
            "cards": {"b1": {"name": "Land", "tapped": False}},
            "gameLog": [],
        },
    }


class ScriptedPilot:
    name = "scripted"
    version = "1"

    def __init__(self, until="phase_end", watch_opponents=False):
        self.calls = 0
        self.until = until
        self.watch_opponents = watch_opponents

    def choose(self, observation):
        self.calls += 1
        if observation.get("pendingDecision") is not None:
            return ArgentumDecisionChoice({"type": "NumberChosenResponse", "decisionId": "d1", "number": 1})
        return ArgentumActionChoice(
            0, metadata=(
                {"priorityDelegation": {
                    "until": self.until, "reason": "Reviewed no action this phase",
                    "watchOpponents": self.watch_opponents,
                }}
                if self.calls == 1 else {}
            ),
        )


class DelegatedAutopassTests(unittest.TestCase):
    def test_versioned_short_wait_uses_same_opponent_watch_on_both_callbacks(self):
        for boundary in ("phase_end", "next_own_main"):
            with self.subTest(boundary=boundary):
                strategic = ScriptedPilot(until=boundary)
                pilot = DelegatedAutopassPilot(strategic, version="2", allow_named_deferrals=True)
                start = observation()
                pilot.choose(start)
                self.assertIn("delegatedPass", pilot.choose(copy.deepcopy(start)).metadata)
                self.assertEqual(strategic.calls, 1)

    def test_forge_then_cast_runs_only_after_isolated_native_land_transition(self):
        class LandPilot:
            name, version = "scripted", "1"
            calls = 0

            def choose(self, observation):
                self.calls += 1
                return ArgentumActionChoice(0, metadata={"thenCast": {
                    "cardId": "h2", "reason": "Play the planned follow-up creature",
                }}) if self.calls == 1 else ArgentumActionChoice(0)

        strategic = LandPilot()
        pilot = DelegatedAutopassPilot(strategic, version="2", allow_named_deferrals=True)
        start = observation(legal=[
            {"actionId": 0, "kind": "PlayLand", "action": {"cardId": "h1"}},
        ])
        start["state"]["zones"][0]["cardIds"] = ["h1", "h2"]
        pilot.choose(start)
        later = copy.deepcopy(start)
        later["state"]["zones"][0]["cardIds"] = ["h2"]
        later["state"]["zones"][1]["cardIds"] = ["b1", "h1"]
        later["state"]["cards"]["h1"] = {"name": "Played Land", "tapped": False}
        later["state"]["gameLog"] = [{"type": "permanentEntered"}]
        later["legalActions"] = [{"actionId": 7, "kind": "CastSpell", "affordable": True,
                                  "isAffordable": True, "hasXCost": False,
                                  "additionalCostInfo": None,
                                  "parameterSpec": {"allowedFields": {}},
                                  "action": {"type": "CastSpell", "playerId": "p1", "cardId": "h2",
                                             "additionalCostPayment": None,
                                             "alternativeCostType": None,
                                             "alternativePayment": None,
                                             "casualtyCreature": None,
                                             "chosenModes": [], "targets": [],
                                             "modeTargetsOrdered": [], "splicedCardIds": [],
                                             "conspiredCreatures": [], "damageDistribution": None,
                                             "declaredCostSlot": None, "faceIndex": None,
                                             "giftRecipient": None, "graveyardCastRider": None,
                                             "graveyardLifeCost": 0, "modeDamageDistribution": {},
                                             "wasWaterbendPaid": False, "xValue": None,
                                             "paymentStrategy": {"type": "AutoPay"},
                                             "useAlternativeCost": False,
                                             "useWithoutPayingManaCost": False,
                                             "castFaceDown": False}}]
        result = pilot.choose(later)
        self.assertEqual(result.action_id, 7)
        self.assertEqual(result.metadata["forgeThenCast"]["cardId"], "h2")
        self.assertEqual(strategic.calls, 1)

        for change in ("stack", "opponent", "unaffordable", "ambiguous",
                       "targeted_template", "x_cost", "additional_cost", "mode"):
            with self.subTest(change=change):
                strategic = LandPilot()
                pilot = DelegatedAutopassPilot(strategic, allow_named_deferrals=True)
                pilot.choose(start)
                interrupted = copy.deepcopy(later)
                if change == "stack":
                    interrupted["state"]["zones"][-1]["cardIds"] = ["new-spell"]
                elif change == "opponent":
                    interrupted["state"]["zones"][2]["cardIds"] = ["new-permanent"]
                    interrupted["state"]["cards"]["new-permanent"] = {"controllerId": "p2"}
                elif change == "unaffordable":
                    interrupted["legalActions"][0]["affordable"] = False
                elif change == "ambiguous":
                    interrupted["legalActions"].append({**interrupted["legalActions"][0],
                                                        "actionId": 8})
                elif change == "targeted_template":
                    # Real Argentum CastSpell offers currently use this broad
                    # ActionParams template even for otherwise simple cards.
                    interrupted["legalActions"][0]["parameterSpec"]["allowedFields"] = {
                        "exiledCards": "ENTITY_ID_ARRAY", "targets": "ENTITY_ID_ARRAY",
                        "xValue": "INTEGER",
                    }
                elif change == "x_cost":
                    interrupted["legalActions"][0]["hasXCost"] = True
                elif change == "additional_cost":
                    interrupted["legalActions"][0]["additionalCostInfo"] = {"kind": "sacrifice"}
                else:
                    interrupted["legalActions"][0]["action"]["chosenModes"] = ["first"]
                pilot.choose(interrupted)
                self.assertEqual(strategic.calls, 2)

    def test_named_forge_wait_crosses_phase_then_wakes_at_turn_end(self):
        class NamedPilot:
            name, version = "scripted", "1"
            calls = 0

            def choose(self, observation):
                self.calls += 1
                return ArgentumActionChoice(0, metadata={"priorityDelegation": {
                    "until": "turn_end", "reason": "Hold Mind Stone through this turn",
                    "deferAbilities": [{"sourceId": "stone", "abilityId": "draw"}],
                }} if self.calls == 1 else {})

        legal = [
            {"actionId": 0, "kind": "PassPriority"},
            {"actionId": 1, "kind": "ActivateAbility", "isManaAbility": False,
             "action": {"sourceId": "stone", "abilityId": "draw"}},
        ]
        strategic = NamedPilot()
        pilot = DelegatedAutopassPilot(strategic, version="2", allow_named_deferrals=True)
        start = observation(legal=legal)
        pilot.choose(start)
        later = observation(step="POSTCOMBAT_MAIN", legal=copy.deepcopy(legal))
        self.assertEqual(pilot.choose(later).metadata["delegatedPass"]["ordinal"], 1)
        self.assertEqual(strategic.calls, 1)
        pilot.choose(observation(turn=2, legal=copy.deepcopy(legal)))
        self.assertEqual(strategic.calls, 2)

    def test_named_wait_wakes_on_changed_menu_state_and_decision(self):
        class NamedPilot:
            name, version = "scripted", "1"
            calls = 0

            def choose(self, observation):
                self.calls += 1
                return ArgentumActionChoice(0, metadata={"priorityDelegation": {
                    "until": "turn_end", "reason": "Defer current ability",
                    "deferAbilities": [{"sourceId": "stone", "abilityId": "draw"}],
                }} if self.calls == 1 else {})

        legal = [{"actionId": 0, "kind": "PassPriority"},
                 {"actionId": 1, "kind": "ActivateAbility", "isManaAbility": False,
                  "action": {"sourceId": "stone", "abilityId": "draw"}}]
        for change in ("spell", "ability", "parameters", "opponent", "stack", "decision"):
            with self.subTest(change=change):
                strategic = NamedPilot()
                pilot = DelegatedAutopassPilot(strategic, allow_named_deferrals=True)
                base = observation(legal=copy.deepcopy(legal))
                pilot.choose(base)
                later = copy.deepcopy(base)
                if change == "spell":
                    later["legalActions"].append({"actionId": 2, "kind": "CastSpell"})
                elif change == "ability":
                    later["legalActions"][1]["action"]["abilityId"] = "different"
                elif change == "parameters":
                    later["legalActions"][1]["parameterSpec"] = {"allowedFields": {"targets": "LIST"}}
                elif change == "opponent":
                    later["state"]["zones"][2]["cardIds"] = ["new"]
                    later["state"]["cards"]["new"] = {"controllerId": "p2"}
                elif change == "stack":
                    later["state"]["zones"][-1]["cardIds"] = ["spell"]
                else:
                    later["pendingDecision"] = {"decisionId": "d1"}
                pilot.choose(later)
                self.assertEqual(strategic.calls, 2)

    def test_named_wait_rejects_incomplete_deferral(self):
        class NamedPilot:
            name, version = "scripted", "1"

            def choose(self, observation):
                return ArgentumActionChoice(0, metadata={"priorityDelegation": {
                    "until": "turn_end", "reason": "Skip", "deferAbilities": [],
                }})

        pilot = DelegatedAutopassPilot(NamedPilot(), allow_named_deferrals=True)
        legal = [{"actionId": 0, "kind": "PassPriority"},
                 {"actionId": 1, "kind": "ActivateAbility", "isManaAbility": False,
                  "action": {"sourceId": "stone", "abilityId": "draw"}}]
        self.assertIn("priorityDelegationRejected", pilot.choose(observation(legal=legal)).metadata)

    def test_one_model_approved_pass_covers_later_native_mana_window(self):
        class Responses:
            calls = 0

            def create(self, **kwargs):
                self.calls += 1
                return type("Response", (), {
                    "status": "completed",
                    "output_text": json.dumps({
                        "channel": "action", "semanticId": "native-pass",
                        "params": {},
                        "priorityDelegation": {
                            "until": "phase_end", "reason": "Reviewed this priority window",
                        },
                    }),
                })()

        client = type("Client", (), {"responses": Responses()})()
        strategic = OpenAIResponsesPilot(
            client=client, model="gpt-test", allow_priority_delegation=True,
        )
        pilot = DelegatedAutopassPilot(strategic)
        current = observation()
        current["legalActions"][0]["semanticId"] = "native-pass"
        current["legalActions"][1]["semanticId"] = "native-mana"
        self.assertEqual(pilot.choose(current).action_id, 0)
        later = copy.deepcopy(current)
        self.assertEqual(pilot.choose(later).metadata["delegatedPass"]["ordinal"], 1)
        self.assertEqual(client.responses.calls, 1)

    def test_approved_wait_passes_mana_only_and_expires_at_phase_boundary(self):
        strategic = ScriptedPilot()
        pilot = DelegatedAutopassPilot(strategic)
        first = observation()
        pilot.choose(first)
        self.assertEqual(pilot.choose(copy.deepcopy(first)).action_id, 0)
        self.assertEqual(strategic.calls, 1)
        later = observation(step="POSTCOMBAT_MAIN")
        pilot.choose(later)
        self.assertEqual(strategic.calls, 2)

    def test_new_payable_action_stack_or_private_change_wakes(self):
        for change in ("spell", "ability", "stack", "hand", "battlefield", "life", "mana", "log"):
            with self.subTest(change=change):
                strategic = ScriptedPilot()
                pilot = DelegatedAutopassPilot(strategic)
                base = observation()
                pilot.choose(base)
                later = copy.deepcopy(base)
                if change == "spell":
                    later["legalActions"].append({"actionId": 2, "kind": "CastSpell", "affordable": True})
                elif change == "ability":
                    later["legalActions"].append({"actionId": 2, "kind": "ActivateAbility", "isManaAbility": False})
                elif change == "stack":
                    later["state"]["zones"][-1]["cardIds"].append("s1")
                elif change == "hand":
                    later["state"]["zones"][0]["cardIds"].append("h2")
                elif change == "battlefield":
                    later["state"]["cards"]["b1"]["tapped"] = True
                elif change == "life":
                    later["state"]["players"][0]["life"] = 39
                elif change == "mana":
                    later["state"]["players"][0]["manaPool"]["red"] = 1
                elif change == "log":
                    later["state"]["gameLog"].append({"type": "spellCast"})
                pilot.choose(later)
                self.assertEqual(strategic.calls, 2)

    def test_required_decision_missing_state_and_stale_turn_wake(self):
        for change in ("decision", "missing", "stale"):
            with self.subTest(change=change):
                strategic = ScriptedPilot(until="next_own_main")
                pilot = DelegatedAutopassPilot(strategic)
                pilot.choose(observation())
                later = observation()
                if change == "decision":
                    later["pendingDecision"] = {"decisionId": "d1", "requiresStructuredResponse": True}
                elif change == "missing":
                    del later["state"]["gameLog"]
                else:
                    later["state"]["turnNumber"] = 0
                pilot.choose(later)
                self.assertEqual(strategic.calls, 2)

    def test_float_mana_rejects_delegation_and_does_not_arm(self):
        strategic = ScriptedPilot()
        pilot = DelegatedAutopassPilot(strategic)
        first = observation()
        first["state"]["players"][0]["manaPool"]["red"] = 1
        rejected = pilot.choose(first)
        self.assertIn("priorityDelegationRejected", rejected.metadata)
        pilot.choose(first)
        self.assertEqual(strategic.calls, 2)

    def test_next_own_main_and_explicit_opponent_watch(self):
        strategic = ScriptedPilot(until="next_own_main")
        pilot = DelegatedAutopassPilot(strategic)
        pilot.choose(observation())
        opponent_turn = observation(turn=2, step="PRECOMBAT_MAIN")
        opponent_turn["state"]["activePlayerId"] = "p2"
        opponent_turn["state"]["gameLog"] = [{"type": "turnChanged"}]
        opponent_turn["state"]["zones"][2]["cardIds"] = ["opponent-land"]
        opponent_turn["state"]["cards"]["opponent-land"] = {
            "name": "Opponent Land", "controllerId": "p2",
        }
        self.assertEqual(pilot.choose(opponent_turn).metadata["delegatedPass"]["ordinal"], 1)
        own_main = observation(turn=3)
        own_main["state"]["gameLog"] = [{"type": "turnChanged"}, {"type": "turnChanged"}]
        pilot.choose(own_main)
        self.assertEqual(strategic.calls, 2)

        strategic = ScriptedPilot(watch_opponents=True)
        pilot = DelegatedAutopassPilot(strategic)
        pilot.choose(observation())
        opponent_land = observation()
        opponent_land["state"]["zones"][2]["cardIds"] = ["opponent-land"]
        opponent_land["state"]["gameLog"] = [{"type": "permanentEntered"}]
        pilot.choose(opponent_land)
        self.assertEqual(strategic.calls, 2)

    def test_next_own_main_wakes_from_own_upkeep_or_draw_on_same_turn(self):
        for start_step in ("UPKEEP", "DRAW"):
            with self.subTest(start_step=start_step):
                strategic = ScriptedPilot(until="next_own_main")
                pilot = DelegatedAutopassPilot(strategic)
                start = observation(step=start_step)
                # The exact native step identifies the boundary even if a
                # broader phase label is shared across both observations.
                start["state"]["currentPhase"] = "MAIN"
                pilot.choose(start)
                same_step = copy.deepcopy(start)
                self.assertIn("delegatedPass", pilot.choose(same_step).metadata)
                own_main = observation(step="PRECOMBAT_MAIN")
                own_main["state"]["currentPhase"] = "MAIN"
                self.assertNotIn("delegatedPass", pilot.choose(own_main).metadata)
                self.assertEqual(strategic.calls, 2)

    def test_next_own_main_does_not_expire_within_starting_main(self):
        strategic = ScriptedPilot(until="next_own_main")
        pilot = DelegatedAutopassPilot(strategic)
        first = observation(step="PRECOMBAT_MAIN")
        pilot.choose(first)
        self.assertIn("delegatedPass", pilot.choose(copy.deepcopy(first)).metadata)
        self.assertEqual(strategic.calls, 1)

    def test_borrowed_permanent_change_wakes_without_opponent_watch(self):
        for change in ("damage", "control"):
            with self.subTest(change=change):
                strategic = ScriptedPilot()
                pilot = DelegatedAutopassPilot(strategic)
                start = observation(step="DECLARE_ATTACKERS")
                start["state"]["zones"][2]["cardIds"] = ["borrowed"]
                start["state"]["cards"]["borrowed"] = {
                    "name": "Borrowed Creature", "controllerId": "p1", "damage": 0,
                }
                pilot.choose(start)
                later = copy.deepcopy(start)
                if change == "damage":
                    later["state"]["cards"]["borrowed"]["damage"] = 2
                    later["state"]["gameLog"].append({"type": "damageDealt"})
                else:
                    later["state"]["cards"]["borrowed"]["controllerId"] = "p2"
                self.assertNotIn("delegatedPass", pilot.choose(later).metadata)
                self.assertEqual(strategic.calls, 2)

    def test_pass_limit_expires_lease(self):
        strategic = ScriptedPilot(until="next_own_main")
        pilot = DelegatedAutopassPilot(strategic, max_passes=1)
        same = observation()
        pilot.choose(same)
        self.assertIn("delegatedPass", pilot.choose(copy.deepcopy(same)).metadata)
        pilot.choose(copy.deepcopy(same))
        self.assertEqual(strategic.calls, 2)


if __name__ == "__main__":
    unittest.main()
