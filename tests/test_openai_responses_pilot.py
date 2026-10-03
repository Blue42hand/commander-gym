import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from commander_gym.openai_responses_pilot import (
    MODEL_IO_SCHEMA_VERSION,
    OpenAIResponsesPilot,
    OpenAIResponsesPilotError,
)
from commander_gym.pilot import (
    ArgentumActionChoice,
    ArgentumDecisionChoice,
    choose_for_observation,
)
from commander_gym.openai_run_budget import OpenAIRunBudget


class FakeResponse:
    def __init__(
        self,
        output,
        *,
        response_id="resp-1",
        status="completed",
        error=None,
        incomplete_details=None,
    ):
        self.id = response_id
        self.model = "provider-model-snapshot"
        self.status = status
        self.error = error
        self.incomplete_details = incomplete_details
        self.output_text = output
        self.usage = {"input_tokens": 100, "output_tokens": 20}


class FakeResponses:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        if isinstance(self.response, list):
            return self.response.pop(0)
        return self.response


class FakeClient:
    def __init__(self, response=None, error=None):
        self.responses = FakeResponses(response=response, error=error)


def action_observation():
    return {
        "type": "Game",
        "schemaHash": "argentum-gym-contract@v1.7-semantic-state-provenance",
        "stateDigest": "state-a",
        "perspectivePlayerId": "player-1",
        "agentToAct": "player-1",
        "pendingDecision": None,
        "legalActions": [
            {
                "actionId": 2,
                "semanticId": "argentum-action-v1:pass",
                "kind": "PassPriority",
                "description": "Pass priority",
                "affordable": True,
            },
            {
                "actionId": 7,
                "semanticId": "argentum-action-v1:attack",
                "kind": "DeclareAttackers",
                "description": "Declare attackers",
                "affordable": True,
                "validAttackers": ["creature-1"],
                "validAttackTargets": ["player-2"],
            },
        ],
        "terminated": False,
    }


def structured_observation():
    return {
        "type": "Game",
        "schemaHash": "argentum-gym-contract@v1.7-semantic-state-provenance",
        "stateDigest": "state-structured",
        "perspectivePlayerId": "player-1",
        "agentToAct": "player-1",
        "pendingDecision": {
            "decisionId": "routing-live-9",
            "semanticId": "argentum-decision-v1:choose-targets",
            "kind": "CHOOSE_TARGETS",
            "playerId": "player-1",
            "prompt": "Choose one target",
            "requiresStructuredResponse": True,
            "responseSpec": {
                "responseType": "TargetsResponse",
                "requiredFields": {"selectedTargets": "MAP"},
            },
            "legalTargets": {"0": ["target-1"]},
        },
        "legalActions": [],
        "terminated": False,
    }


