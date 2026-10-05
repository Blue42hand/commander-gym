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
    def test_declarative_wait_land_cast_uses_fresh_native_offers(self):
        class PlannedPilot:
            name, version = "planned", "1"
            calls = 0

            def choose(self, observation):
                self.calls += 1
                if self.calls == 1:
                    return ArgentumActionChoice(0, metadata={"continuation": {
                        "reason": "Wait through upkeep, then play land and cast",
                        "steps": [
                            {"type": "wait", "until": "phase_end", "maxPasses": 3},
                            {"type": "playLand", "cardId": "h1",
                             "when": {"phase": "PRECOMBAT_MAIN", "stackEmpty": True},
                             "params": {}},
                            {"type": "cast", "cardId": "h2",
                             "when": {"phase": "PRECOMBAT_MAIN", "stackEmpty": True},
                             "params": {}},
                            {"type": "wait", "until": "phase_end", "maxPasses": 2},
                        ],
                    }})
                return ArgentumActionChoice(0)

        passed = {
            "actionId": 0, "kind": "PassPriority", "actionType": "PassPriority",
            "affordable": True, "isAffordable": True,
            "action": {"type": "PassPriority", "playerId": "p1"},
        }
        land = {
            "actionId": 5, "kind": "PlayLand", "actionType": "PlayLand",
            "affordable": True, "isAffordable": True, "isDecisionOption": False,
            "parameterSpec": {"allowedFields": {}},
            "action": {"type": "PlayLand", "playerId": "p1", "cardId": "h1",
                       "asBackFace": False},
        }
        cast = {
            "actionId": 9, "kind": "CastSpell", "actionType": "CastSpell",
            "affordable": True, "isAffordable": True, "isDecisionOption": False,
            "sourceZone": None, "hasXCost": False, "additionalCostInfo": None,
            "requiresTargets": False, "validTargets": None,
            "requiresDamageDistribution": False, "requiresManaColorChoice": False,
            "modalEnumeration": None, "maxAffordableX": None,
            "hasDelve": False, "hasConvoke": False, "hasHarmonize": False,
            "hasTapForGeneric": False,
            "parameterSpec": {"allowedFields": {
                "targets": "ENTITY_ID_ARRAY", "xValue": "INTEGER",
            }},
            "action": {
                "type": "CastSpell", "playerId": "p1", "cardId": "h2",
                "additionalCostPayment": None, "alternativeCostType": None,
                "alternativePayment": None, "casualtyCreature": None,
                "chosenModes": [], "targets": [], "modeTargetsOrdered": [],
                "splicedCardIds": [], "conspiredCreatures": [],
                "damageDistribution": None, "declaredCostSlot": None,
                "faceIndex": None, "giftRecipient": None,
                "graveyardCastRider": None, "graveyardLifeCost": 0,
                "modeDamageDistribution": {}, "wasWaterbendPaid": False,
                "xValue": None, "paymentStrategy": {"type": "AutoPay"},
                "useAlternativeCost": False, "useWithoutPayingManaCost": False,
                "castFaceDown": False, "castPrototyped": False,
                "additionalCostChoices": {}, "additionalManaForCounters": 0,
                "declaredCostIndices": [], "declaredCostTimes": 1,
            },
        }
        start = observation(step="UPKEEP", legal=[passed])
        start["state"]["currentPhase"] = "BEGINNING"
        start["state"]["zones"][0]["cardIds"] = ["h1", "h2"]
        main = copy.deepcopy(start)
        main["state"]["currentPhase"] = "PRECOMBAT_MAIN"
        main["state"]["currentStep"] = "PRECOMBAT_MAIN"
        main["legalActions"] = [land]
        after_land = copy.deepcopy(main)
        after_land["state"]["zones"][0]["cardIds"] = ["h2"]
        after_land["state"]["zones"][1]["cardIds"] = ["b1", "h1"]
        after_land["state"]["cards"]["h1"] = {"name": "Played Land", "tapped": False}
        after_land["state"]["gameLog"] = [{"type": "permanentEntered"}]
        after_land["legalActions"] = [cast]

        strategic = PlannedPilot()
        pilot = DelegatedAutopassPilot(
            strategic, allow_named_deferrals=True,
            allow_declarative_continuation=True,
        )
        self.assertEqual(pilot.choose(start).action_id, 0)
        self.assertEqual(pilot.choose(copy.deepcopy(start)).action_id, 0)
        self.assertEqual(pilot.choose(main).action_id, 5)
        chosen = pilot.choose(after_land)
        self.assertEqual(chosen.action_id, 9)
        self.assertEqual(chosen.metadata["declarativeContinuation"]["type"], "cast")
        self.assertEqual(strategic.calls, 1)
        after_cast = copy.deepcopy(after_land)
        after_cast["state"]["zones"][0]["cardIds"] = []
        after_cast["state"]["zones"][-1]["cardIds"] = ["h2"]
        after_cast["state"]["gameLog"].append({"type": "spellCast"})
        after_cast["legalActions"] = [passed]
        self.assertEqual(pilot.choose(after_cast).action_id, 0)
        self.assertEqual(pilot.choose(after_cast).action_id, 0)
        finished = copy.deepcopy(after_cast)
        finished["state"]["currentPhase"] = "POSTCOMBAT_MAIN"
        finished["state"]["currentStep"] = "POSTCOMBAT_MAIN"
        self.assertEqual(pilot.choose(finished).metadata["continuationWake"]["reason"],
                         "boundary-reached")
        self.assertEqual(strategic.calls, 2)

        class MaxOnePilot(PlannedPilot):
            def choose(self, observation):
                choice = super().choose(observation)
                if self.calls == 1:
                    choice.metadata["continuation"]["steps"][-1]["maxPasses"] = 1
                return choice

        strategic = MaxOnePilot()
        pilot = DelegatedAutopassPilot(
            strategic, allow_named_deferrals=True,
            allow_declarative_continuation=True,
        )
        pilot.choose(start)
        pilot.choose(main)
        pilot.choose(after_land)
        first_pass = pilot.choose(after_cast)
        self.assertEqual(first_pass.metadata["declarativeContinuation"]["ordinal"], 1)
        self.assertEqual(pilot.choose(after_cast).metadata["continuationWake"]["reason"],
                         "wait-state-or-menu-changed")
        self.assertEqual(strategic.calls, 2)

        for changed in ("resolved", "opponent_spell", "tapped_mana", "decision",
                        "nonmana_option", "unknown_event"):
            with self.subTest(after_cast=changed):
                strategic = PlannedPilot()
                pilot = DelegatedAutopassPilot(
                    strategic, allow_named_deferrals=True,
                    allow_declarative_continuation=True,
                )
                pilot.choose(start)
                pilot.choose(main)
                pilot.choose(after_land)
                interrupted = copy.deepcopy(after_cast)
                if changed == "resolved":
                    interrupted["state"]["zones"][-1]["cardIds"] = []
                    interrupted["state"]["gameLog"].append({"type": "spellResolved"})
                elif changed == "opponent_spell":
                    interrupted["state"]["zones"][-1]["cardIds"].append("opponent-spell")
                elif changed == "tapped_mana":
                    interrupted["state"]["cards"]["b1"]["tapped"] = True
                elif changed == "decision":
                    interrupted["pendingDecision"] = {"decisionId": "d1"}
                elif changed == "nonmana_option":
                    interrupted["legalActions"].append({
                        "actionId": 3, "kind": "CastSpell", "affordable": True,
                        "action": {"cardId": "h3"},
                    })
                else:
                    interrupted["state"]["gameLog"][-1] = {"type": "unknownEvent"}
                choice = pilot.choose(interrupted)
                self.assertEqual(strategic.calls, 2)
                self.assertEqual(choice.metadata["continuationWake"]["reason"],
                                 ("decision-or-invalid-view" if changed == "decision"
                                  else "cast-transition-or-pass-menu-changed"))

        for changed in ("unknown_event", "stack", "decision", "mana", "ambiguous_land",
                        "stale_land", "cast_target", "duplicate_cast"):
            with self.subTest(changed=changed):
                strategic = PlannedPilot()
                pilot = DelegatedAutopassPilot(
                    strategic, allow_named_deferrals=True,
                    allow_declarative_continuation=True,
                )
                pilot.choose(start)
                interrupted = copy.deepcopy(main)
                if changed == "unknown_event":
                    interrupted["state"]["gameLog"] = [{"type": "unknownEvent"}]
                elif changed == "stack":
                    interrupted["state"]["zones"][-1]["cardIds"] = ["opponent-spell"]
                elif changed == "decision":
                    interrupted["pendingDecision"] = {"decisionId": "d1"}
                elif changed == "mana":
                    interrupted["state"]["players"][0]["manaPool"]["red"] = 1
                elif changed == "ambiguous_land":
                    interrupted["legalActions"].append({**land, "actionId": 6})
                elif changed == "stale_land":
                    interrupted["state"]["zones"][0]["cardIds"] = ["h2"]
                else:
                    interrupted = copy.deepcopy(after_land)
                    if changed == "cast_target":
                        interrupted["legalActions"][0]["requiresTargets"] = True
                    else:
                        interrupted["legalActions"].append({**cast, "actionId": 10})
                    pilot.choose(main)
                chosen = pilot.choose(interrupted)
                self.assertEqual(strategic.calls, 2)
                self.assertIn("continuationWake", chosen.metadata)
                self.assertIn("maskedState", chosen.metadata["continuationWake"])
                self.assertEqual(len(chosen.metadata["continuationWake"]["intent"]["steps"]), 4)
                if changed == "unknown_event":
                    self.assertEqual(chosen.metadata["continuationWake"]["maskedEvents"],
                                     [{"type": "unknownEvent"}])
                    self.assertIn("log", chosen.metadata["continuationWake"]["changedVisibleFields"])

    def test_declarative_wait_expires_on_repeated_callback(self):
        class PlannedPilot:
            name, version = "planned", "1"
            calls = 0

            def choose(self, observation):
                self.calls += 1
                return ArgentumActionChoice(0, metadata={"continuation": {
                    "reason": "Short wait", "steps": [
                        {"type": "wait", "until": "phase_end", "maxPasses": 1},
                    ],
                }}) if self.calls == 1 else ArgentumActionChoice(0)

        start = observation(legal=[{
            "actionId": 0, "kind": "PassPriority", "actionType": "PassPriority",
            "affordable": True, "isAffordable": True,
            "action": {"type": "PassPriority", "playerId": "p1"},
        }])
        strategic = PlannedPilot()
        pilot = DelegatedAutopassPilot(strategic, allow_declarative_continuation=True)
        pilot.choose(start)
        self.assertEqual(pilot.choose(start).metadata["declarativeContinuation"]["ordinal"], 1)
        self.assertEqual(pilot.choose(start).metadata["continuationWake"]["reason"],
                         "wait-state-or-menu-changed")
        self.assertEqual(strategic.calls, 2)

    def test_declarative_malformed_or_unapproved_plan_does_not_arm(self):
        class InvalidPilot:
            name, version = "invalid", "1"
            calls = 0

            def __init__(self, directive):
                self.directive = directive

            def choose(self, observation):
                self.calls += 1
                return ArgentumActionChoice(0, metadata={"continuation": self.directive}) \
                    if self.calls == 1 else ArgentumActionChoice(0)

        start = observation(legal=[{
            "actionId": 0, "kind": "PassPriority", "actionType": "PassPriority",
            "affordable": True, "isAffordable": True,
            "action": {"type": "PassPriority", "playerId": "p1"},
        }])
        valid = {"reason": "Yield to main", "steps": [
            {"type": "wait", "until": "phase_end", "maxPasses": 2},
        ]}
        for directive in (
            {**valid, "steps": [{"type": "wait", "until": "phase_end",
                                  "maxPasses": True}]},
            {**valid, "steps": [{"type": "wait", "until": "phase_end",
                                  "maxPasses": 2, "code": "pass"}]},
            {**valid, "steps": [{"type": []}]},
            {**valid, "steps": [{"type": "wait", "until": {}, "maxPasses": 1}]},
            {**valid, "steps": [{"type": "cast", "cardId": "h1",
                                  "when": {"phase": "PRECOMBAT_MAIN"}, "params": {}}]},
            {**valid, "steps": valid["steps"] * 5},
            {**valid, "reason": " "},
        ):
            with self.subTest(directive=directive):
                strategic = InvalidPilot(directive)
                pilot = DelegatedAutopassPilot(
                    strategic, allow_declarative_continuation=True,
                )
                first = pilot.choose(start)
                self.assertIn("continuationRejected", first.metadata)
                self.assertEqual(pilot.choose(start).action_id, 0)
                self.assertEqual(strategic.calls, 2)
        strategic = InvalidPilot(valid)
        legacy = DelegatedAutopassPilot(strategic)
        self.assertEqual(legacy.choose(start).metadata["continuationRejected"],
                         "component-disabled")
        unsafe = copy.deepcopy(start)
        unsafe["legalActions"].append({
            "actionId": 1, "kind": "CastSpell", "affordable": True,
            "action": {"playerId": "p1", "cardId": "h1"},
        })
        strategic = InvalidPilot(valid)
        pilot = DelegatedAutopassPilot(strategic, allow_declarative_continuation=True)
        self.assertEqual(pilot.choose(unsafe).metadata["continuationRejected"],
                         "unsafe-priority-start")
        malformed = copy.deepcopy(start)
        malformed["state"]["zones"].append({
            "zoneId": {"ownerId": "p1", "zoneType": []}, "cardIds": [],
        })
        strategic = InvalidPilot(valid)
        pilot = DelegatedAutopassPilot(strategic, allow_declarative_continuation=True)
        self.assertEqual(pilot.choose(malformed).metadata["continuationRejected"],
                         "invalid-current-choice-or-view")

    def test_guarded_then_cast_optional_native_template_and_abort_conditions(self):
        class LandPilot:
            name, version = "planned-land", "1"

            def __init__(self, card_id="h2"):
                self.calls = 0
                self.card_id = card_id

            def choose(self, observation):
                self.calls += 1
                return ArgentumActionChoice(0, metadata={"thenCast": {
                    "cardId": self.card_id, "reason": "Cast the selected spell after land",
                }}) if self.calls == 1 else ArgentumActionChoice(0)

        start = observation(legal=[
            {"actionId": 0, "kind": "PlayLand", "action": {"cardId": "h1"}},
        ])
        start["state"]["zones"][0]["cardIds"] = ["h1", "h2"]
        later = copy.deepcopy(start)
        later["state"]["zones"][0]["cardIds"] = ["h2"]
        later["state"]["zones"][1]["cardIds"] = ["b1", "h1"]
        later["state"]["cards"]["h1"] = {"name": "Played Land", "tapped": False}
        later["state"]["gameLog"] = [{"type": "permanentEntered"}]
        cast = {
            "actionId": 7, "kind": "CastSpell", "sourceZone": None,
            "affordable": True, "isAffordable": True,
            "hasXCost": False, "additionalCostInfo": None,
            "requiresTargets": False, "requiresDamageDistribution": False,
            "requiresManaColorChoice": False, "hasDelve": False,
            "hasConvoke": False, "hasHarmonize": False, "hasTapForGeneric": False,
            "parameterSpec": {"allowedFields": {
                "targets": "ENTITY_ID_ARRAY", "xValue": "INTEGER",
            }},
            "action": {
                "type": "CastSpell", "playerId": "p1", "cardId": "h2",
                "additionalCostPayment": None, "alternativeCostType": None,
                "alternativePayment": None, "casualtyCreature": None,
                "chosenModes": [], "targets": [], "modeTargetsOrdered": [],
                "splicedCardIds": [], "conspiredCreatures": [],
                "damageDistribution": None, "declaredCostSlot": None,
                "faceIndex": None, "giftRecipient": None,
                "graveyardCastRider": None, "graveyardLifeCost": 0,
                "modeDamageDistribution": {}, "wasWaterbendPaid": False,
                "xValue": None, "paymentStrategy": {"type": "AutoPay"},
                "useAlternativeCost": False, "useWithoutPayingManaCost": False,
                "castFaceDown": False, "castPrototyped": False,
                "additionalCostChoices": {}, "additionalManaForCounters": 0,
                "declaredCostIndices": [], "declaredCostTimes": 1,
            },
        }
        later["legalActions"] = [cast]

        def run(first, second, *, enabled=True, card_id="h2"):
            strategic = LandPilot(card_id)
            pilot = DelegatedAutopassPilot(
                strategic, allow_named_deferrals=True,
                guarded_then_cast_templates=enabled,
            )
            pilot.choose(first)
            choice = pilot.choose(second)
            return choice, strategic.calls

        result, calls = run(start, later)
        self.assertEqual((result.action_id, result.params, calls), (7, {}, 1))
        self.assertEqual(run(start, later, enabled=False)[1], 2)

        # The eight saved game intents cover seven isolated land transitions
        # (including one command-zone cast) and one visible effect change.
        # Card IDs are local to that trace; the fixture contains no deck data.
        for row, card_id, command, changed_effect in (
            (4, "e30", False, False), (15, "e82", False, False),
            (23, "e144", False, False), (36, "e4", False, False),
            (50, "e100", True, False), (59, "e10", False, False),
            (102, "e157", False, False), (129, "e10", False, True),
        ):
            with self.subTest(trace_row=row):
                first, second = copy.deepcopy(start), copy.deepcopy(later)
                second["legalActions"][0]["action"]["cardId"] = card_id
                if command:
                    first["state"]["zones"][0]["cardIds"] = ["h1"]
                    second["state"]["zones"][0]["cardIds"] = []
                    for item in (first, second):
                        item["state"]["zones"].insert(1, {
                            "zoneId": {"ownerId": "p1", "zoneType": "Command"},
                            "cardIds": [card_id],
                        })
                    second["legalActions"][0]["sourceZone"] = "COMMAND"
                else:
                    first["state"]["zones"][0]["cardIds"] = ["h1", card_id]
                    second["state"]["zones"][0]["cardIds"] = [card_id]
                if changed_effect:
                    second["state"]["cards"]["b1"]["activeEffects"] = [
                        {"description": "artifact count changed"},
                    ]
                _, calls = run(first, second, card_id=card_id)
                self.assertEqual(calls, 2 if changed_effect else 1)

        # A missing empty Battlefield zone and reordered existing permanents
        # are representation differences, not new game decisions.
        empty_before, empty_after = copy.deepcopy(start), copy.deepcopy(later)
        empty_before["state"]["zones"].pop(1)
        empty_after["state"]["zones"][1]["cardIds"] = ["h1"]
        empty_before["state"]["cards"].pop("b1")
        empty_after["state"]["cards"].pop("b1")
        self.assertEqual(run(empty_before, empty_after)[1], 1)

        commander_before, commander_after = copy.deepcopy(start), copy.deepcopy(later)
        commander_before["state"]["zones"][0]["cardIds"] = ["h1"]
        commander_after["state"]["zones"][0]["cardIds"] = []
        for item in (commander_before, commander_after):
            item["state"]["zones"].insert(1, {
                "zoneId": {"ownerId": "p1", "zoneType": "Command"}, "cardIds": ["h2"],
            })
        commander_after["legalActions"][0]["sourceZone"] = "COMMAND"
        self.assertEqual(run(commander_before, commander_after)[1], 1)

        for field, value in (
            ("additionalCostChoices", {"cost": "selected"}),
            ("additionalManaForCounters", 1),
            ("additionalManaForCounters", False),
            ("castPrototyped", True),
            ("declaredCostIndices", [0]), ("declaredCostTimes", 2),
            ("declaredCostTimes", True),
            ("unexpectedNativeField", None),
        ):
            with self.subTest(nondefault=field):
                interrupted = copy.deepcopy(later)
                interrupted["legalActions"][0]["action"][field] = value
                self.assertEqual(run(start, interrupted)[1], 2)
        missing_default = copy.deepcopy(later)
        missing_default["legalActions"][0]["action"].pop("declaredCostTimes")
        self.assertEqual(run(start, missing_default)[1], 2)

        for change in ("targets", "x", "mode", "delve", "extra_cost", "other_variant",
                       "second_cast",
                       "opponent", "stack", "hand", "life", "mana", "decision", "log"):
            with self.subTest(change=change):
                interrupted = copy.deepcopy(later)
                offer = interrupted["legalActions"][0]
                if change == "targets":
                    offer["requiresTargets"] = True
                elif change == "x":
                    offer["hasXCost"] = True
                elif change == "mode":
                    offer["action"]["chosenModes"] = ["first"]
                elif change == "delve":
                    offer["hasDelve"] = True
                elif change == "extra_cost":
                    offer["additionalCostInfo"] = {"kind": "discard"}
                elif change == "other_variant":
                    interrupted["legalActions"].append({
                        "actionId": 8, "kind": "CastAlternative", "action": {"cardId": "h2"},
                    })
                elif change == "second_cast":
                    interrupted["legalActions"].append({
                        **copy.deepcopy(offer), "actionId": 8, "affordable": False,
                    })
                elif change == "opponent":
                    interrupted["state"]["zones"][2]["cardIds"] = ["new"]
                elif change == "stack":
                    interrupted["state"]["zones"][-1]["cardIds"] = ["spell"]
                elif change == "hand":
                    interrupted["state"]["zones"][0]["cardIds"].append("new")
                elif change == "life":
                    interrupted["state"]["players"][0]["life"] = 39
                elif change == "mana":
                    interrupted["state"]["players"][0]["manaPool"]["red"] = 1
                elif change == "decision":
                    interrupted["pendingDecision"] = {"decisionId": "d1"}
                else:
                    interrupted["state"]["gameLog"].append({"type": "other"})
                self.assertEqual(run(start, interrupted)[1], 2)

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
                                             "castFaceDown": False,
                                             "castPrototyped": False,
                                             "additionalCostChoices": {},
                                             "additionalManaForCounters": 0,
                                             "declaredCostIndices": [],
                                             "declaredCostTimes": 1}}]
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
