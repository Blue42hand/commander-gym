import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from commander_gym.evidence import (
    EvidenceError,
    IDENTITY_EPOCH_BINDING_V1,
    IDENTITY_EPOCH_PRE_BINDING_V1,
    RawEvidenceStore,
    build_raw_evidence_envelope,
)
from commander_gym.identity import IdentityRef
from commander_gym.records import ActionRecord, DecisionRecord, PilotProvenance
from commander_gym.run_records import (
    EngineProvenance,
    RunParticipant,
    RunRecord,
    RunTermination,
)
from commander_gym.storage import StorageLayout


class RawEvidenceTests(unittest.TestCase):
    def binding_ref(self):
        return IdentityRef("binding", "binding-public-1", "r1", "a" * 64)

    def component_ref(self, artifact_type, artifact_id, char):
        return IdentityRef(artifact_type, artifact_id, "r1", char * 64)

    def make_record(self, *, binding=True, routing=True):
        pilot_metadata = {
            "provider": "fixture-provider",
            "modelRevision": "model-r3",
            "skillRevision": "skills-r2",
            "policyRevision": "policy-r5",
            "tokenUsage": {"input": 111, "output": 22},
            "cache": {"hit": False},
            "costUsd": 0.001,
        }
        if routing:
            pilot_metadata["routing"] = {
                "path": "strategic",
                "delegate": "teacher-pilot",
                "delegateVersion": "v7",
                "strategicWakeAvoided": False,
            }
        return DecisionRecord(
            game_id="game-1",
            decision_id="run-1:decision:0",
            decision_type="PassPriority",
            seat=0,
            observation_schema="argentum-schema-v9",
            observation={
                "schemaHash": "argentum-schema-v9",
                "stateDigest": "state-before-1",
                "seatVisible": True,
            },
            legal_actions=[
                ActionRecord(
                    action_id="action-semantic-1",
                    payload={"kind": "PassPriority"},
                    label="Pass priority",
                )
            ],
            chosen_action_id="action-semantic-1",
            pilot=PilotProvenance(
                source="commander-gym",
                implementation="routing-pilot",
                version="v4",
                model="fixture-model",
            ),
            deck_id="deck-public-1",
            deck_version="r1",
            primer_version="knowledge-r1",
            binding=self.binding_ref() if binding else None,
            outcome={
                "result_observation": {
                    "stateDigest": "state-after-1",
                    "post_choice_only": "must-not-leak-into-input",
                }
            },
            metadata={
                "pilot_metadata": pilot_metadata,
                "input_state_digest": "state-before-1",
                "result_state_digest": "state-after-1",
                "timing": {
                    "pilot_elapsed_ms": 12.5,
                    "submission_elapsed_ms": 3.0,
                },
                "retry_count": 0,
            },
        )

    def make_run(self, *, binding=True):
        component_config = {
            "source": "canonical-binding-v1",
            "binding": self.binding_ref().to_dict(),
            "deck": self.component_ref("deck", "deck-public-1", "b").to_dict(),
            "deck_knowledge": self.component_ref(
                "deck_knowledge", "knowledge-public-1", "c"
            ).to_dict(),
            "pilot": self.component_ref("pilot", "pilot-public-1", "d").to_dict(),
        }
        return RunRecord(
            run_id="run-1",
            game_id="game-1",
            started_at="2026-09-23T16:00:00Z",
            finished_at="2026-09-23T16:00:05Z",
            engine=EngineProvenance(
                implementation="argentum",
                version="engine-build-123",
                schema="argentum-schema-v9",
                revision="engine-build-123",
            ),
            termination=RunTermination(status="completed"),
            participants=[
                RunParticipant(
                    seat=0,
                    pilot=PilotProvenance(
                        source="commander-gym",
                        implementation="routing-pilot",
                        version="v4",
                        model="fixture-model",
                    ),
                    deck_id="deck-public-1",
                    deck_version="r1",
                    primer_version="knowledge-r1",
                    binding=self.binding_ref() if binding else None,
                )
            ],
            decision_ids=["run-1:decision:0"],
            metadata={
                "pilot_configurations": [component_config] if binding else [],
            },
        )

    def test_binding_evidence_separates_input_target_and_provenance(self):
        run = self.make_run()
        envelope = build_raw_evidence_envelope(
            run,
            [self.make_record()],
            commander_gym_revision="cg-revision-123",
        )

        self.assertEqual(envelope["identity_epoch"], IDENTITY_EPOCH_BINDING_V1)
        self.assertEqual(envelope["qualification"]["classification"], "completed")
        self.assertFalse(envelope["qualification"]["diagnostic_only"])
        self.assertEqual(
            envelope["run"]["metadata"]["pilot_configurations"],
            run.metadata["pilot_configurations"],
        )

        decision = envelope["decisions"][0]
        self.assertEqual(decision["target"], {"chosen_action_id": "action-semantic-1"})
        self.assertNotIn("chosen_action_id", decision["input"])
        self.assertNotIn("outcome", decision["input"])
        self.assertNotIn("post_choice_only", str(decision["input"]))
        self.assertEqual(decision["provenance"]["binding"], self.binding_ref().to_dict())
        self.assertEqual(decision["provenance"]["routing"]["path"], "strategic")
        self.assertEqual(
            decision["provenance"]["metadata"]["pilot_metadata"]["modelRevision"],
            "model-r3",
        )
        self.assertEqual(
            decision["provenance"]["result_state_digest"], "state-after-1"
        )

    def test_store_is_immutable_accounted_and_path_portable(self):
        run = self.make_run()
        record = self.make_record()
        with tempfile.TemporaryDirectory() as tempdir:
            first_root = Path(tempdir) / "first"
            first_layout = StorageLayout.create(first_root)
            first_layout.ensure_directories()
            first_store = RawEvidenceStore(first_layout)
            written = first_store.write(
                run,
                [record],
                commander_gym_revision="cg-revision-123",
            )
            repeated = first_store.write(
                run,
                [record],
                commander_gym_revision="cg-revision-123",
            )
            self.assertEqual(written.artifact.artifact_id, repeated.artifact.artifact_id)
            self.assertGreater(written.accounting.raw_bytes, 0)
            self.assertEqual(written.accounting.raw_bytes, written.accounting.stored_bytes)
            self.assertGreater(written.accounting.observation_bytes, 0)
            self.assertEqual(written.accounting.decision_count, 1)
            self.assertIsNone(written.accounting.compressed_bytes)
            self.assertIsNone(written.accounting.model_input_bytes)

            moved_root = Path(tempdir) / "moved"
            shutil.copytree(first_root, moved_root)
            recovered = RawEvidenceStore(StorageLayout.create(moved_root)).read("run-1")
            self.assertEqual(recovered["run"]["run_id"], "run-1")
            self.assertEqual(
                recovered["decisions"][0]["provenance"]["binding"],
                self.binding_ref().to_dict(),
            )

            with self.assertRaises(EvidenceError):
                first_store.write(
                    run,
                    [record],
                    commander_gym_revision="different-producer-revision",
                )

    def test_failed_run_is_preserved_as_diagnostic_evidence(self):
        failed = RunRecord(
            run_id="failed-preflight",
            started_at="2026-09-23T16:00:00Z",
            finished_at="2026-09-23T16:00:01Z",
            engine=EngineProvenance(
                implementation="argentum",
                version="engine-build-123",
                schema="argentum-schema-v9",
                revision="engine-build-123",
            ),
            termination=RunTermination(
                status="failed",
                reason="provider unavailable",
                failure_domain="provider",
            ),
        )
        envelope = build_raw_evidence_envelope(
            failed,
            [],
            commander_gym_revision="cg-revision-123",
        )
        self.assertEqual(envelope["identity_epoch"], IDENTITY_EPOCH_PRE_BINDING_V1)
        self.assertEqual(envelope["qualification"]["classification"], "failed")
        self.assertTrue(envelope["qualification"]["diagnostic_only"])
        self.assertEqual(envelope["qualification"]["failure_domain"], "provider")
        self.assertEqual(envelope["decisions"], [])

    def test_pre_binding_epoch_and_fail_closed_requirements(self):
        legacy = build_raw_evidence_envelope(
            self.make_run(binding=False),
            [self.make_record(binding=False)],
            commander_gym_revision="cg-revision-123",
        )
        self.assertEqual(legacy["identity_epoch"], IDENTITY_EPOCH_PRE_BINDING_V1)
        self.assertNotIn("binding", legacy["decisions"][0]["provenance"])

        with self.assertRaises(EvidenceError):
            build_raw_evidence_envelope(
                self.make_run(),
                [self.make_record(routing=False)],
                commander_gym_revision="cg-revision-123",
            )

        with self.assertRaises(EvidenceError):
            build_raw_evidence_envelope(
                replace(self.make_run(), decision_ids=["different-decision"]),
                [self.make_record()],
                commander_gym_revision="cg-revision-123",
            )

        missing_schema = replace(
            self.make_run(),
            engine=replace(self.make_run().engine, schema=None),
        )
        with self.assertRaises(EvidenceError):
            build_raw_evidence_envelope(
                missing_schema,
                [self.make_record()],
                commander_gym_revision="cg-revision-123",
            )


if __name__ == "__main__":
    unittest.main()
