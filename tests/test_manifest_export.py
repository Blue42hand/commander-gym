import shutil
import tempfile
import unittest
from pathlib import Path

from commander_gym.dataset_manifest import (
    DatasetEvidenceSelection,
    DatasetManifest,
    DatasetManifestStore,
    DatasetSplit,
)
from commander_gym.evidence import RawEvidenceStore
from commander_gym.identity import IdentityRef
from commander_gym.manifest_export import (
    ManifestExportError,
    build_manifest_training_rows,
    write_manifest_training_jsonl,
)
from commander_gym.records import ActionRecord, DecisionRecord, PilotProvenance
from commander_gym.run_records import EngineProvenance, RunParticipant, RunRecord, RunTermination
from commander_gym.storage import StorageLayout


class ManifestExportTests(unittest.TestCase):
    def binding_ref(self):
        return IdentityRef("binding", "binding-public-1", "r1", "a" * 64)

    def pilot(self):
        return PilotProvenance(
            source="commander-gym",
            implementation="routing-pilot",
            version="v4",
            model="fixture-model",
        )

    def make_record(self, *, game_id, decision_id, before, after):
        return DecisionRecord(
            game_id=game_id,
            decision_id=decision_id,
            decision_type="PassPriority",
            seat=0,
            observation_schema="argentum-schema-v9",
            observation={"schemaHash": "argentum-schema-v9", "stateDigest": before},
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
            outcome={"winner": 0, "result_observation": {"stateDigest": after}},
            metadata={
                "input_state_digest": before,
                "result_state_digest": after,
                "pilot_metadata": {
                    "routing": {"path": "strategic"},
                    "provider": "fixture-provider",
                    "modelRevision": "model-r3",
                },
            },
        )

    def make_run(self, *, run_id, game_id, decision_id):
        return RunRecord(
            run_id=run_id,
            game_id=game_id,
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
                    pilot=self.pilot(),
                    deck_id="deck-public-1",
                    deck_version="r1",
                    primer_version="knowledge-r1",
                    binding=self.binding_ref(),
                )
            ],
            decision_ids=[decision_id],
        )

    def write_evidence(self, layout, *, suffix):
        game_id = f"game-{suffix}"
        run_id = f"run-{suffix}"
        decision_id = f"{run_id}:decision:0"
        result = RawEvidenceStore(layout).write(
            self.make_run(run_id=run_id, game_id=game_id, decision_id=decision_id),
            [
                self.make_record(
                    game_id=game_id,
                    decision_id=decision_id,
                    before=f"before-{suffix}",
                    after=f"after-{suffix}",
                )
            ],
            commander_gym_revision="cg-revision-123",
        )
        return result.artifact.artifact_id, decision_id

    def write_manifest(self, layout, *, transform=None):
        train_source, train_decision = self.write_evidence(layout, suffix="train")
        test_source, test_decision = self.write_evidence(layout, suffix="held-out")
        manifest = DatasetManifest(
            dataset_id="pilot-imitation-v1",
            version="r1",
            purpose="teacher imitation fixture",
            created_at="2026-09-24T02:30:00Z",
            source_population="qualified binding-v1 raw evidence",
            source_query={"qualification": "completed", "identity_epoch": "binding-v1"},
            selection_rules={"decision_types": ["PassPriority"]},
            exclusion_rules={"technical_failures": True},
            required_annotation_artifact_ids=(),
            transformations=(
                transform
                or {"name": "input-target-provenance", "version": 1},
            ),
            splits=(
                DatasetSplit(
                    name="train",
                    role="train",
                    selections=(DatasetEvidenceSelection(train_source, (train_decision,)),),
                ),
                DatasetSplit(
                    name="held-out",
                    role="frozen_test",
                    selections=(DatasetEvidenceSelection(test_source, (test_decision,)),),
                ),
            ),
            generator_revision="dataset-generator-r1",
        )
        written = DatasetManifestStore(layout).write(manifest)
        return manifest, written.artifact.artifact_id, train_source, train_decision, test_decision

    def test_training_rows_are_manifest_backed_and_keep_leakage_out_of_input(self):
        with tempfile.TemporaryDirectory() as tempdir:
            layout = StorageLayout.create(Path(tempdir) / "data")
            layout.ensure_directories()
            manifest, manifest_artifact_id, train_source, train_decision, _ = self.write_manifest(
                layout
            )

            rows = build_manifest_training_rows(layout, manifest_artifact_id)
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["target"], {"chosen_action_id": "pass"})
            self.assertEqual(row["provenance"]["decision_id"], train_decision)
            self.assertEqual(
                row["provenance"]["dataset_manifest"]["manifest_artifact_id"],
                manifest_artifact_id,
            )
            self.assertEqual(
                row["provenance"]["dataset_manifest"]["dataset_digest"],
                manifest.dataset_digest,
            )
            self.assertEqual(
                row["provenance"]["dataset_manifest"]["source_evidence_artifact_id"],
                train_source,
            )
            self.assertEqual(
                row["provenance"]["source_evidence"]["qualification"]["classification"],
                "completed",
            )
            self.assertNotIn("outcome", row["input"])
            self.assertNotIn("routing", row["input"])
            self.assertNotIn("binding", row["input"])
            self.assertEqual(row["provenance"]["outcome"]["winner"], 0)
            self.assertEqual(row["provenance"]["routing"]["path"], "strategic")
            self.assertEqual(row["provenance"]["binding"]["id"], "binding-public-1")

    def test_training_export_rejects_frozen_test_split(self):
        with tempfile.TemporaryDirectory() as tempdir:
            layout = StorageLayout.create(Path(tempdir) / "data")
            layout.ensure_directories()
            _, manifest_artifact_id, _, _, _ = self.write_manifest(layout)
            with self.assertRaises(ManifestExportError):
                build_manifest_training_rows(
                    layout,
                    manifest_artifact_id,
                    split_name="held-out",
                )

    def test_training_export_fails_closed_on_unknown_transform(self):
        with tempfile.TemporaryDirectory() as tempdir:
            layout = StorageLayout.create(Path(tempdir) / "data")
            layout.ensure_directories()
            _, manifest_artifact_id, _, _, _ = self.write_manifest(
                layout,
                transform={"name": "future-adjudicated-target", "version": 2},
            )
            with self.assertRaises(ManifestExportError):
                build_manifest_training_rows(layout, manifest_artifact_id)

    def test_materialized_rows_remain_identical_after_moving_data_root(self):
        with tempfile.TemporaryDirectory() as tempdir:
            first_root = Path(tempdir) / "first"
            first_layout = StorageLayout.create(first_root)
            first_layout.ensure_directories()
            _, manifest_artifact_id, _, _, _ = self.write_manifest(first_layout)
            expected = build_manifest_training_rows(first_layout, manifest_artifact_id)

            moved_root = Path(tempdir) / "moved"
            shutil.copytree(first_root, moved_root)
            moved_layout = StorageLayout.create(moved_root)
            actual = build_manifest_training_rows(moved_layout, manifest_artifact_id)
            self.assertEqual(actual, expected)

            output = Path(tempdir) / "training.jsonl"
            write_manifest_training_jsonl(output, moved_layout, manifest_artifact_id)
            self.assertTrue(output.read_text(encoding="utf-8").endswith("\n"))


if __name__ == "__main__":
    unittest.main()
