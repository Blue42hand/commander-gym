from copy import deepcopy
import json
import unittest

from commander_gym.lmstudio_pilot import LMStudioPilot, LMStudioPilotError
from commander_gym.openai_responses_pilot import OpenAIResponsesPilot
from commander_gym.pilot import choose_for_observation
from tests.test_lmstudio import FakeTransport, completion, config
from tests.test_openai_responses_pilot import FakeClient, FakeResponse


def mixed_observation():
    return {
        "type": "Game", "perspectivePlayerId": "seat", "agentToAct": "seat", "terminated": False,
        "pendingDecision": {
            "kind": "SelectManaSourcesDecision", "decisionId": "live-payment-1",
            "requiresStructuredResponse": True, "canAutoPayNow": False,
            "availableSources": [{"entityId": "visible-source"}],
            "responseSpec": {"responseType": "ManaSourcesSelectedResponse", "requiredFields": {
                "autoPay": "BOOLEAN", "declined": "BOOLEAN", "selectedSources": "ENTITY_ID_ARRAY",
                "waterbendPermanents": "ENTITY_ID_ARRAY",
            }},
        },
        "legalActions": [
            {"actionId": 4, "semanticId": "offered-mana", "kind": "ActivateAbility", "isManaAbility": True,
             "parameterSpec": {"allowedFields": {}}},
            # Even if present in the envelope, this is not the native exception to
            # a structured payment. Only explicitly offered mana abilities qualify.
            {"actionId": 8, "semanticId": "nonmana", "kind": "ActivateAbility", "isManaAbility": False,
             "parameterSpec": {"allowedFields": {}}},
        ],
    }


def mana_action(**overrides):
    return {"channel": "action", "choice": {"semanticId": "offered-mana", "params": {}, **overrides}}


def payment(**overrides):
    return {"channel": "decision", "response": {
        "type": "ManaSourcesSelectedResponse", "autoPay": False, "declined": False,
        "selectedSources": ["visible-source"], "waterbendPermanents": [], **overrides,
    }}


