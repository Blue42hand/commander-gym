"""Offline compatibility checks for the merged native action/decision vocabulary."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from commander_gym.openai_responses_pilot import (
    OpenAIResponsesPilot,
    OpenAIResponsesPilotError,
    _matches_native_field_kind,
    _native_action_field_schema,
    _native_action_format,
    _native_decision_format,
    _validate_native_action_params,
)
from commander_gym.records import RecordValidationError, native_bounce_offer, validate_action_params


class UpstreamNativeContractTests(unittest.TestCase):
    def action(self, fields):
        return {"semanticId": "native:cast", "kind": "CastSpell",
                "parameterSpec": {"allowedFields": fields}}

    def test_target_groups_reach_request_schema_and_keep_order(self):
        action = self.action({"targetGroups": "ENTITY_ID_ARRAY_ARRAY"})
        schema = _native_action_format({"legalActions": [action], "pendingDecision": None})
        group_schema = schema["schema"]["properties"]["choice"]["anyOf"][0]["properties"]["params"]["properties"]["targetGroups"]
        self.assertEqual(group_schema, {"type": "array", "items": {"type": "array", "items": {"type": "string"}}})
        params = {"targetGroups": [["second", "first"], [], ["third"]]}
        self.assertEqual(_validate_native_action_params(params, action), params)
        validate_action_params(params, action)
        for invalid in [["flat"], [[1]], {"0": ["card"]}, True]:
            with self.subTest(invalid=invalid):
                with self.assertRaises(OpenAIResponsesPilotError):
                    _validate_native_action_params({"targetGroups": invalid}, action)
                with self.assertRaises(RecordValidationError):
                    validate_action_params({"targetGroups": invalid}, action)

    def test_only_native_offered_new_fields_are_admitted(self):
        for name in ["variablePermanents", "crew", "saddle", "tap", "sacrifice", "discard", "exile"]:
            with self.subTest(name=name):
                action = self.action({name: "ENTITY_ID_ARRAY"})
                params = {name: ["native-offered-id"]}
                self.assertEqual(_validate_native_action_params(params, action), params)
                validate_action_params(params, action)
                with self.assertRaises(RecordValidationError):
                    validate_action_params(params, self.action({}))
        with self.assertRaises(RecordValidationError):
            validate_action_params({}, self.action({"adminToken": "ENTITY_ID_ARRAY"}))

    def test_bounce_alias_keeps_native_candidate_and_count_checks(self):
        action = self.action({"bouncedPermanents": "ENTITY_ID_ARRAY", "bounce": "ENTITY_ID_ARRAY"})
        action["costChoices"] = {"validBounceTargets": ["own-card"], "bounceCount": 1}
        # Additive list metadata does not replace the existing authoritative cost object.
        action["costChoiceOptions"] = [{"param": "bounce", "candidates": ["own-card"], "min": 1, "max": 1}]
        self.assertEqual(_native_action_field_schema(action, "bounce", "ENTITY_ID_ARRAY")["maxItems"], 1)
        for params in [{"bounce": ["own-card"]}, {"bouncedPermanents": ["own-card"]}]:
            self.assertEqual(_validate_native_action_params(params, action), params)
            validate_action_params(params, action)
        for params in [{"bounce": ["hidden-card"]}, {"bounce": []},
                       {"bounce": ["own-card", "own-card"]},
                       {"bounce": ["own-card"], "bouncedPermanents": ["own-card"]}]:
            with self.subTest(params=params):
                with self.assertRaises(OpenAIResponsesPilotError):
                    _validate_native_action_params(params, action)
                with self.assertRaises(RecordValidationError):
                    validate_action_params(params, action)

    def test_flip_response_exposes_numeric_shape_and_rejects_nonfinite_values(self):
        observation = {"legalActions": [], "pendingDecision": {
            "kind": "FLIP_ONTO_BATTLEFIELD", "requiresStructuredResponse": True,
            "responseSpec": {"responseType": "FlipThrowResponse", "requiredFields": {
                "velocityX": "NUMBER", "velocityY": "NUMBER", "spin": "NUMBER",
            }},
        }}
        schema = _native_decision_format(observation)
        fields = schema["schema"]["properties"]["response"]["properties"]
        for field in ["velocityX", "velocityY", "spin"]:
            self.assertEqual(fields[field], {"type": "number"})
        for value in [0, 1, -0.5, 0.25, 1.0]:
            self.assertTrue(_matches_native_field_kind(value, "NUMBER"))
        for value in [True, False, "0.5", None, float("inf"), -float("inf"), float("nan"), 10**1000]:
            with self.subTest(value=repr(value)):
                self.assertFalse(_matches_native_field_kind(value, "NUMBER"))
        self.assertFalse(_matches_native_field_kind(0.5, "INTEGER"))


    def test_flip_velocity_and_spin_parse_without_legacy_aliases_or_defaults(self):
        fields = {"velocityX": "NUMBER", "velocityY": "NUMBER", "spin": "NUMBER"}
        observation = {"legalActions": [], "pendingDecision": {
            "decisionId": "live-flip", "requiresStructuredResponse": True,
            "responseSpec": {"responseType": "FlipThrowResponse", "requiredFields": fields},
        }}
        create = Mock(side_effect=AssertionError("parser test must not call a provider"))
        client = SimpleNamespace(responses=SimpleNamespace(create=create))
        pilot = OpenAIResponsesPilot(client=client, model="offline-test")
        response = {"type": "FlipThrowResponse", "velocityX": 0.25, "velocityY": -0.75, "spin": -0.125}
        schema = _native_decision_format(observation)["schema"]["properties"]["response"]
        self.assertEqual(set(schema["properties"]), {"type", *fields})
        self.assertEqual(set(schema["required"]), {"type", *fields})
        parsed = pilot._decision_choice({"response": response}, observation, {})
        self.assertEqual(parsed.response, {**response, "decisionId": "live-flip"})
        for field in fields:
            with self.subTest(missing=field):
                missing = {name: value for name, value in response.items() if name != field}
                with self.assertRaises(OpenAIResponsesPilotError):
                    pilot._decision_choice({"response": missing}, observation, {})
            for invalid in [True, "0.25", None, float("nan"), float("inf"), -float("inf")]:
                with self.subTest(field=field, invalid=repr(invalid)):
                    with self.assertRaises(OpenAIResponsesPilotError):
                        pilot._decision_choice({"response": {**response, field: invalid}}, observation, {})
        for legacy in ["aimX", "aimY", "strength", "sway"]:
            with self.subTest(legacy=legacy):
                with self.assertRaises(OpenAIResponsesPilotError):
                    pilot._decision_choice({"response": {**response, legacy: 0.25}}, observation, {})
        create.assert_not_called()

    def test_nested_bounce_costs_match_native_aggregate_and_transport(self):
        fields = {"bouncedPermanents": "ENTITY_ID_ARRAY", "bounce": "ENTITY_ID_ARRAY"}
        nested = {"validBounceTargets": ["a", "b"], "bounceCount": 1}
        for top in [
            {"costType": "TapPermanents", "validBounceTargets": [], "bounceCount": 0,
             "alsoRequired": [nested]},
            {**nested, "alsoRequired": [nested]},
        ]:
            count = top["bounceCount"] + 1
            action = {**self.action(fields), "additionalCostInfo": top}
            self.assertEqual(native_bounce_offer(action), (["a", "b"], count))
            training = {**self.action(fields), "costChoices": {
                "validBounceTargets": ["a", "b"], "bounceCount": count,
            }}
            self.assertEqual(native_bounce_offer(training), native_bounce_offer(action))
            combined = {**action, "costChoices": training["costChoices"]}
            self.assertEqual(native_bounce_offer(combined), (["a", "b"], count))
            selected = ["a", "b"][:count]
            for params in [{"bounce": selected}, {"bouncedPermanents": selected},
                           {"bounce": [], "bouncedPermanents": selected},
                           {"bounce": selected, "bouncedPermanents": []}]:
                validate_action_params(params, action)
                self.assertEqual(_validate_native_action_params(params, action), params)
            with self.assertRaises(RecordValidationError):
                native_bounce_offer({**action, "costChoices": {
                    "validBounceTargets": ["a", "other"], "bounceCount": count,
                }})
            with self.assertRaises(RecordValidationError):
                validate_action_params({"bounce": ["outside"] * count}, action)
            with self.assertRaises(RecordValidationError):
                validate_action_params({"bounce": []}, action)


if __name__ == "__main__":
    unittest.main()
