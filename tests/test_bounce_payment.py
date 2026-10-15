"""Synthetic native return-cost contracts; no provider or game process is used."""
from dataclasses import replace
import json
import unittest

from commander_gym.cache_friendly_input import cache_friendly_observation_input, reconstruct_cache_friendly_observation
from commander_gym.dataset_export import build_run_dataset_rows
from commander_gym.evidence import build_raw_evidence_envelope, validate_raw_evidence_envelope
from commander_gym.game_server_seat import GameServerSeatAdapter
from commander_gym.observation_projection import compact_seat_observation, expand_seat_observation
from commander_gym.openai_responses_pilot import OpenAIResponsesPilot, OpenAIResponsesPilotError
from commander_gym.record_codec import decode_record, encode_record
from commander_gym.records import ActionRecord, DecisionRecord, RecordValidationError
from tests import test_evidence
from tests.test_openai_responses_pilot import FakeClient, FakeResponse


def bounce_action(count=1):
    return {
        "actionId": 0, "semanticId": "native-symbiote-ability", "kind": "ActivateAbility",
        "actionType": "ActivateAbility", "description": "Wirewood Symbiote: return an Elf",
        "parameterSpec": {"allowedFields": {
            "targets": "ENTITY_ID_ARRAY", "bouncedPermanents": "ENTITY_ID_ARRAY"}},
        "additionalCostInfo": {"validBounceTargets": ["elf-a", "elf-b"], "bounceCount": count},
        "action": {"type": "ActivateAbility", "playerId": "seat-a", "sourceId": "symbiote",
                   "abilityId": "return-elf-untap", "costPayment": None},
    }


def observation(action=None):
    return {"type": "GameServerSeat", "perspectivePlayerId": "seat-a", "agentToAct": "seat-a",
            "stateDigest": "synthetic-epoch", "pendingDecision": None,
            "state": {"cards": {}, "players": [{"id": "seat-b", "handSize": 4}]},
            "legalActions": [action or bounce_action()]}


def pilot(params, **settings):
    client = FakeClient(FakeResponse(json.dumps({"channel": "action", "choice": {
        "semanticId": "native-symbiote-ability", "params": params}})))
    return client, OpenAIResponsesPilot(client=client, model="offline-test", max_attempts=1, **settings)


