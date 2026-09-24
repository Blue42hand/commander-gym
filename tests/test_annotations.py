import shutil
import tempfile
import unittest
from pathlib import Path

from commander_gym.annotations import (
    ANNOTATION_TARGET_DECISION,
    AnnotationError,
    AnnotationRecord,
    AnnotationStore,
)
from commander_gym.evidence import RawEvidenceStore
from commander_gym.identity import IdentityRef
from commander_gym.records import ActionRecord, DecisionRecord, PilotProvenance
from commander_gym.run_records import EngineProvenance, RunParticipant, RunRecord, RunTermination
from commander_gym.storage import StorageLayout


class AnnotationTests(unittest.TestCase):
    def binding_ref(self):
        return IdentityRef("binding", "binding-public-1", "r1", "a" * 64)

    def make_record(self):
        return DecisionRecord(
            game_id="game-1",
            decision_id="run-1:decision:0",
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
            pilot=PilotProvenance(
                source="commander-gym",
                implementation="routing-pilot",
                version="v4",
                model="fixture-model",
            ),
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

    def make_run(self):
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
                    binding=self.binding_ref(),
                )
            ],
            decision_ids=["run-1:decision:0"],
        )

    def write_raw_evidence(self, layout):
        result = RawEvidenceStore(layout).write(
            self.make_run(),
            [self.make_record()],
            commander_gym_revision="cg-revision-123",
        )
        return result.artifact.artifact_id

    def annotation(self, source_artifact_id, *, revision="r1", payload=None, supersedes=None):
        return AnnotationRecord(
            annotation_id="teacher-review-1",
            revision=revision,
            created_at="2026-09-24T00:30:00Z",
            source_evidence_artifact_id=source_artifact_id,
            target_kind=ANNOTATION_TARGET_DECISION,
            target_id="run-1:decision:0",
            annotation_type="teacher-adjudication",
            annotator={
                "source": "teacher-model",
                "provider": "fixture-provider",
                "model_revision": "teacher-r7",
            },
            payload=payload or {
                "decision_class": "priority",
                "preferred": True,
                "material_error": False,
            },
            supersedes_artifact_id=supersedes,
        )

    def test_annotation_is_joined_to_exact_raw_evidence_and_path_portable(self):
        with tempfile.TemporaryDirectory() as tempdir:
            first_root = Path(tempdir) / "first"
            layout = StorageLayout.create(first_root)
            layout.ensure_directories()
            source_id = self.write_raw_evidence(layout)
            raw_before = layout.path("artifacts")

            store = AnnotationStore(layout)
            written = store.write(self.annotation(source_id))
            recovered = store.read(
                target_kind=ANNOTATION_TARGET_DECISION,
                target_id="run-1:decision:0",
                annotation_id="teacher-review-1",
                revision="r1",
            )
            self.assertEqual(recovered.source_evidence_artifact_id, source_id)
            self.assertEqual(recovered.payload["decision_class"], "priority")
            self.assertNotEqual(written.artifact.artifact_id, source_id)
            self.assertTrue(raw_before.exists())

            moved_root = Path(tempdir) / "moved"
            shutil.copytree(first_root, moved_root)
            moved = AnnotationStore(StorageLayout.create(moved_root)).read(
                target_kind=ANNOTATION_TARGET_DECISION,
                target_id="run-1:decision:0",
                annotation_id="teacher-review-1",
                revision="r1",
            )
            self.assertEqual(moved.source_evidence_artifact_id, source_id)

    def test_new_revision_appends_and_preserves_previous_annotation(self):
        with tempfile.TemporaryDirectory() as tempdir:
            layout = StorageLayout.create(Path(tempdir) / "data")
            layout.ensure_directories()
            source_id = self.write_raw_evidence(layout)
            store = AnnotationStore(layout)

            first = store.write(self.annotation(source_id))
            second = store.write(
                self.annotation(
                    source_id,
                    revision="r2",
                    payload={
                        "decision_class": "priority",
                        "preferred": False,
                        "material_error": True,
                    },
                    supersedes=first.artifact.artifact_id,
                )
            )

            self.assertNotEqual(first.artifact.artifact_id, second.artifact.artifact_id)
            old = store.read_artifact(first.artifact.artifact_id)
            new = store.read_artifact(second.artifact.artifact_id)
            self.assertFalse(old.payload["material_error"])
            self.assertTrue(new.payload["material_error"])
            self.assertEqual(new.supersedes_artifact_id, first.artifact.artifact_id)

    def test_same_annotation_revision_cannot_be_rewritten(self):
        with tempfile.TemporaryDirectory() as tempdir:
            layout = StorageLayout.create(Path(tempdir) / "data")
            layout.ensure_directories()
            source_id = self.write_raw_evidence(layout)
            store = AnnotationStore(layout)
            store.write(self.annotation(source_id))

            with self.assertRaises(AnnotationError):
                store.write(
                    self.annotation(
                        source_id,
                        payload={"decision_class": "priority", "material_error": True},
                    )
                )

    def test_unknown_decision_target_and_cross_evidence_supersede_fail_closed(self):
        with tempfile.TemporaryDirectory() as tempdir:
            layout = StorageLayout.create(Path(tempdir) / "data")
            layout.ensure_directories()
            source_id = self.write_raw_evidence(layout)
            store = AnnotationStore(layout)
            first = store.write(self.annotation(source_id))

            unknown = AnnotationRecord(
                annotation_id="bad-target",
                revision="r1",
                created_at="2026-09-24T00:30:00Z",
                source_evidence_artifact_id=source_id,
                target_kind=ANNOTATION_TARGET_DECISION,
                target_id="missing-decision",
                annotation_type="teacher-adjudication",
                annotator={"source": "teacher-model"},
                payload={},
            )
            with self.assertRaises(AnnotationError):
                store.write(unknown)

            other_source = store.artifacts.put_bytes(b"not raw evidence").artifact_id
            replacement = self.annotation(
                other_source,
                revision="r2",
                supersedes=first.artifact.artifact_id,
            )
            with self.assertRaises(AnnotationError):
                store.write(replacement)


if __name__ == "__main__":
    unittest.main()
