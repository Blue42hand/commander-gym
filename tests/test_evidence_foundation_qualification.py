import shutil
import tempfile
import unittest
from pathlib import Path

from commander_gym.annotations import (
    ANNOTATION_TARGET_DECISION,
    AnnotationRecord,
    AnnotationStore,
)
from commander_gym.dataset_manifest import (
    DatasetEvidenceSelection,
    DatasetManifest,
    DatasetManifestError,
    DatasetManifestStore,
    DatasetSplit,
)
from commander_gym.evidence import RawEvidenceStore
from commander_gym.identity import IdentityRef
from commander_gym.manifest_export import build_manifest_training_rows
from commander_gym.records import ActionRecord, DecisionRecord, PilotProvenance
from commander_gym.run_records import (
    EngineProvenance,
    RunParticipant,
    RunRecord,
    RunTermination,
)
from commander_gym.storage import StorageLayout


class EvidenceFoundationQualificationTests(unittest.TestCase):
    """Qualify the settled #53 contracts as one composable foundation path."""

    def binding_ref(self):
        return IdentityRef("binding", "binding-foundation-1", "r1", "a" * 64)

    def pilot(self):
        return PilotProvenance(
            source="commander-gym",
            implementation="routing-pilot",
            version="v4",
            model="fixture-model",
        )

    def record(self, *, suffix):
        return DecisionRecord(
            game_id=f"game-{suffix}",
            decision_id=f"run-{suffix}:decision:0",
            decision_type="PassPriority",
            seat=0,
            observation_schema="argentum-schema-v9",
            observation={
                "schemaHash": "argentum-schema-v9",
                "stateDigest": f"before-{suffix}",
                "seatVisible": True,
            },
            legal_actions=[
                ActionRecord(
                    action_id="pass",
                    payload={"kind": "PassPriority"},
                    label="Pass priority",
                )
            ],
            chosen_action_id="pass",
            pilot=self.pilot(),
            deck_id="deck-public-1",
            deck_version="r1",
            primer_version="knowledge-r1",
            binding=self.binding_ref(),
            outcome={
                "winner": 0,
                "result_observation": {
                    "stateDigest": f"after-{suffix}",
                    "post_choice_only": "provenance-only",
                },
            },
            metadata={
                "input_state_digest": f"before-{suffix}",
                "result_state_digest": f"after-{suffix}",
                "pilot_metadata": {
                    "routing": {
                        "path": "strategic",
                        "delegate": "fixture-strategic-pilot",
                    },
                    "provider": "fixture-provider",
                    "modelRevision": "model-r3",
                },
            },
        )

    def make_run(self, *, suffix, status="completed"):
        reason = None
        failure_domain = None
        if status == "failed":
            reason = "provider transport failed after the recorded decision"
            failure_domain = "provider"
        elif status == "stopped":
            reason = "bounded qualification choice limit"
        return RunRecord(
            run_id=f"run-{suffix}",
            game_id=f"game-{suffix}",
            started_at="2026-09-24T05:00:00Z",
            finished_at="2026-09-24T05:00:01Z",
            engine=EngineProvenance(
                implementation="argentum",
                version="engine-build-123",
                schema="argentum-schema-v9",
                revision="engine-build-123",
            ),
            termination=RunTermination(
                status=status,
                reason=reason,
                failure_domain=failure_domain,
            ),
            participants=[
                RunParticipant(
                    seat=0,
                    pilot=self.pilot(),
                    deck_id="deck-public-1",
                    deck_version="r1",
                    primer_version="knowledge-r1",
                    binding=self.binding_ref(),
                )
            ],
            decision_ids=[f"run-{suffix}:decision:0"],
        )

    def write_evidence(self, layout, *, suffix, status="completed"):
        result = RawEvidenceStore(layout).write(
            self.make_run(suffix=suffix, status=status),
            [self.record(suffix=suffix)],
            commander_gym_revision="cg-foundation-qualification",
        )
        return result

    def manifest(
        self,
        *,
        version,
        train_artifact,
        train_decision,
        held_out_artifact,
        held_out_decision,
        annotations=(),
    ):
        return DatasetManifest(
            dataset_id="foundation-evidence-qualification",
            version=version,
            purpose="synthetic #53 evidence foundation qualification",
            created_at="2026-09-24T05:00:02Z",
            source_population="canonical binding-v1 raw evidence",
            source_query={"qualification": "completed", "identity_epoch": "binding-v1"},
            selection_rules={"decision_types": ["PassPriority"]},
            exclusion_rules={"diagnostic_only": True},
            required_annotation_artifact_ids=tuple(annotations),
            transformations=({"name": "input-target-provenance", "version": 1},),
            splits=(
                DatasetSplit(
                    name="train",
                    role="train",
                    selections=(
                        DatasetEvidenceSelection(train_artifact, (train_decision,)),
                    ),
                ),
                DatasetSplit(
                    name="held-out",
                    role="frozen_test",
                    selections=(
                        DatasetEvidenceSelection(held_out_artifact, (held_out_decision,)),
                    ),
                ),
            ),
            generator_revision="foundation-qualification-r1",
        )

    def test_raw_annotation_manifest_export_pipeline_and_diagnostic_qualification(self):
        with tempfile.TemporaryDirectory() as tempdir:
            first_root = Path(tempdir) / "first"
            layout = StorageLayout.create(first_root)
            layout.ensure_directories()

            train = self.write_evidence(layout, suffix="train")
            held_out = self.write_evidence(layout, suffix="held-out")
            failed = self.write_evidence(layout, suffix="failed", status="failed")
            partial = self.write_evidence(layout, suffix="partial", status="stopped")

            raw_store = RawEvidenceStore(layout)
            self.assertEqual(
                raw_store.read("run-train")["qualification"]["classification"],
                "completed",
            )
            self.assertEqual(
                raw_store.read("run-failed")["qualification"]["classification"],
                "failed",
            )
            self.assertTrue(
                raw_store.read("run-failed")["qualification"]["diagnostic_only"]
            )
            self.assertEqual(
                raw_store.read("run-partial")["qualification"]["classification"],
                "partial",
            )
            self.assertTrue(
                raw_store.read("run-partial")["qualification"]["diagnostic_only"]
            )

            annotation = AnnotationStore(layout).write(
                AnnotationRecord(
                    annotation_id="foundation-teacher-review",
                    revision="r1",
                    created_at="2026-09-24T05:00:02Z",
                    source_evidence_artifact_id=train.artifact.artifact_id,
                    target_kind=ANNOTATION_TARGET_DECISION,
                    target_id="run-train:decision:0",
                    annotation_type="teacher-adjudication",
                    annotator={
                        "source": "teacher-model",
                        "provider": "fixture-provider",
                        "model_revision": "teacher-r1",
                    },
                    payload={"preferred": True, "material_error": False},
                )
            )

            manifest = self.manifest(
                version="r1",
                train_artifact=train.artifact.artifact_id,
                train_decision="run-train:decision:0",
                held_out_artifact=held_out.artifact.artifact_id,
                held_out_decision="run-held-out:decision:0",
                annotations=(annotation.artifact.artifact_id,),
            )
            manifest_written = DatasetManifestStore(layout).write(manifest)
            rows = build_manifest_training_rows(layout, manifest_written.artifact.artifact_id)

            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["target"], {"chosen_action_id": "pass"})
            self.assertEqual(row["provenance"]["decision_id"], "run-train:decision:0")
            self.assertEqual(
                row["provenance"]["dataset_manifest"]["manifest_artifact_id"],
                manifest_written.artifact.artifact_id,
            )
            self.assertEqual(
                row["provenance"]["dataset_manifest"]["source_evidence_artifact_id"],
                train.artifact.artifact_id,
            )
            self.assertNotIn("outcome", row["input"])
            self.assertNotIn("routing", row["input"])
            self.assertNotIn("binding", row["input"])
            self.assertEqual(row["provenance"]["routing"]["path"], "strategic")
            self.assertEqual(
                row["provenance"]["binding"]["artifact_id"],
                "binding-foundation-1",
            )

            # Failed and partial trajectories are durable diagnostics, but cannot enter
            # an ordinary training split without an explicit safe-subset justification.
            for version, diagnostic, decision_id in (
                ("failed-r1", failed, "run-failed:decision:0"),
                ("partial-r1", partial, "run-partial:decision:0"),
            ):
                diagnostic_manifest = self.manifest(
                    version=version,
                    train_artifact=diagnostic.artifact.artifact_id,
                    train_decision=decision_id,
                    held_out_artifact=held_out.artifact.artifact_id,
                    held_out_decision="run-held-out:decision:0",
                )
                with self.assertRaises(DatasetManifestError):
                    DatasetManifestStore(layout).write(diagnostic_manifest)

            # #74 owns the portability contract; #53 proves its semantic artifacts keep
            # composing after the already-supported root relocation.
            moved_root = Path(tempdir) / "moved"
            shutil.copytree(first_root, moved_root)
            moved_layout = StorageLayout.create(moved_root)
            moved_rows = build_manifest_training_rows(
                moved_layout,
                manifest_written.artifact.artifact_id,
            )
            self.assertEqual(moved_rows, rows)
            moved_annotation = AnnotationStore(moved_layout).read_artifact(
                annotation.artifact.artifact_id
            )
            self.assertEqual(moved_annotation.target_id, "run-train:decision:0")
            moved_manifest = DatasetManifestStore(moved_layout).read(
                dataset_id=manifest.dataset_id,
                version=manifest.version,
            )
            self.assertEqual(moved_manifest.dataset_digest, manifest.dataset_digest)


if __name__ == "__main__":
    unittest.main()