class BouncePaymentTests(unittest.TestCase):
    def test_wirewood_schema_exact_count_and_adapter_preserves_payment_and_receipt(self):
        action, receipts = bounce_action(), []
        params = {"targets": ["symbiote"], "bouncedPermanents": ["elf-b"]}
        client, provider = pilot(params)
        result = GameServerSeatAdapter(provider, "seat-a", provenance_sink=receipts.append).choose_action(
            {"viewingPlayerId": "seat-a"}, [action], None)
        self.assertEqual(result.params, params)
        self.assertEqual(result.action, action["action"])
        self.assertIsNone(result.action["costPayment"])  # Native applies params, never Python.
        self.assertEqual(receipts[0].choice["params"], params)
        self.assertEqual(receipts[0].observation["legalActions"][0]["additionalCostInfo"], action["additionalCostInfo"])
        variant = client.responses.calls[0]["text"]["format"]["schema"]["properties"]["choice"]["anyOf"][0]
        spec = variant["properties"]["params"]
        self.assertEqual(spec["required"], ["bouncedPermanents"])
        self.assertEqual(spec["properties"]["bouncedPermanents"], {
            "type": "array", "items": {"type": "string", "enum": ["elf-a", "elf-b"]},
            "minItems": 1, "maxItems": 1})
        self.assertNotIn("uniqueItems", spec["properties"]["bouncedPermanents"])
        self.assertIn("params.bouncedPermanents", client.responses.calls[0]["instructions"])

    def test_missing_illegal_duplicate_and_wrong_count_never_yield_choice(self):
        for count, params in ((1, {}), (1, {"bouncedPermanents": []}),
                              (1, {"bouncedPermanents": ["opponent-elf"]}),
                              (1, {"bouncedPermanents": ["old-elf-object"]}),
                              (1, {"bouncedPermanents": ["elf-a", "elf-b"]}),
                              (2, {"bouncedPermanents": ["elf-a", "elf-a"]}),
                              (2, {"bouncedPermanents": ["elf-a"]})):
            with self.subTest(count=count, params=params):
                _, provider = pilot(params)
                with self.assertRaisesRegex(OpenAIResponsesPilotError, "distinct native offered IDs"):
                    provider.choose(observation(bounce_action(count)))

    def test_two_permanent_cost_and_current_offer_ids_only(self):
        _, provider = pilot({"bouncedPermanents": ["elf-b", "elf-a"]})
        self.assertEqual(provider.choose(observation(bounce_action(2))).params,
                         {"bouncedPermanents": ["elf-b", "elf-a"]})
        action = bounce_action()
        action["additionalCostInfo"]["validBounceTargets"] = ["fresh-elf-object"]
        _, provider = pilot({"bouncedPermanents": ["elf-a"]})
        with self.assertRaisesRegex(OpenAIResponsesPilotError, "distinct native offered IDs"):
            provider.choose(observation(action))

    def test_training_observation_cost_choices_uses_same_native_contract(self):
        action = bounce_action()
        action["costChoices"] = action.pop("additionalCostInfo")
        _, provider = pilot({"bouncedPermanents": ["elf-a"]})
        self.assertEqual(provider.choose(observation(action)).params, {"bouncedPermanents": ["elf-a"]})
        legacy = test_evidence.RawEvidenceTests().make_record()
        record = replace(legacy, schema_version=2, chosen_action_params={"bouncedPermanents": ["elf-a"]},
                         legal_actions=[ActionRecord(legacy.chosen_action_id, action)])
        self.assertEqual(DecisionRecord.from_dict(record.to_dict()), record)
        action["additionalCostInfo"] = dict(action["costChoices"])
        record.validate()
        action["additionalCostInfo"] = {"validBounceTargets": ["different"], "bounceCount": 1}
        with self.assertRaisesRegex(RecordValidationError, "offers disagree"):
            record.validate()
        action.pop("additionalCostInfo")
        action["costChoices"]["validBounceTargets"] = ["elf-b"]
        with self.assertRaisesRegex(RecordValidationError, "distinct native offered IDs"):
            record.validate()

    def test_unoffered_and_malformed_native_offer_fail_closed(self):
        action = bounce_action()
        action["parameterSpec"]["allowedFields"].pop("bouncedPermanents")
        _, provider = pilot({"bouncedPermanents": ["elf-a"]})
        with self.assertRaisesRegex(OpenAIResponsesPilotError, "not allowed by native"):
            provider.choose(observation(action))
        for info in (None, {}, {"validBounceTargets": [], "bounceCount": 1},
                     {"validBounceTargets": ["elf-a", "elf-a"], "bounceCount": 1},
                     {"validBounceTargets": ["elf-a"], "bounceCount": True},
                     {"validBounceTargets": ["elf-a"], "bounceCount": 2},
                     {"validBounceTargets": ["elf-a"], "bounceCount": 0},
                     {"validBounceTargets": ["elf-a"], "bounceCount": -1},
                     {"validBounceTargets": ["elf-a"], "bounceCount": "1"},
                     {"validBounceTargets": [{"entityId": "elf-a"}], "bounceCount": 1}):
            with self.subTest(info=info):
                action = bounce_action()
                action["additionalCostInfo"] = info
                client, provider = pilot({"bouncedPermanents": ["elf-a"]})
                with self.assertRaisesRegex(OpenAIResponsesPilotError, "native bounce payment offer is malformed"):
                    provider.choose(observation(action))
                self.assertEqual(client.responses.calls, [])

    def test_compact_cache_roundtrip_preserves_all_bounce_candidates_and_settings(self):
        obs, before = observation(), observation()
        for compact in (False, True):
            view = compact_seat_observation(obs) if compact else obs
            restored = reconstruct_cache_friendly_observation(cache_friendly_observation_input(view))
            if compact:
                restored = expand_seat_observation(restored)
            self.assertEqual(restored, before)
        for cached in (False, True):
            client, provider = pilot({"bouncedPermanents": ["elf-a"]},
                                     compact_model_observation=True, cache_friendly_history=cached)
            self.assertEqual(provider.choose(obs).params, {"bouncedPermanents": ["elf-a"]})
            wire = json.dumps(client.responses.calls)
            self.assertIn("elf-b", wire)
        self.assertEqual(obs, before)

    def test_canonical_record_dataset_envelope_and_codec_preserve_payment(self):
        legacy = test_evidence.RawEvidenceTests().make_record()
        record = replace(legacy, schema_version=2, chosen_action_params={"bouncedPermanents": ["elf-b"]},
                         legal_actions=[ActionRecord(legacy.chosen_action_id, bounce_action())])
        run = test_evidence.RawEvidenceTests().make_run()
        self.assertEqual(DecisionRecord.from_dict(record.to_dict()), record)
        row = build_run_dataset_rows(run, [record], schema_version=2)[0]
        envelope = build_raw_evidence_envelope(run, [record], commander_gym_revision="synthetic")
        validate_raw_evidence_envelope(envelope)
        self.assertEqual(row["target"], envelope["decisions"][0]["target"])
        self.assertEqual(row["target"]["chosen_action_params"], {"bouncedPermanents": ["elf-b"]})
        encoded = json.dumps(record.to_dict()).encode() + b"\n"
        self.assertEqual(decode_record(encode_record(encoded)), encoded)
        for params in ({}, {"bouncedPermanents": ["not-offered"]}, {"bouncedPermanents": ["elf-a", "elf-a"]}):
            with self.subTest(params=params), self.assertRaises(RecordValidationError):
                replace(record, chosen_action_params=params).validate()
        historical = replace(record, legal_actions=[ActionRecord(legacy.chosen_action_id,
                             {"parameterSpec": {"allowedFields": {}}})], chosen_action_params={})
        self.assertEqual(DecisionRecord.from_dict(historical.to_dict()), historical)
