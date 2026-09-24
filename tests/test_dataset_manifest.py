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
    validate_export_selection,
)
from commander_gym.evidence import RawEvidenceStore
from commander_gym.identity import IdentityRef
from commander_gym.records import ActionRecord, DecisionRecord, PilotProvenance
from commander_gym.run_records import EngineProvenance, RunParticipant, RunRecord, RunTermination
from commander_gym.storage import StorageLayout


class DatasetManifestTests(unittest.TestCase):
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
            outcome={"result_observation": {"stateDigest": after}},
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

    def make_run(self, *, run_id, game_id, decision_id, status="completed"):
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
            termination=RunTermination(
                status=status,
                reason="provider-failure" if status == "failed" else None,
                failure_domain="provider" if status == "failed" else None,
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
            decision_ids=[decision_id],
        )

    def write_evidence(self, layout, *, suffix, status="completed"):
        game_id = f"game-{suffix}"
        run_id = f"run-{suffix}"
        decision_id = f"{run_id}:decision:0"
        result = RawEvidenceStore(layout).write(
            self.make_run(
                run_id=run_id,
                game_id=game_id,
                decision_id=decision_id,
                status=status,
            ),
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

    def manifest(self, *, train_source, train_decision, test_source, test_decision, annotations=()):
        return DatasetManifest(
            dataset_id="pilot-imitation-v1",
            version="r1",
            purpose="teacher imitation fixture",
            created_at="2026-09-24T01:30:00Z",
            source_population="qualified binding-v1 raw evidence",
            source_query={"qualification": "completed", "identity_epoch": "binding-v1"},
            selection_rules={"decision_types": ["PassPriority"]},
            exclusion_rules={"technical_failures": True},
            required_annotation_artifact_ids=tuple(annotations),
            transformations=({"name": "input-target-provenance", "version": 1},),
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

    def test_manifest_digest_is_deterministic_and_path_portable(self):
        with tempfile.TemporaryDirectory() as tempdir:
            first_root = Path(tempdir) / "first"
            layout = StorageLayout.create(first_root)
            layout.ensure_directories()
            train_source, train_decision = self.write_evidence(layout, suffix="train")
            test_source, test_decision = self.write_evidence(layout, suffix="test")
            manifest = self.manifest(
                train_source=train_source,
                train_decision=train_decision,
                test_source=test_source,
                test_decision=test_decision,
            )
            reversed_manifest = DatasetManifest(
                **{
                    **manifest.__dict__,
                    "splits": tuple(reversed(manifest.splits)),
                }
            )
            self.assertEqual(manifest.dataset_digest, reversed_manifest.dataset_digest)

            written = DatasetManifestStore(layout).write(manifest)
            self.assertEqual(written.dataset_digest, manifest.dataset_digest)

            moved_root = Path(tempdir) / "moved"
            shutil.copytree(first_root, moved_root)
            recovered = DatasetManifestStore(StorageLayout.create(moved_root)).read(
                dataset_id=manifest.dataset_id,
                version=manifest.version,
            )
            self.assertEqual(recovered.dataset_digest, manifest.dataset_digest)
            self.assertEqual(
                recovered.split("held-out").decision_ids,
                frozenset({test_decision}),
            )

    def test_frozen_test_membership_cannot_overlap_or_escape_export_split(self):
        with tempfile.TemporaryDirectory() as tempdir:
            layout = StorageLayout.create(Path(tempdir) / "data")
            layout.ensure_directories()
            train_source, train_decision = self.write_evidence(layout, suffix="train")
            test_source, test_decision = self.write_evidence(layout, suffix="test")
            manifest = self.manifest(
                train_source=train_source,
                train_decision=train_decision,
                test_source=test_source,
                test_decision=test_decision,
            )
            validate_export_selection(manifest, "train", [train_decision])
            with self.assertRaises(DatasetManifestError):
                validate_export_selection(manifest, "train", [train_decision, test_decision])

            overlapping = DatasetManifest(
                **{
                    **manifest.__dict__,
                    "splits": (
                        manifest.split("train"),
                        DatasetSplit(
                            name="held-out",
                            role="frozen_test",
                            selections=(
                                DatasetEvidenceSelection(test_source, (train_decision,)),
                            ),
                        ),
                    ),
                }
            )
            with self.assertRaises(DatasetManifestError):
                overlapping.validate()

    def test_required_annotation_must_join_selected_evidence(self):
        with tempfile.TemporaryDirectory() as tempdir:
            layout = StorageLayout.create(Path(tempdir) / "data")
            layout.ensure_directories()
            train_source, train_decision = self.write_evidence(layout, suffix="train")
            test_source, test_decision = self.write_evidence(layout, suffix="test")
            unused_source, unused_decision = self.write_evidence(layout, suffix="unused")
            annotation = AnnotationStore(layout).write(
                AnnotationRecord(
                    annotation_id="teacher-review-unused",
                    revision="r1",
                    created_at="2026-09-24T01:30:00Z",
                    source_evidence_artifact_id=unused_source,
                    target_kind=ANNOTATION_TARGET_DECISION,
                    target_id=unused_decision,
                    annotation_type="teacher-adjudication",
                    annotator={"source": "teacher-model"},
                    payload={"preferred": True},
                )
            )
            manifest = self.manifest(
                train_source=train_source,
                train_decision=train_decision,
                test_source=test_source,
                test_decision=test_decision,
                annotations=(annotation.artifact.artifact_id,),
            )
            with self.assertRaises(DatasetManifestError):
                DatasetManifestStore(layout).write(manifest)

    def test_diagnostic_evidence_requires_explicit_safe_subset_justification(self):
        with tempfile.TemporaryDirectory() as tempdir:
            layout = StorageLayout.create(Path(tempdir) / "data")
            layout.ensure_directories()
            failed_source, failed_decision = self.write_evidence(
                layout,
                suffix="failed",
                status="failed",
            )
            test_source, test_decision = self.write_evidence(layout, suffix="test")
            manifest = self.manifest(
                train_source=failed_source,
                train_decision=failed_decision,
                test_source=test_source,
                test_decision=test_decision,
            )
            with self.assertRaises(DatasetManifestError):
                DatasetManifestStore(layout).write(manifest)

            allowed_train = DatasetSplit(
                name="train",
                role="train",
                selections=(
                    DatasetEvidenceSelection(failed_source, (failed_decision,)),
                ),
                allow_diagnostic_evidence=True,
                diagnostic_justification="use only pre-failure decision as a routing fixture",
            )
            allowed = DatasetManifest(
                **{
                    **manifest.__dict__,
                    "version": "r2",
                    "splits": (allowed_train, manifest.split("held-out")),
                }
            )
            DatasetManifestStore(layout).write(allowed)

    def test_dataset_id_version_is_immutable(self):
        with tempfile.TemporaryDirectory() as tempdir:
            layout = StorageLayout.create(Path(tempdir) / "data")
            layout.ensure_directories()
            train_source, train_decision = self.write_evidence(layout, suffix="train")
            test_source, test_decision = self.write_evidence(layout, suffix="test")
            manifest = self.manifest(
                train_source=train_source,
                train_decision=train_decision,
                test_source=test_source,
                test_decision=test_decision,
            )
            store = DatasetManifestStore(layout)
            store.write(manifest)
            changed = DatasetManifest(
                **{
                    **manifest.__dict__,
                    "purpose": "different purpose under same dataset id/version",
                }
            )
            with self.assertRaises(DatasetManifestError):
                store.write(changed)


if __name__ == "__main__":
    unittest.main()