class OpenAIResponsesPilotTests(unittest.TestCase):
    def test_priority_delegation_requires_opt_in_and_exact_pass(self):
        directive = {"until": "phase_end", "reason": "Reviewed this main phase"}
        output = json.dumps({
            "channel": "action", "semanticId": "argentum-action-v1:pass",
            "params": {}, "priorityDelegation": directive,
        })
        client = FakeClient(FakeResponse(output))
        pilot = OpenAIResponsesPilot(
            client=client, model="gpt-test", allow_priority_delegation=True,
        )
        choice = pilot.choose(action_observation())
        self.assertEqual(choice.metadata["priorityDelegation"], directive)
        self.assertIn("priorityDelegation", client.responses.calls[0]["instructions"])

        rejected = OpenAIResponsesPilot(
            client=FakeClient(FakeResponse(output)), model="gpt-test", max_attempts=1,
        )
        with self.assertRaisesRegex(OpenAIResponsesPilotError, "not enabled"):
            rejected.choose(action_observation())

        wrong_action = json.dumps({
            "channel": "action", "semanticId": "argentum-action-v1:attack",
            "params": {"attackers": {}}, "priorityDelegation": directive,
        })
        rejected = OpenAIResponsesPilot(
            client=FakeClient(FakeResponse(wrong_action)), model="gpt-test",
            max_attempts=1, allow_priority_delegation=True,
        )
        with self.assertRaisesRegex(OpenAIResponsesPilotError, "requires an exact current PassPriority"):
            rejected.choose(action_observation())

    def test_native_action_schema_exposes_delegation_only_for_opt_in(self):
        obs = action_observation()
        for action in obs["legalActions"]:
            action["parameterSpec"] = {"allowedFields": {}}
        output = json.dumps({
            "channel": "action", "choice": {
                "semanticId": "argentum-action-v1:pass", "params": {},
            },
        })
        for enabled in (False, True):
            client = FakeClient(FakeResponse(output))
            OpenAIResponsesPilot(
                client=client, model="gpt-test", allow_priority_delegation=enabled,
            ).choose(obs)
            properties = client.responses.calls[0]["text"]["format"]["schema"]["properties"]
            self.assertEqual("priorityDelegation" in properties, enabled)

    def test_bounded_provider_request_is_recorded_in_exact_model_io(self):
        with TemporaryDirectory() as temporary:
            budget = OpenAIRunBudget(Path(temporary) / "budget.json", 5)
            client = FakeClient(FakeResponse(json.dumps({
                "channel": "action", "semanticId": "argentum-action-v1:pass", "params": {},
            })))
            choice = OpenAIResponsesPilot(
                client=client, model="gpt-6-luna", budget=budget,
            ).choose(action_observation())

            self.assertEqual(client.responses.calls[0]["max_output_tokens"], 2048)
            self.assertEqual(
                choice.metadata["modelIo"]["attempts"][0]["request"],
                client.responses.calls[0],
            )
            self.assertEqual(budget.snapshot()["requests"], 1)
            self.assertEqual(budget.snapshot()["inputTokens"], 100)
            self.assertEqual(budget.snapshot()["outputTokens"], 20)

    def test_selects_by_semantic_id_and_hides_live_action_ids_from_model(self):
        raw_output = json.dumps(
            {
                "channel": "action",
                "semanticId": "argentum-action-v1:attack",
                "params": {"attackers": {"creature-1": "player-2"}},
            }
        )
        client = FakeClient(FakeResponse(raw_output))
        pilot = OpenAIResponsesPilot(client=client, model="gpt-test")

        choice = choose_for_observation(pilot, action_observation())

        self.assertIsInstance(choice, ArgentumActionChoice)
        self.assertEqual(choice.action_id, 7)
        self.assertEqual(
            choice.params,
            {"attackers": {"creature-1": "player-2"}},
        )
        self.assertEqual(choice.metadata["provider"], "openai")
        self.assertEqual(choice.metadata["model"], "gpt-test")
        self.assertEqual(choice.metadata["responseId"], "resp-1")
        self.assertEqual(choice.metadata["usage"]["input_tokens"], 100)

        self.assertEqual(len(client.responses.calls), 1)
        request = client.responses.calls[0]
        self.assertTrue(request["input"].startswith("Return one JSON object"))
        model_input = json.loads(request["input"].split("\n", 1)[1])
        self.assertNotIn("actionId", model_input["legalActions"][0])
        self.assertNotIn("actionId", model_input["legalActions"][1])
        self.assertEqual(
            model_input["legalActions"][1]["semanticId"],
            "argentum-action-v1:attack",
        )
        self.assertEqual(request["text"], {"format": {"type": "json_object"}})
        self.assertFalse(request["store"])
        self.assertEqual(choice.metadata["retryCount"], 0)

        model_io = choice.metadata["modelIo"]
        self.assertEqual(model_io["schemaVersion"], MODEL_IO_SCHEMA_VERSION)
        self.assertEqual(model_io["provider"], "openai")
        self.assertEqual(model_io["selectedAttempt"], 0)
        self.assertEqual(model_io["attempts"][0]["request"], request)
        self.assertEqual(model_io["attempts"][0]["response"]["outputText"], raw_output)
        self.assertEqual(
            model_io["attempts"][0]["response"]["usage"],
            {"input_tokens": 100, "output_tokens": 20},
        )

    def test_retries_one_invalid_unsubmitted_choice_and_records_retry(self):
        invalid_output = json.dumps(
            {
                "channel": "action",
                "semanticId": "invented-action",
                "params": {},
            }
        )
        valid_output = json.dumps(
            {
                "channel": "action",
                "semanticId": "argentum-action-v1:pass",
                "params": {},
            }
        )
        client = FakeClient(
            [
                FakeResponse(invalid_output, response_id="resp-invalid"),
                FakeResponse(valid_output, response_id="resp-valid"),
            ]
        )
        pilot = OpenAIResponsesPilot(client=client, model="gpt-test")

        choice = choose_for_observation(pilot, action_observation())

        self.assertEqual(choice.action_id, 2)
        self.assertEqual(choice.metadata["retryCount"], 1)
        self.assertEqual(len(client.responses.calls), 2)
        self.assertIn("previous response was invalid", client.responses.calls[1]["input"])

        model_io = choice.metadata["modelIo"]
        self.assertEqual(model_io["selectedAttempt"], 1)
        self.assertEqual(len(model_io["attempts"]), 2)
        self.assertEqual(model_io["attempts"][0]["request"], client.responses.calls[0])
        self.assertEqual(model_io["attempts"][1]["request"], client.responses.calls[1])
        self.assertEqual(
            model_io["attempts"][0]["response"]["outputText"], invalid_output
        )
        self.assertIn("validationError", model_io["attempts"][0]["response"])
        self.assertEqual(
            model_io["attempts"][1]["response"]["outputText"], valid_output
        )
        self.assertNotIn("validationError", model_io["attempts"][1]["response"])

    def test_engine_action_specs_constrain_each_request_local_choice(self):
        observation = action_observation()
        observation["type"] = "GameServerSeat"
        observation["legalActions"][0]["parameterSpec"] = {"allowedFields": {}}
        observation["legalActions"][1]["parameterSpec"] = {
            "allowedFields": {"attackers": "ENTITY_ID_MAP"}
        }
        client = FakeClient(FakeResponse(json.dumps({
            "channel": "action",
            "choice": {
                "semanticId": "argentum-action-v1:attack",
                "params": {"attackers": {"creature-1": "player-2"}},
            },
        })))

        choice = OpenAIResponsesPilot(client=client, model="gpt-test").choose(observation)

        self.assertEqual(choice.action_id, 7)
        self.assertEqual(choice.params, {"attackers": {"creature-1": "player-2"}})
        schema = client.responses.calls[0]["text"]["format"]["schema"]
        self.assertEqual(schema["type"], "object")
        variants = schema["properties"]["choice"]["anyOf"]
        self.assertEqual(variants[0]["properties"]["params"]["properties"], {})
        self.assertEqual(
            set(variants[1]["properties"]["params"]["properties"]), {"attackers"}
        )
        self.assertFalse(variants[1]["properties"]["params"]["additionalProperties"])

    def test_engine_spec_rejects_stale_kind_based_params_before_submission(self):
        observation = action_observation()
        observation["legalActions"][1]["parameterSpec"] = {"allowedFields": {}}
        client = FakeClient(FakeResponse(json.dumps({
            "channel": "action",
            "semanticId": "argentum-action-v1:attack",
            "params": {"attackers": {"creature-1": "player-2"}},
        })))

        with self.assertRaisesRegex(OpenAIResponsesPilotError, "not allowed by native"):
            OpenAIResponsesPilot(client=client, model="gpt-test", max_attempts=1).choose(
                observation
            )
        self.assertEqual(len(client.responses.calls), 1)

    def test_engine_spec_accepts_declared_field_independent_of_action_kind(self):
        observation = action_observation()
        observation["legalActions"][0]["parameterSpec"] = {
            "allowedFields": {"xValue": "INTEGER"}
        }
        client = FakeClient(FakeResponse(json.dumps({
            "channel": "action",
            "semanticId": "argentum-action-v1:pass",
            "params": {"xValue": 2},
        })))

        choice = OpenAIResponsesPilot(client=client, model="gpt-test").choose(observation)

        self.assertEqual(choice.params, {"xValue": 2})

    def test_engine_spec_rejects_wrong_action_param_wire_type(self):
        observation = action_observation()
        observation["legalActions"][1]["parameterSpec"] = {
            "allowedFields": {"attackers": "ENTITY_ID_MAP"}
        }
        client = FakeClient(FakeResponse(json.dumps({
            "channel": "action",
            "semanticId": "argentum-action-v1:attack",
            "params": {"attackers": {"creature-1": ["player-2"]}},
        })))

        with self.assertRaisesRegex(OpenAIResponsesPilotError, "requires ENTITY_ID_MAP"):
            OpenAIResponsesPilot(client=client, model="gpt-test", max_attempts=1).choose(
                observation
            )

    def test_unknown_native_action_param_kind_fails_before_provider_call(self):
        observation = action_observation()
        observation["legalActions"][0]["parameterSpec"] = {
            "allowedFields": {"futureParam": "FUTURE_KIND"}
        }
        client = FakeClient(FakeResponse("{}"))

        with self.assertRaisesRegex(OpenAIResponsesPilotError, "unsupported field"):
            OpenAIResponsesPilot(client=client, model="gpt-test").choose(observation)
        self.assertEqual(client.responses.calls, [])

    def test_legacy_game_server_without_parameter_spec_keeps_json_object_contract(self):
        observation = action_observation()
        observation["type"] = "GameServerSeat"
        client = FakeClient(FakeResponse(json.dumps({
            "channel": "action",
            "semanticId": "argentum-action-v1:pass",
            "params": {},
        })))

        OpenAIResponsesPilot(client=client, model="gpt-test").choose(observation)
        self.assertEqual(
            client.responses.calls[0]["text"]["format"], {"type": "json_object"}
        )

    def test_structured_decision_injects_live_routing_id_locally(self):
        client = FakeClient(
            FakeResponse(
                json.dumps(
                    {
                        "channel": "decision",
                        "response": {
                            "type": "TargetsResponse",
                            "selectedTargets": {"0": ["target-1"]},
                        },
                    }
                )
            )
        )
        pilot = OpenAIResponsesPilot(client=client, model="gpt-test")

        choice = choose_for_observation(pilot, structured_observation())

        self.assertIsInstance(choice, ArgentumDecisionChoice)
        self.assertEqual(
            choice.response,
            {
                "type": "TargetsResponse",
                "selectedTargets": {"0": ["target-1"]},
                "decisionId": "routing-live-9",
            },
        )
        model_input = json.loads(client.responses.calls[0]["input"].split("\n", 1)[1])
        self.assertNotIn("decisionId", model_input["pendingDecision"])
        self.assertEqual(
            model_input["pendingDecision"]["semanticId"],
            "argentum-decision-v1:choose-targets",
        )
        self.assertEqual(choice.metadata["modelIo"]["selectedAttempt"], 0)
        selected_schema = client.responses.calls[0]["text"]["format"]["schema"]
        self.assertEqual(
            selected_schema["properties"]["response"]["properties"]
            ["selectedTargets"]["properties"]["0"]["items"]["enum"],
            ["target-1"],
        )

    def test_retries_target_map_scalar_from_native_snapcaster_decision(self):
        observation = structured_observation()
        client = FakeClient([
            FakeResponse(json.dumps({
                "channel": "decision", "response": {
                    "type": "TargetsResponse", "selectedTargets": {"0": "target-1"},
                },
            })),
            FakeResponse(json.dumps({
                "channel": "decision", "response": {
                    "type": "TargetsResponse", "selectedTargets": {"0": ["target-1"]},
                },
            })),
        ])
        choice = OpenAIResponsesPilot(client=client, model="gpt-test").choose(observation)
        self.assertEqual(choice.response["selectedTargets"], {"0": ["target-1"]})
        self.assertEqual(choice.metadata["modelIo"]["selectedAttempt"], 1)
        self.assertIn("arrays of offered target IDs", client.responses.calls[1]["input"])

    def test_rejects_unoffered_target_or_requirement_index(self):
        for selected in ({"0": ["invented"]}, {"1": ["target-1"]}):
            with self.subTest(selected=selected):
                client = FakeClient(FakeResponse(json.dumps({
                    "channel": "decision", "response": {
                        "type": "TargetsResponse", "selectedTargets": selected,
                    },
                })))
                with self.assertRaisesRegex(OpenAIResponsesPilotError, "offered target IDs"):
                    OpenAIResponsesPilot(
                        client=client, model="gpt-test", max_attempts=1,
                    ).choose(structured_observation())

    def test_retries_response_that_violates_native_decision_spec(self):
        observation = structured_observation()
        observation["pendingDecision"]["kind"] = "SELECT_CARDS"
        observation["pendingDecision"]["responseSpec"] = {
            "responseType": "CardsSelectedResponse",
            "requiredFields": {"selectedCards": "ENTITY_ID_ARRAY"},
        }
        client = FakeClient([
            FakeResponse(json.dumps({
                "channel": "decision",
                "response": {"type": "SELECT_CARDS", "cardIds": ["card-1"]},
            })),
            FakeResponse(json.dumps({
                "channel": "decision",
                "response": {"type": "CardsSelectedResponse", "selectedCards": ["card-1"]},
            })),
        ])
        choice = OpenAIResponsesPilot(client=client, model="gpt-test").choose(observation)
        self.assertEqual(choice.response, {
            "type": "CardsSelectedResponse",
            "selectedCards": ["card-1"],
            "decisionId": "routing-live-9",
        })
        self.assertEqual(choice.metadata["modelIo"]["selectedAttempt"], 1)
        self.assertIn("CardsSelectedResponse", client.responses.calls[1]["input"])

    def test_rejects_extra_native_decision_fields_before_submission(self):
        client = FakeClient(FakeResponse(json.dumps({
            "channel": "decision",
            "response": {
                "type": "TargetsResponse",
                "selectedTargets": {"0": ["target-1"]},
                "selectedCards": ["card-1"],
            },
        })))
        pilot = OpenAIResponsesPilot(client=client, model="gpt-test", max_attempts=1)

        with self.assertRaisesRegex(OpenAIResponsesPilotError, "unsupported fields"):
            pilot.choose(structured_observation())
        self.assertEqual(len(client.responses.calls), 1)

    def test_retries_mana_source_not_offered_by_current_decision(self):
        observation = structured_observation()
        observation["pendingDecision"].update({
            "kind": "SelectManaSourcesDecision",
            "availableSources": [{"entityId": "e170", "name": "Mountain"}],
            "responseSpec": {
                "responseType": "ManaSourcesSelectedResponse",
                "requiredFields": {
                    "autoPay": "BOOLEAN", "declined": "BOOLEAN",
                    "selectedSources": "ENTITY_ID_ARRAY",
                    "waterbendPermanents": "ENTITY_ID_ARRAY",
                },
            },
        })
        def answer(sources):
            return FakeResponse(json.dumps({
                "channel": "decision", "response": {
                    "type": "ManaSourcesSelectedResponse", "autoPay": True,
                    "declined": False, "selectedSources": sources,
                    "waterbendPermanents": [],
                },
            }))
        client = FakeClient([answer(["e170", "e146"]), answer(["e170"])])
        choice = OpenAIResponsesPilot(client=client, model="gpt-test").choose(observation)
        self.assertEqual(choice.response["selectedSources"], ["e170"])
        self.assertEqual(choice.metadata["modelIo"]["selectedAttempt"], 1)
        self.assertGreaterEqual(choice.metadata["providerWallTimeMs"], 0)
        self.assertTrue(all(
            attempt["response"]["providerWallTimeMs"] >= 0
            for attempt in choice.metadata["modelIo"]["attempts"]
        ))
        self.assertIn("must be offered", client.responses.calls[1]["input"])
        response_schema = client.responses.calls[0]["text"]["format"]["schema"]
        self.assertEqual(response_schema["properties"]["channel"]["const"], "decision")
        self.assertEqual(
            response_schema["properties"]["response"]["properties"]
            ["selectedSources"]["items"]["enum"], ["e170"],
        )

    def test_mixed_mana_payment_window_keeps_native_mana_action_channel(self):
        observation = structured_observation()
        observation["pendingDecision"].update({
            "kind": "SelectManaSourcesDecision",
            "availableSources": [{"entityId": "e170", "name": "Mountain"}],
            "responseSpec": {
                "responseType": "ManaSourcesSelectedResponse",
                "requiredFields": {
                    "autoPay": "BOOLEAN", "declined": "BOOLEAN",
                    "selectedSources": "ENTITY_ID_ARRAY",
                    "waterbendPermanents": "ENTITY_ID_ARRAY",
                },
            },
        })
        observation["legalActions"] = [{
            "actionId": 0, "kind": "ActivateAbility", "isManaAbility": True,
            "semanticId": "native-mana-ability",
            "parameterSpec": {"allowedFields": {}},
        }]
        client = FakeClient(FakeResponse(json.dumps({
            "channel": "action", "semanticId": "native-mana-ability", "params": {},
        })))
        choice = choose_for_observation(
            OpenAIResponsesPilot(client=client, model="gpt-test"), observation,
        )
        self.assertEqual(choice.action_id, 0)
        self.assertEqual(client.responses.calls[0]["text"]["format"], {"type": "json_object"})
        self.assertIn("offered native mana ability", client.responses.calls[0]["instructions"])

    def test_rejects_wrong_native_array_element_kind_before_submission(self):
        observation = structured_observation()
        observation["pendingDecision"]["responseSpec"] = {
            "responseType": "ModesChosenResponse",
            "requiredFields": {"selectedModes": "INTEGER_ARRAY"},
        }
        client = FakeClient(FakeResponse(json.dumps({
            "channel": "decision",
            "response": {"type": "ModesChosenResponse", "selectedModes": [True]},
        })))
        pilot = OpenAIResponsesPilot(client=client, model="gpt-test", max_attempts=1)

        with self.assertRaisesRegex(OpenAIResponsesPilotError, "requires INTEGER_ARRAY"):
            pilot.choose(observation)
        self.assertEqual(len(client.responses.calls), 1)

    def test_native_response_spec_wire_shapes_table(self):
        # Each distinct response DTO in Argentum DecisionResponseSpec.kt. Native
        # state still decides card counts, ordering, amounts, and other legality.
        cases = [
            ("TargetsResponse", {"selectedTargets": "MAP"},
             {"selectedTargets": {"0": ["target-1"]}}, {"selectedTargets": {"0": "target-1"}}),
            ("CardsSelectedResponse", {"selectedCards": "ENTITY_ID_ARRAY"},
             {"selectedCards": ["card-1"]}, {"selectedCards": [1]}),
            ("YesNoResponse", {"choice": "BOOLEAN"}, {"choice": True}, {"choice": 1}),
            ("BatchYesNoResponse", {"choice": "BOOLEAN", "applyToAll": "BOOLEAN"},
             {"choice": False, "applyToAll": True}, {"choice": False, "applyToAll": 1}),
            ("ModesChosenResponse", {"selectedModes": "INTEGER_ARRAY"},
             {"selectedModes": [0]}, {"selectedModes": [True]}),
            ("ColorChosenResponse", {"color": "STRING"},
             {"color": "BLUE"}, {"color": 1}),
            ("NumberChosenResponse", {"number": "INTEGER"},
             {"number": 0}, {"number": True}),
            ("DistributionResponse", {"distribution": "MAP"},
             {"distribution": {"target-1": 1}}, {"distribution": {"target-1": "1"}}),
            ("OrderedResponse", {"orderedObjects": "ENTITY_ID_ARRAY"},
             {"orderedObjects": ["card-1"]}, {"orderedObjects": [1]}),
            ("PilesSplitResponse", {"piles": "ENTITY_ID_ARRAY_ARRAY"},
             {"piles": [["card-1"], []]}, {"piles": ["card-1"]}),
            ("OptionChosenResponse", {"optionIndex": "INTEGER"},
             {"optionIndex": 0}, {"optionIndex": False}),
            ("ReplacementChosenResponse", {"fromIndex": "INTEGER", "toIndex": "INTEGER"},
             {"fromIndex": 0, "toIndex": 1}, {"fromIndex": 0, "toIndex": "1"}),
            ("BudgetModalResponse", {"selectedModeIndices": "INTEGER_ARRAY"},
             {"selectedModeIndices": [0]}, {"selectedModeIndices": ["0"]}),
            ("DamageAssignmentResponse", {"assignments": "MAP"},
             {"assignments": {"target-1": 1}}, {"assignments": {"target-1": True}}),
            ("CombatResolutionResponse", {"edges": "DAMAGE_EDGE_AMOUNT_ARRAY"},
             {"edges": [{"edgeId": "edge-1", "amount": 1}]},
             {"edges": [{"edgeId": "edge-1", "amount": "1"}]}),
            ("ManaSourcesSelectedResponse", {
                "selectedSources": "ENTITY_ID_ARRAY", "autoPay": "BOOLEAN",
                "waterbendPermanents": "ENTITY_ID_ARRAY", "declined": "BOOLEAN",
             }, {"selectedSources": ["source-1"], "autoPay": False,
                 "waterbendPermanents": [], "declined": False},
             {"selectedSources": ["source-1"], "autoPay": False,
              "waterbendPermanents": [1], "declined": False}),
        ]
        for response_type, fields, valid, invalid in cases:
            with self.subTest(response_type=response_type):
                observation = structured_observation()
                pending = observation["pendingDecision"]
                pending["responseSpec"] = {"responseType": response_type, "requiredFields": fields}
                if response_type == "ManaSourcesSelectedResponse":
                    pending["availableSources"] = [{"entityId": "source-1"}]

                def answer(values):
                    return FakeResponse(json.dumps({
                        "channel": "decision", "response": {"type": response_type, **values},
                    }))

                client = FakeClient(answer(valid))
                choice = OpenAIResponsesPilot(client=client, model="gpt-test").choose(observation)
                self.assertEqual({k: choice.response[k] for k in fields}, valid)
                response_schema = client.responses.calls[0]["text"]["format"]["schema"]
                response_schema = response_schema["properties"]["response"]
                optional = {"orderedBlockers", "orderedAttackers"} if response_type == "CombatResolutionResponse" else set()
                self.assertEqual(set(response_schema["properties"]), {"type", *fields, *optional})
                self.assertEqual(set(response_schema["required"]), {"type", *fields})
                self.assertEqual(response_schema["properties"]["type"]["const"], response_type)

                bad_client = FakeClient(answer(invalid))
                with self.assertRaises(OpenAIResponsesPilotError):
                    OpenAIResponsesPilot(
                        client=bad_client, model="gpt-test", max_attempts=1,
                    ).choose(observation)
                self.assertEqual(len(bad_client.responses.calls), 1)

    def test_nested_native_response_schema_values(self):
        for response_type, field in (
            ("DistributionResponse", "distribution"),
            ("DamageAssignmentResponse", "assignments"),
        ):
            with self.subTest(response_type=response_type):
                observation = structured_observation()
                observation["pendingDecision"]["responseSpec"] = {
                    "responseType": response_type, "requiredFields": {field: "MAP"},
                }
                client = FakeClient(FakeResponse(json.dumps({
                    "channel": "decision", "response": {"type": response_type, field: {}},
                })))
                OpenAIResponsesPilot(client=client, model="gpt-test").choose(observation)
                schema = client.responses.calls[0]["text"]["format"]["schema"]
                nested = schema["properties"]["response"]["properties"][field]
                self.assertEqual(nested, {"type": "object"})

        observation = structured_observation()
        observation["pendingDecision"]["responseSpec"] = {
            "responseType": "CombatResolutionResponse",
            "requiredFields": {"edges": "DAMAGE_EDGE_AMOUNT_ARRAY"},
        }
        client = FakeClient(FakeResponse(json.dumps({
            "channel": "decision", "response": {"type": "CombatResolutionResponse", "edges": []},
        })))
        OpenAIResponsesPilot(client=client, model="gpt-test").choose(observation)
        schema = client.responses.calls[0]["text"]["format"]["schema"]
        edge = schema["properties"]["response"]["properties"]["edges"]["items"]
        self.assertEqual(set(edge["required"]), {"edgeId", "amount"})
        self.assertEqual(edge["properties"]["amount"]["type"], "integer")

    def test_native_nested_wire_rejects_kotlin_decoder_failures(self):
        bad = [
            ("DistributionResponse", "distribution", {"e12": "one"}),
            ("DamageAssignmentResponse", "assignments", {"e12": True}),
            ("CombatResolutionResponse", "edges", ["e12"]),
            ("CombatResolutionResponse", "edges", [{}]),
            ("CombatResolutionResponse", "edges", [{"edgeId": "edge-1", "amount": 2**31}]),
            ("ColorChosenResponse", "color", "chartreuse"),
            ("NumberChosenResponse", "number", 2**31),
            ("ModesChosenResponse", "selectedModes", [-(2**31) - 1]),
        ]
        for response_type, field, value in bad:
            with self.subTest(response_type=response_type, value=value):
                observation = structured_observation()
                observation["pendingDecision"]["responseSpec"] = {
                    "responseType": response_type,
                    "requiredFields": {field: {
                        "distribution": "MAP", "assignments": "MAP",
                        "edges": "DAMAGE_EDGE_AMOUNT_ARRAY", "color": "STRING",
                        "number": "INTEGER", "selectedModes": "INTEGER_ARRAY",
                    }[field]},
                }
                client = FakeClient(FakeResponse(json.dumps({
                    "channel": "decision", "response": {"type": response_type, field: value},
                })))
                with self.assertRaises(OpenAIResponsesPilotError):
                    OpenAIResponsesPilot(
                        client=client, model="gpt-test", max_attempts=1,
                    ).choose(observation)

    def test_combat_optional_order_maps_match_native_dto(self):
        observation = structured_observation()
        observation["pendingDecision"]["responseSpec"] = {
            "responseType": "CombatResolutionResponse",
            "requiredFields": {"edges": "DAMAGE_EDGE_AMOUNT_ARRAY"},
        }
        def answer(ordered):
            return FakeResponse(json.dumps({
                "channel": "decision", "response": {
                    "type": "CombatResolutionResponse", "edges": [],
                    "orderedBlockers": ordered, "orderedAttackers": {},
                },
            }))
        client = FakeClient(answer({"attacker-1": ["blocker-1"]}))
        choice = OpenAIResponsesPilot(client=client, model="gpt-test").choose(observation)
        self.assertEqual(choice.response["orderedBlockers"], {"attacker-1": ["blocker-1"]})
        schema = client.responses.calls[0]["text"]["format"]["schema"]
        nested = schema["properties"]["response"]["properties"]["orderedBlockers"]
        self.assertEqual(nested, {"type": "object"})
        with self.assertRaisesRegex(OpenAIResponsesPilotError, "ENTITY_ID_ARRAY_MAP"):
            OpenAIResponsesPilot(
                client=FakeClient(answer({"attacker-1": "blocker-1"})),
                model="gpt-test", max_attempts=1,
            ).choose(observation)

    def test_cancel_response_requires_native_cancel_allowed(self):
        for response_type, fields in (
            ("TargetsResponse", {"selectedTargets": "MAP"}),
            ("OptionChosenResponse", {"optionIndex": "INTEGER"}),
        ):
            for allowed in (True, False):
                with self.subTest(response_type=response_type, allowed=allowed):
                    observation = structured_observation()
                    observation["pendingDecision"]["responseSpec"] = {
                        "responseType": response_type, "requiredFields": fields,
                        "cancelAllowed": allowed,
                    }
                    client = FakeClient(FakeResponse(json.dumps({
                        "channel": "decision",
                        "response": {"type": "CancelDecisionResponse"},
                    })))
                    pilot = OpenAIResponsesPilot(client=client, model="gpt-test", max_attempts=1)
                    if allowed:
                        choice = pilot.choose(observation)
                        self.assertEqual(choice.response, {
                            "type": "CancelDecisionResponse", "decisionId": "routing-live-9",
                        })
                        schema = client.responses.calls[0]["text"]["format"]["schema"]
                        self.assertEqual(schema["type"], "object")
                        self.assertEqual(schema["properties"]["response"]["anyOf"][1]
                                         ["properties"]["type"]["const"], "CancelDecisionResponse")
                    else:
                        with self.assertRaisesRegex(OpenAIResponsesPilotError, "requires native"):
                            pilot.choose(observation)

    def test_unknown_semantic_id_fails_closed(self):
        client = FakeClient(
            FakeResponse(
                json.dumps(
                    {
                        "channel": "action",
                        "semanticId": "invented-action",
                        "params": {},
                    }
                )
            )
        )
        pilot = OpenAIResponsesPilot(client=client, model="gpt-test")

        with self.assertRaisesRegex(
            OpenAIResponsesPilotError,
            "not exactly one current Argentum legal action",
        ):
            pilot.choose(action_observation())

    def test_model_cannot_supply_live_structured_routing_id(self):
        client = FakeClient(
            FakeResponse(
                json.dumps(
                    {
                        "channel": "decision",
                        "response": {
                            "type": "TargetsResponse",
                            "decisionId": "invented-routing",
                            "selectedTargets": {"0": ["target-1"]},
                        },
                    }
                )
            )
        )
        pilot = OpenAIResponsesPilot(client=client, model="gpt-test")

        with self.assertRaisesRegex(OpenAIResponsesPilotError, "must not invent"):
            pilot.choose(structured_observation())

    def test_provider_failure_propagates_fail_closed(self):
        client = FakeClient(error=RuntimeError("provider unavailable"))
        pilot = OpenAIResponsesPilot(client=client, model="gpt-test")

        with self.assertRaisesRegex(OpenAIResponsesPilotError, "request failed"):
            pilot.choose(action_observation())

    def test_incomplete_provider_response_fails_closed(self):
        client = FakeClient(
            FakeResponse(
                "{}",
                status="incomplete",
                incomplete_details={"reason": "max_output_tokens"},
            )
        )
        pilot = OpenAIResponsesPilot(client=client, model="gpt-test")

        with self.assertRaisesRegex(OpenAIResponsesPilotError, "did not complete"):
            pilot.choose(action_observation())

    def test_malformed_provider_output_fails_closed(self):
        client = FakeClient(FakeResponse("not-json"))
        pilot = OpenAIResponsesPilot(client=client, model="gpt-test")

        with self.assertRaisesRegex(OpenAIResponsesPilotError, "not valid JSON"):
            pilot.choose(action_observation())


if __name__ == "__main__":
    unittest.main()
