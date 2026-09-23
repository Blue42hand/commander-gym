import unittest

from commander_gym.dataset_export import DatasetExportError, build_run_dataset_rows
from commander_gym.identity import IdentityRef
from commander_gym.records import ActionRecord, DecisionRecord, PilotProvenance
from commander_gym.run_records import (
    EngineProvenance,
    RunParticipant,
    RunRecord,
    RunTermination,
)


def binding_ref(hex_digit: str = "a") -> IdentityRef:
    return IdentityRef(
        artifact_type="binding",
        artifact_id="binding-public-synthetic",
        revision="r1",
        fingerprint=hex_digit * 64,
    )


def decision(binding: IdentityRef | None) -> DecisionRecord:
    return DecisionRecord(
        game_id="game-1",
        decision_id="decision-1",
        decision_type="LEGAL_ACTION",
        seat=0,
        observation_schema="schema-v1",
        observation={"perspectivePlayerId": 1},
        legal_actions=[ActionRecord(action_id="action-a", label="Pass")],
        chosen_action_id="action-a",
        pilot=PilotProvenance(
            source="test",
            implementation="fixture",
            version="v1",
        ),
        deck_id="deck-public-synthetic",
        deck_version="r1",
        binding=binding,
    )


def run(binding: IdentityRef | None) -> RunRecord:
    return RunRecord(
        run_id="run-1",
        started_at="2026-09-23T20:00:00Z",
        finished_at="2026-09-23T20:00:01Z",
        engine=EngineProvenance(implementation="argentum", version="test"),
        termination=RunTermination(status="completed"),
        participants=[
            RunParticipant(
                seat=0,
                pilot=PilotProvenance(
                    source="test",
                    implementation="fixture",
                    version="v1",
                ),
                deck_id="deck-public-synthetic",
                deck_version="r1",
                binding=binding,
            )
        ],
        game_id="game-1",
        decision_ids=["decision-1"],
    )


class BindingProvenanceTests(unittest.TestCase):
    def test_binding_identity_round_trips_in_run_and_decision_records(self):
        binding = binding_ref()
        recorded_decision = decision(binding)
        recorded_run = run(binding)

        self.assertEqual(
            DecisionRecord.from_dict(recorded_decision.to_dict()).binding,
            binding,
        )
        self.assertEqual(
            RunRecord.from_dict(recorded_run.to_dict()).participants[0].binding,
            binding,
        )

    def test_dataset_export_joins_and_emits_exact_binding_in_provenance_only(self):
        binding = binding_ref()
        rows = build_run_dataset_rows(run(binding), [decision(binding)])

        self.assertEqual(rows[0]["provenance"]["binding"], binding.to_dict())
        self.assertNotIn("binding", rows[0]["input"])
        self.assertNotIn("binding", rows[0]["target"])

    def test_dataset_export_rejects_binding_mismatch(self):
        with self.assertRaisesRegex(DatasetExportError, "Binding does not match"):
            build_run_dataset_rows(run(binding_ref("a")), [decision(binding_ref("b"))])

    def test_dataset_export_rejects_one_sided_binding_identity(self):
        with self.assertRaisesRegex(DatasetExportError, "Binding presence does not match"):
            build_run_dataset_rows(run(binding_ref()), [decision(None)])

    def test_legacy_records_remain_binding_optional_and_serialize_without_new_field(self):
        legacy_decision = decision(None)
        legacy_run = run(None)

        self.assertNotIn("binding", legacy_decision.to_dict())
        self.assertNotIn("binding", legacy_run.to_dict()["participants"][0])
        rows = build_run_dataset_rows(legacy_run, [legacy_decision])
        self.assertNotIn("binding", rows[0]["provenance"])


if __name__ == "__main__":
    unittest.main()
