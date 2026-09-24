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
from commander_gym.model_lineage import (
    DatasetLineageRef,
    ModelArtifactRef,
    ModelLineageError,
    ModelLineageRecord,
    ModelLineageStore,
)
from commander_gym.records import ActionRecord, DecisionRecord, PilotProvenance
from commander_gym.run_records import EngineProvenance, RunParticipant, RunRecord, RunTermination
from commander_gym.storage import LocalArtifactStore, StorageLayout


class ModelLineageTests(unittest.TestCase):
    def binding_ref(self):
        return IdentityRef("binding", "binding-public-1", "r1", "a" * 64)

    def pilot(self):
        return PilotProvenance(
            source="commander-gym",
            implementation="routing-pilot",
            version="v4",
            model="fixture-model",
        )

    def write_evidence(self, layout):
        decision_id = "run-model-lineage:decision:0"
        record = DecisionRecord(
            game_id="game-model-lineage",
            decision_id=decision_id,
            decision_type="PassPriority",
            seat=0,
            observation_schema="argentum-schema-v9",
            observation={"schemaHash": "argentum-schema-v9", "stateDigest": "before"},
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
            outcome={"result_observation": {"stateDigest": "after"}},
            metadata={
                "input_state_digest": "before",
                "result_state_digest": "after",
                "pilot_metadata": {
                    "routing": {"path": "strategic"},
                    "provider": "fixture-provider",
                    "modelRevision": "model-r3",
                },
            },
        )
        run = RunRecord(
            run_id="run-model-lineage",
            game_id="game-model-lineage",
            started_at="2026-09-24T03:30:00Z",
            finished_at="2026-09-24T03:30:05Z",
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
        result = RawEvidenceStore(layout).write(
            run,
            [record],
            commander_gym_revision="cg-revision-model-lineage",
        )
        return result.artifact.artifact_id, decision_id

    def write_dataset(self, layout):
        evidence_artifact_id, decision_id = self.write_evidence(layout)
        manifest = DatasetManifest(
            dataset_id="imitation-fixture",
            version="r1",
            purpose="model lineage fixture",
            created_at="2026-09-24T03:31:00Z",
            source_population="qualified binding-v1 evidence",
            source_query={"qualification": "completed"},
            selection_rules={"decision_types": ["PassPriority"]},
            exclusion_rules={"technical_failures": True},
            required_annotation_artifact_ids=(),
            transformations=(),
            splits=(
                DatasetSplit(
                    name="train",
                    role="train",
                    selections=(
                        DatasetEvidenceSelection(evidence_artifact_id, (decision_id,)),
                    ),
                ),
            ),
            generator_revision="dataset-generator-r1",
        )
        result = DatasetManifestStore(layout).write(manifest)
        return manifest, result.artifact.artifact_id, evidence_artifact_id

    def record(self, *, manifest, manifest_artifact_id, model_artifact_id, version="r1"):
        return ModelLineageRecord(
            model_id="local-generalist-fixture",
            version=version,
            created_at="2026-09-24T03:32:00Z",
            producer_revision="trainer-r7",
            datasets=(
                DatasetLineageRef(
                    manifest_artifact_id=manifest_artifact_id,
                    dataset_id=manifest.dataset_id,
                    version=manifest.version,
                    dataset_digest=manifest.dataset_digest,
                ),
            ),
            artifacts=(ModelArtifactRef(role="checkpoint", artifact_id=model_artifact_id),),
        )

    def test_model_lineage_reaches_source_evidence_and_survives_data_root_move(self):
        with tempfile.TemporaryDirectory() as tempdir:
            first_root = Path(tempdir) / "first"
            layout = StorageLayout.create(first_root)
            layout.ensure_directories()
            manifest, manifest_artifact_id, evidence_artifact_id = self.write_dataset(layout)
            model_artifact = LocalArtifactStore(layout).put_bytes(b"fixture checkpoint bytes")
            record = self.record(
                manifest=manifest,
                manifest_artifact_id=manifest_artifact_id,
                model_artifact_id=model_artifact.artifact_id,
            )
            store = ModelLineageStore(layout)
            written = store.write(record)
            self.assertEqual(written.lineage_digest, record.lineage_digest)
            self.assertEqual(written.source_evidence_artifact_ids, (evidence_artifact_id,))

            moved_root = Path(tempdir) / "moved"
            shutil.copytree(first_root, moved_root)
            moved_store = ModelLineageStore(StorageLayout.create(moved_root))
            recovered = moved_store.read(model_id=record.model_id, version=record.version)
            self.assertEqual(recovered.lineage_digest, record.lineage_digest)
            self.assertEqual(
                moved_store.source_evidence_artifact_ids(recovered),
                (evidence_artifact_id,),
            )

    def test_stale_dataset_digest_fails_closed(self):
        with tempfile.TemporaryDirectory() as tempdir:
            layout = StorageLayout.create(Path(tempdir) / "data")
            layout.ensure_directories()
            manifest, manifest_artifact_id, _ = self.write_dataset(layout)
            model_artifact = LocalArtifactStore(layout).put_bytes(b"fixture checkpoint bytes")
            record = self.record(
                manifest=manifest,
                manifest_artifact_id=manifest_artifact_id,
                model_artifact_id=model_artifact.artifact_id,
            )
            stale = ModelLineageRecord(
                **{
                    **record.__dict__,
                    "datasets": (
                        DatasetLineageRef(
                            manifest_artifact_id=manifest_artifact_id,
                            dataset_id=manifest.dataset_id,
                            version=manifest.version,
                            dataset_digest="sha256:" + "0" * 64,
                        ),
                    ),
                }
            )
            with self.assertRaises(ModelLineageError):
                ModelLineageStore(layout).write(stale)

    def test_missing_model_artifact_fails_closed(self):
        with tempfile.TemporaryDirectory() as tempdir:
            layout = StorageLayout.create(Path(tempdir) / "data")
            layout.ensure_directories()
            manifest, manifest_artifact_id, _ = self.write_dataset(layout)
            record = self.record(
                manifest=manifest,
                manifest_artifact_id=manifest_artifact_id,
                model_artifact_id="sha256:" + "0" * 64,
            )
            with self.assertRaises(ModelLineageError):
                ModelLineageStore(layout).write(record)

    def test_model_id_version_is_immutable(self):
        with tempfile.TemporaryDirectory() as tempdir:
            layout = StorageLayout.create(Path(tempdir) / "data")
            layout.ensure_directories()
            manifest, manifest_artifact_id, _ = self.write_dataset(layout)
            model_artifact = LocalArtifactStore(layout).put_bytes(b"fixture checkpoint bytes")
            record = self.record(
                manifest=manifest,
                manifest_artifact_id=manifest_artifact_id,
                model_artifact_id=model_artifact.artifact_id,
            )
            store = ModelLineageStore(layout)
            store.write(record)
            changed = ModelLineageRecord(
                **{
                    **record.__dict__,
                    "producer_revision": "different-trainer-revision",
                }
            )
            with self.assertRaises(ModelLineageError):
                store.write(changed)


if __name__ == "__main__":
    unittest.main()
