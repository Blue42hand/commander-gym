import json
import unittest

from commander_gym.openai_responses_pilot import (
    OpenAIResponsesPilot,
    OpenAIResponsesPilotError,
)
from commander_gym.pilot import (
    ArgentumActionChoice,
    ArgentumDecisionChoice,
    choose_for_observation,
)


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
        },
        "legalActions": [],
        "terminated": False,
    }


class OpenAIResponsesPilotTests(unittest.TestCase):
    def test_selects_by_semantic_id_and_hides_live_action_ids_from_model(self):
        client = FakeClient(
            FakeResponse(
                json.dumps(
                    {
                        "channel": "action",
                        "semanticId": "argentum-action-v1:attack",
                        "params": {"attackers": {"creature-1": "player-2"}},
                    }
                )
            )
        )
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

    def test_retries_one_invalid_unsubmitted_choice_and_records_retry(self):
        client = FakeClient(
            [
                FakeResponse(
                    json.dumps(
                        {
                            "channel": "action",
                            "semanticId": "invented-action",
                            "params": {},
                        }
                    )
                ),
                FakeResponse(
                    json.dumps(
                        {
                            "channel": "action",
                            "semanticId": "argentum-action-v1:pass",
                            "params": {},
                        }
                    )
                ),
            ]
        )
        pilot = OpenAIResponsesPilot(client=client, model="gpt-test")

        choice = choose_for_observation(pilot, action_observation())

        self.assertEqual(choice.action_id, 2)
        self.assertEqual(choice.metadata["retryCount"], 1)
        self.assertEqual(len(client.responses.calls), 2)
        self.assertIn("previous response was invalid", client.responses.calls[1]["input"])

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

    def test_yes_no_structured_decision_retries_wrong_native_response_shape(self):
        observation = structured_observation()
        observation["pendingDecision"] = {
            "decisionId": "routing-live-yes-no",
            "semanticId": "argentum-decision-v1:yes-no",
            "kind": "YesNoDecision",
            "type": "YesNoDecision",
            "playerId": "player-1",
            "prompt": "You may draw a card",
            "yesText": "Yes",
            "noText": "No",
            "requiresStructuredResponse": True,
        }
        client = FakeClient(
            [
                FakeResponse(
                    json.dumps(
                        {
                            "channel": "decision",
                            "response": {
                                "type": "YesNoDecision",
                                "choice": "YES",
                            },
                        }
                    )
                ),
                FakeResponse(
                    json.dumps(
                        {
                            "channel": "decision",
                            "response": {
                                "type": "YesNoResponse",
                                "choice": True,
                            },
                        }
                    )
                ),
            ]
        )
        pilot = OpenAIResponsesPilot(client=client, model="gpt-test")

        choice = choose_for_observation(pilot, observation)

        self.assertEqual(
            choice.response,
            {
                "type": "YesNoResponse",
                "choice": True,
                "decisionId": "routing-live-yes-no",
            },
        )
        self.assertEqual(choice.metadata["retryCount"], 1)
        self.assertEqual(len(client.responses.calls), 2)
        first_input = json.loads(client.responses.calls[0]["input"].split("\n", 1)[1])
        self.assertEqual(
            first_input["pendingDecision"]["responseSpec"],
            {
                "type": "YesNoResponse",
                "requiredFields": {"choice": "boolean"},
            },
        )
        self.assertIn(
            "must use type YesNoResponse",
            client.responses.calls[1]["input"],
        )

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
                            "type": "ChooseTargetsResponse",
                            "decisionId": "invented-routing",
                            "targets": ["target-1"],
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
