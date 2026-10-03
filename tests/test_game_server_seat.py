from __future__ import annotations

import json
import unittest

from commander_gym.game_server_seat import (
    GameServerSeatAdapter,
    GameServerSeatError,
    NativeActionResponse,
    NativeDecisionResponse,
)
from commander_gym.openai_responses_pilot import OpenAIResponsesPilot
from commander_gym.pilot import (
    ArgentumActionChoice,
    ArgentumDecisionChoice,
    PilotContractError,
)


class ScriptedPilot:
    name = "scripted"
    version = "1"

    def __init__(self, *choices):
        self.choices = list(choices)
        self.observations = []

    def choose(self, observation):
        self.observations.append(observation)
        choice = self.choices.pop(0)
        if isinstance(choice, Exception):
            raise choice
        return choice


class GameServerSeatAdapterTests(unittest.TestCase):
    def setUp(self):
        self.state = {"viewingPlayerId": "ai", "hand": [{"name": "Mountain"}]}
        self.actions = [
            {
                "actionType": "PassPriority",
                "description": "Pass",
                "action": {"type": "PassPriority", "playerId": "ai"},
            },
            {
                "actionType": "PlayLand",
                "description": "Play Mountain",
                "action": {"type": "PlayLand", "playerId": "ai", "cardId": "mountain-1"},
            },
        ]

    def test_returns_exact_native_legal_action_and_records_masked_provenance(self):
        records = []
        pilot = ScriptedPilot(ArgentumActionChoice(1, metadata={"model": "test"}))
        adapter = GameServerSeatAdapter(pilot, "ai", provenance_sink=records.append)

        result = adapter.choose_action(self.state, self.actions, None, ["Human played a land"])

        self.assertIsInstance(result, NativeActionResponse)
        self.assertEqual(result.action, self.actions[1]["action"])
        self.assertEqual(result.params, {})
        self.assertEqual(pilot.observations[0]["state"], self.state)
        self.assertNotIn("snapshot", pilot.observations[0])
        self.assertEqual(records[0].callback, "chooseAction")
        self.assertNotIn("snapshot", records[0].observation)

    def test_native_structured_decision_round_trip(self):
        pending = {
            "decisionId": "decision-7",
            "kind": "YesNo",
            "requiresStructuredResponse": True,
            "prompt": "Pay one life?",
        }
        choice = ArgentumDecisionChoice(
            {"type": "YesNoResponse", "decisionId": "decision-7", "yes": True}
        )
        adapter = GameServerSeatAdapter(ScriptedPilot(choice), "ai")

        result = adapter.choose_action(self.state, [], pending)

        self.assertIsInstance(result, NativeDecisionResponse)
        self.assertEqual(result.player_id, "ai")
        self.assertEqual(result.response, choice.response)

    def test_pending_mana_payment_accepts_only_offered_mana_ability_action(self):
        pending = {
            "decisionId": "payment-1", "kind": "SelectManaSourcesDecision",
            "responseSpec": {"responseType": "ManaSourcesSelectedResponse"},
        }
        mana_action = {
            "actionType": "ActivateAbility", "isManaAbility": True,
            "description": "Activate Springleaf Drum for mana",
            "semanticId": "native-drum-mana", "parameterSpec": {"allowedFields": {}},
            "action": {"type": "ActivateAbility", "playerId": "ai", "sourceId": "drum-1"},
        }
        nonmana_action = {
            "actionType": "ActivateAbility", "isManaAbility": False,
            "action": {"type": "ActivateAbility", "playerId": "ai", "sourceId": "other-1"},
        }
        result = GameServerSeatAdapter(
            ScriptedPilot(ArgentumActionChoice(0)), "ai",
        ).choose_action(self.state, [mana_action, nonmana_action], pending)
        self.assertIsInstance(result, NativeActionResponse)
        self.assertEqual(result.action, mana_action["action"])

        with self.assertRaisesRegex(PilotContractError, "offered mana ability"):
            GameServerSeatAdapter(
                ScriptedPilot(ArgentumActionChoice(1)), "ai",
            ).choose_action(self.state, [mana_action, nonmana_action], pending)

        class Responses:
            def create(self, **_request):
                return type("Response", (), {
                    "status": "completed",
                    "output_text": json.dumps({
                        "channel": "action", "semanticId": "native-drum-mana", "params": {},
                    }),
                })()

        provider = OpenAIResponsesPilot(
            client=type("Client", (), {"responses": Responses()})(), model="gpt-test",
        )
        native = GameServerSeatAdapter(provider, "ai").choose_action(
            self.state, [mana_action, nonmana_action], pending,
        )
        self.assertIsInstance(native, NativeActionResponse)
        self.assertEqual(native.action, mana_action["action"])

    def test_mulligan_and_bottom_card_callbacks_use_same_pilot(self):
        pilot = ScriptedPilot(
            ArgentumActionChoice(1),
            ArgentumDecisionChoice(
                {
                    "type": "CardsSelectedResponse",
                    "decisionId": "bottom-1",
                    "selectedCards": ["card-b"],
                }
            ),
        )
        adapter = GameServerSeatAdapter(pilot, "ai")

        self.assertFalse(adapter.decide_mulligan({"hand": ["card-a", "card-b"]}))
        self.assertEqual(
            adapter.choose_bottom_cards(
                {
                    "decisionId": "bottom-1",
                    "hand": ["card-a", "card-b"],
                    "cardsToPutOnBottom": 1,
                }
            ),
            ["card-b"],
        )
        self.assertEqual(len(pilot.observations), 2)

    def test_invalid_stale_and_provider_failures_propagate_without_fallback(self):
        cases = (
            ScriptedPilot(ArgentumActionChoice(99)),
            ScriptedPilot(
                ArgentumDecisionChoice(
                    {"type": "YesNoResponse", "decisionId": "stale", "yes": True}
                )
            ),
            ScriptedPilot(RuntimeError("provider unavailable")),
        )
        pending = {
            "decisionId": "current",
            "kind": "YesNo",
            "requiresStructuredResponse": True,
        }
        for index, pilot in enumerate(cases):
            with self.subTest(index=index):
                adapter = GameServerSeatAdapter(pilot, "ai")
                with self.assertRaises((PilotContractError, RuntimeError)):
                    if index == 0:
                        adapter.choose_action(self.state, self.actions, None)
                    else:
                        adapter.choose_action(self.state, [], pending)

    def test_rejects_wrong_perspective_and_carries_native_action_params(self):
        with self.assertRaisesRegex(GameServerSeatError, "perspective"):
            GameServerSeatAdapter(ScriptedPilot(ArgentumActionChoice(0)), "ai").choose_action(
                {"viewingPlayerId": "human"}, self.actions, None
            )

        result = GameServerSeatAdapter(
            ScriptedPilot(ArgentumActionChoice(0, params={"targets": ["target-1"]})), "ai"
        ).choose_action(self.state, self.actions, None)
        self.assertEqual(result.action, self.actions[0]["action"])
        self.assertEqual(result.params, {"targets": ["target-1"]})

    def test_canonical_known_deck_reaches_masked_observation(self):
        pilot = ScriptedPilot(ArgentumActionChoice(0))
        seat = GameServerSeatAdapter(pilot, "ai")
        seat.set_deck_list({"Mountain": 20, "Goblin": 4})
        seat.choose_action(self.state, [self.actions[0]], None)
        self.assertEqual(pilot.observations[0]["knownDeck"], {
            "cards": {"Mountain": 20, "Goblin": 4},
        })


if __name__ == "__main__":
    unittest.main()