class MixedPaymentTests(unittest.TestCase):
    def choose(self, obs, output, **kwargs):
        transport = FakeTransport(completion(json.dumps(output)))
        pilot = LMStudioPilot(config(max_attempts=1, **kwargs), transport=transport)
        return choose_for_observation(pilot, obs), transport

    def test_both_native_channels_share_constrained_union_and_original_observation(self):
        for output in (mana_action(), payment()):
            obs = mixed_observation()
            before = deepcopy(obs)
            choice, transport = self.choose(obs, output)
            request = transport.calls[1][1]
            fmt = request["response_format"]
            self.assertEqual(fmt["type"], "json_schema")
            self.assertEqual(fmt["json_schema"]["name"], "commander_gym_native_mixed_payment")
            action_schema, decision_schema = fmt["json_schema"]["schema"]["anyOf"]
            self.assertEqual(action_schema["properties"]["channel"]["const"], "action")
            self.assertEqual(len(action_schema["properties"]["choice"]["anyOf"]), 1)
            self.assertEqual(decision_schema["properties"]["channel"]["const"], "decision")
            response_schema = decision_schema["properties"]["response"]["properties"]
            self.assertEqual(response_schema["autoPay"]["const"], False)
            self.assertEqual(response_schema["selectedSources"]["items"]["enum"], ["visible-source"])
            sent = json.loads(request["messages"][1]["content"])
            self.assertIsNotNone(sent["pendingDecision"])
            self.assertEqual(len(sent["legalActions"]), 2)
            self.assertNotIn("decisionId", sent["pendingDecision"])
            self.assertTrue(all("actionId" not in action for action in sent["legalActions"]))
            self.assertEqual(obs, before)
            if output["channel"] == "action": self.assertEqual(choice.action_id, 4)
            else: self.assertEqual(choice.response["decisionId"], "live-payment-1")

    def test_manual_autopay_decline_and_cancel_preserve_native_options(self):
        for output in (payment(), payment(autoPay=True, selectedSources=[]),
                       payment(declined=True, selectedSources=[]),
                       {"channel": "decision", "response": {"type": "CancelDecisionResponse"}}):
            obs = mixed_observation()
            obs["pendingDecision"]["canAutoPayNow"] = True
            obs["pendingDecision"]["responseSpec"]["cancelAllowed"] = True
            choice, _ = self.choose(obs, output)
            self.assertEqual(choice.response, {**output["response"], "decisionId": "live-payment-1"})

    def test_invalid_source_autopay_cancel_and_nonmana_action_never_selected(self):
        invalid = [payment(selectedSources=["hidden-or-stale"]), payment(autoPay=True, selectedSources=[]),
                   payment(autoPay=True), payment(decisionId="invented"),
                   {"channel": "decision", "response": {"type": "CancelDecisionResponse"}},
                   mana_action(semanticId="nonmana"), mana_action(semanticId="stale-mana"),
                   mana_action(params={"targets": ["hidden"]})]
        for output in invalid:
            with self.subTest(output=output), self.assertRaises(LMStudioPilotError) as caught:
                self.choose(mixed_observation(), output)
            self.assertIsNone(caught.exception.model_io["selectedAttempt"])
        obs = mixed_observation()
        obs["pendingDecision"]["canAutoPayNow"] = True
        with self.assertRaises(LMStudioPilotError): self.choose(obs, payment(autoPay=True))

    def test_native_parameter_types_and_current_routing_are_still_validated(self):
        obs = mixed_observation()
        obs["legalActions"][0]["parameterSpec"]["allowedFields"] = {"xValue": "INTEGER"}
        choice, _ = self.choose(obs, mana_action(params={"xValue": 2}))
        self.assertEqual(choice.params, {"xValue": 2})
        with self.assertRaises(LMStudioPilotError): self.choose(obs, mana_action(params={"xValue": True}))
        obs["legalActions"][0]["actionId"] = 94
        obs["pendingDecision"]["decisionId"] = "fresh-payment-2"
        choice, _ = self.choose(obs, mana_action())
        self.assertEqual(choice.action_id, 94)
        choice, _ = self.choose(obs, payment())
        self.assertEqual(choice.response["decisionId"], "fresh-payment-2")
        obs["legalActions"][1]["actionId"] = 94
        with self.assertRaises(LMStudioPilotError): self.choose(obs, mana_action())

    def test_unrepresentable_payment_or_mana_channel_fails_before_network(self):
        mutations = [
            lambda o: o["pendingDecision"].pop("availableSources"),
            lambda o: o["pendingDecision"]["responseSpec"]["requiredFields"].update(unknown="MAP"),
            lambda o: o["legalActions"][0].pop("parameterSpec"),
            lambda o: o.update(legalActions=None),
        ]
        for mutation in mutations:
            obs = mixed_observation()
            mutation(obs)
            transport = FakeTransport()
            with self.subTest(mutation=mutation), self.assertRaises(LMStudioPilotError):
                LMStudioPilot(config(), transport=transport).choose(obs)
            self.assertEqual(transport.calls, [])

    def test_invalid_payment_can_retry_into_current_mana_action(self):
        transport = FakeTransport(completion(json.dumps(payment(autoPay=True))), completion(json.dumps(mana_action())))
        choice = choose_for_observation(LMStudioPilot(config(), transport=transport), mixed_observation())
        self.assertEqual(choice.action_id, 4)
        self.assertEqual(choice.metadata["modelIo"]["selectedAttempt"], 1)
        requests = [body for path, body, _ in transport.calls if body is not None]
        self.assertEqual(requests[0]["response_format"], requests[1]["response_format"])

    def test_empty_sources_and_no_mana_offer_remain_decision_only(self):
        obs = mixed_observation()
        obs["legalActions"] = []
        obs["pendingDecision"]["availableSources"] = []
        _, transport = self.choose(obs, payment(declined=True, selectedSources=[]))
        schema = transport.calls[1][1]["response_format"]["json_schema"]["schema"]
        self.assertNotIn("anyOf", schema)
        self.assertEqual(schema["properties"]["response"]["properties"]["selectedSources"]["maxItems"], 0)

    def test_openai_mixed_window_request_contract_unchanged(self):
        obs = mixed_observation()
        client = FakeClient(FakeResponse(json.dumps(mana_action())))
        choice = choose_for_observation(OpenAIResponsesPilot(client=client, model="gpt-6-luna"), obs)
        self.assertEqual(choice.action_id, 4)
        self.assertEqual(client.responses.calls[0]["text"]["format"], {"type": "json_object"})
        self.assertEqual(choice.metadata["provider"], "openai")
