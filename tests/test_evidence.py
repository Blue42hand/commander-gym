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
        return IdentityRef(
            artifact_type="binding",
            artifact_id="binding-public-1",
            revision="r1",
            fingerprint="a" * 64,
        )

    def component_ref(self, artifact_type, artifact_id, fingerprint):
        return IdentityRef(
            artifact_type=artifact_type,
            artifact_id=artifact_id,
            revision="r1",
            fingerprint=fingerprint * 64,
        )

    def record(self, *, binding=True, routing=True):
        metadata = {
            "pilot_metadata": {
                "routing": (
                    {
                        "path": "strategic",
                        "delegate": "teacher-pilot",
                        "delegateVersion": "v7",
                        "strategicWakeAvoided": False,
                    }
                    if routing
                    else None
                ),
                "provider": "fixture-provider",
                "modelRevision": "model-r3",
                "skillRevision": "skills-r2",
                "policyRevision": "policy-r5",
                "tokenUsage": {"input": 111, "output": 22},
                "cache": {"hit": False},
                "costUsd": 0.001,
            },
            "input_state_digest": "state-before-1",
            "result_state_digest": "state-after-1",
            "timing": {"pilot_elapsed_ms": 12.5, "submission_elapsed_ms": 3.0},
            "retry_count": 0,
        }
        if not routing:
            metadata["pilot_metadata"] = {"provider": "fixture-provider"}
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
            metadata=metadata,
        )

    def run(self, *, binding=True, termination=None):
        binding_ref = self.binding_ref() if binding else None
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
            termination=termination or RunTermination(status="completed"),
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
                    binding=binding_ref,
                )
            ],
            decision_ids=["run-1:decision:0"],
            metadata={
                "pilot_configurations": [component_config] if binding else [],
            },
        )

    def test_binding_evidence_separates_input_target_and_provenance(self):
        run = self.run()
        record = self.record()
        envelope = build_raw_evidence_envelope(
            run,
            [record],
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
        self.assertEqual(decision["sequence_index"], 0)
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
        run = self.run()
        record = self.record()
        with tempfile.TemporaryDirectory() as tempdir:
            first_root = Path(tempdir) / "first"
            layout = StorageLayout.create(first_root)
            layout.ensure_directories()
            store = RawEvidenceStore(layout)

            first = store.write(
                run,
                [record],
                commander_gym_revision="cg-revision-123",
            )
            second = store.write(
                run,
                [record],
                commander_gym_revision="cg-revision-123",
            )
            self.assertEqual(first.artifact.artifact_id, second.artifact.artifact_id)
            self.assertGreater(first.accounting.raw_bytes, 0)
            self.assertEqual(first.accounting.raw_bytes, first.accounting.stored_bytes)
            self.assertGreater(first.accounting.observation_bytes, 0)
            self.assertGreater(first.accounting.evidence_input_bytes, 0)
            self.assertGreater(first.accounting.evidence_target_bytes, 0)
            self.assertEqual(first.accounting.decision_count, 1)
            self.assertIsNone(first.accounting.compressed_bytes)
            self.assertIsNone(first.accounting.model_input_bytes)
            self.assertIsNone(first.accounting.model_output_bytes)
            self.assertIsNone(first.accounting.logs_debug_bytes)

            moved_root = Path(tempdir) / "moved"
            shutil.copytree(first_root, moved_root)
            moved = RawEvidenceStore(StorageLayout.create(moved_root))
            recovered = moved.read("run-1")
            self.assertEqual(recovered["run"]["run_id"], "run-1")
            self.assertEqual(
                recovered["decisions"][0]["provenance"]["binding"],
                self.binding_ref().to_dict(),
            )

            with self.assertRaises(EvidenceError):
                store.write(
                    run,
                    [record],
                    commander_gym_revision="different-producer-revision",
                )

    def test_failed_run_without_game_is_preserved_as_diagnostic_evidence(self):
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

    def test_pre_binding_evidence_remains_explicitly_pre_binding(self):
        envelope = build_raw_evidence_envelope(
            self.run(binding=False),
            [self.record(binding=False)],
            commander_gym_revision="cg-revision-123",
        )
        self.assertEqual(envelope["identity_epoch"], IDENTITY_EPOCH_PRE_BINDING_V1)
        self.assertNotIn("binding", envelope["decisions"][0]["provenance"])

    def test_missing_actual_routing_provenance_fails_closed(self):
        with self.assertRaises(EvidenceError):
            build_raw_evidence_envelope(
                self.run(),
                [self.record(routing=False)],
                commander_gym_revision="cg-revision-123",
            )

    def test_run_decision_chronology_mismatch_fails_closed(self):
        run = replace(self.run(), decision_ids=["different-decision"])
        with self.assertRaises(EvidenceError):
            build_raw_evidence_envelope(
                run,
                [self.record()],
                commander_gym_revision="cg-revision-123",
            )

    def test_engine_schema_and_revision_are_required(self):
        run = self.run()
        missing_schema = replace(run, engine=replace(run.engine, schema=None))
        with self.assertRaises(EvidenceError):
            build_raw_evidence_envelope(
                missing_schema,
                [self.record()],
                commander_gym_revision="cg-revision-123",
            )


if __name__ == "__main__":
    unittest.main()
