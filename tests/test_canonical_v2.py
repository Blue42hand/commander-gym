"""Keyless contract fixtures: these tests never invoke a pilot or native game."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import unittest

from commander_gym.dataset_export import build_run_dataset_rows, DatasetExportError
from commander_gym.evidence import build_raw_evidence_envelope, validate_raw_evidence_envelope, EvidenceError
from commander_gym.records import ActionRecord, DecisionRecord, RecordValidationError
from commander_gym.record_codec import encode_record, decode_record
from tests import test_evidence


class CanonicalV2Tests(unittest.TestCase):
    def fixtures(self, params):
        legacy = test_evidence.RawEvidenceTests().make_record()
        record = replace(legacy, schema_version=2, chosen_action_params=params,
                         legal_actions=[ActionRecord(legacy.chosen_action_id, {
                             "kind": "CastSpell", "parameterSpec": {"allowedFields": {
                                 "targets": "ENTITY_ID_ARRAY", "xValue": "INTEGER",
                                 "tappedPermanents": "ENTITY_ID_ARRAY",
                                 "sacrificedPermanents": "ENTITY_ID_ARRAY",
                                 "discardedCards": "ENTITY_ID_ARRAY",
                                 "exiledCards": "ENTITY_ID_ARRAY", "delvedCards": "ENTITY_ID_ARRAY"}}})])
        run = test_evidence.RawEvidenceTests().make_run()
        return run, record

    def test_native_reader_rejects_nonobject_observation(self):
        import hashlib
        from commander_gym.captured_choices import verified_observation, CALLBACK_SCHEMA_HASH
        from commander_gym.game_journal import JournalError
        context = {"version": 2, "schemaHash": CALLBACK_SCHEMA_HASH,
                   "correlationId": "fixture", "observationBody": "[]",
                   "stateDigest": hashlib.sha256(b"[]").hexdigest()}
        with self.assertRaises(JournalError):
            verified_observation(context, "fixture-seat")

    def test_qualification_checks_survive_optimized_python(self):
        driver = Path(__file__).resolve().parents[1] / "scripts" / "qualify_canonical_v2_fixture.py"
        code = f"import runpy; runpy.run_path({str(driver)!r})['require'](False, 'qualification rejected')"
        result = subprocess.run([sys.executable, "-O", "-c", code], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ValueError: qualification rejected", result.stderr)

    def test_targets_x_and_each_payment_remain_distinct(self):
        variants = [{"targets": ["visible-a"]}, {"targets": ["visible-b"]},
                    {"xValue": 1}, {"xValue": 3}]
        variants += [{field: ["visible-payment"]} for field in
                     ("tappedPermanents", "sacrificedPermanents", "discardedCards", "exiledCards", "delvedCards")]
        targets = []
        for params in variants:
            run, record = self.fixtures(params)
            self.assertEqual(DecisionRecord.from_dict(record.to_dict()), record)
            row = build_run_dataset_rows(run, [record], schema_version=2)[0]
            envelope = build_raw_evidence_envelope(run, [record], commander_gym_revision="fixture-new-gym", schema_version=2)
            self.assertEqual(row["target"], envelope["decisions"][0]["target"])
            self.assertEqual(row["target"]["chosen_action_params"], params)
            self.assertEqual(row["dataset_schema_version"], 2)
            for excluded in ("chosen_action_params", "chosen_action_id", "metadata", "outcome", "binding", "routing"):
                self.assertNotIn(excluded, row["input"])
            data = json.dumps(record.to_dict(), sort_keys=True).encode() + b"\n"
            self.assertEqual(decode_record(encode_record(data)), data)
            targets.append(json.dumps(row["target"], sort_keys=True))
        self.assertEqual(len(set(targets)), len(variants))

    def test_v1_is_unchanged_and_never_inferred_from_metadata(self):
        record = test_evidence.RawEvidenceTests().make_record()
        run = test_evidence.RawEvidenceTests().make_run()
        record = replace(record, metadata={**record.metadata, "submitted": {"params": {"xValue": 3}}})
        self.assertNotIn("chosen_action_params", record.to_dict())
        self.assertEqual(build_run_dataset_rows(run, [record])[0]["target"], {"chosen_action_id": record.chosen_action_id})
        self.assertEqual(build_raw_evidence_envelope(run, [record], commander_gym_revision="legacy")["evidence_schema_version"], 1)
        with self.assertRaises(RecordValidationError):
            DecisionRecord.from_dict({**record.to_dict(), "schema_version": 2})
        with self.assertRaises(RecordValidationError):
            DecisionRecord.from_dict({**record.to_dict(), "chosen_action_params": {}})
        with self.assertRaises(DatasetExportError):
            build_run_dataset_rows(run, [record], schema_version=2)

    def test_explicit_empty_params_only_for_native_declared_offer(self):
        _, record = self.fixtures({})
        record = replace(record, legal_actions=[ActionRecord(record.chosen_action_id, {"parameterSpec": {"allowedFields": {}}})])
        record.validate()
        with self.assertRaises(RecordValidationError):
            replace(record, chosen_action_params=None).validate()
        with self.assertRaises(RecordValidationError):
            replace(record, legal_actions=[ActionRecord(record.chosen_action_id)]).validate()

    def test_private_routing_credentials_and_bad_shapes_are_rejected(self):
        for params in ({"decisionId": "live"}, {"authorization": "fake-secret"},
                       {"administrativeState": {}}, {"OPENAI_API_KEY": "fixture"},
                       {"targets": [{"hiddenHand": "card"}]}, {"xValue": True},
                       {"tappedPermanents": "visible-a"}):
            with self.subTest(params=params):
                _, record = self.fixtures(params)
                with self.assertRaises(RecordValidationError):
                    record.validate()

    def test_cannot_downgrade_or_mix_action_versions(self):
        run, record = self.fixtures({"xValue": 3})
        with self.assertRaises(DatasetExportError):
            build_run_dataset_rows(run, [record], schema_version=1)
        with self.assertRaises(EvidenceError):
            build_raw_evidence_envelope(run, [record], commander_gym_revision="fixture", schema_version=1)

    def test_reader_rejects_parameterless_v2_downgrade(self):
        run, record = self.fixtures({"xValue": 3})
        envelope = build_raw_evidence_envelope(run, [record], commander_gym_revision="fixture")
        envelope["evidence_schema_version"] = 1
        envelope["decisions"][0]["target"].pop("chosen_action_params")
        with self.assertRaises(EvidenceError):
            validate_raw_evidence_envelope(envelope)

    def test_envelope_reader_rejects_unoffered_missing_or_smuggled_targets(self):
        run, record = self.fixtures({"xValue": 3})
        envelope = build_raw_evidence_envelope(run, [record], commander_gym_revision="fixture")
        for target in ({"chosen_action_id": record.chosen_action_id},
                       {"chosen_action_id": "unoffered", "chosen_action_params": {}},
                       {"chosen_action_id": record.chosen_action_id, "chosen_action_params": {"credentials": "fixture"}},
                       {"chosen_action_id": record.chosen_action_id, "chosen_action_params": {}, "routing": "live"}):
            with self.subTest(target=target):
                bad = deepcopy(envelope)
                bad["decisions"][0]["target"] = target
                with self.assertRaises(EvidenceError):
                    validate_raw_evidence_envelope(bad)


if __name__ == "__main__":
    unittest.main()
