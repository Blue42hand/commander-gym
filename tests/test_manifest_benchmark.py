import tempfile
import unittest
from pathlib import Path

from commander_gym.benchmark_runner import BenchmarkInput, CallablePilot
from commander_gym.dataset_manifest import (
    DatasetEvidenceSelection,
    DatasetManifest,
    DatasetManifestStore,
    DatasetSplit,
)
from commander_gym.deck_package import ArtifactRef
from commander_gym.evidence import RawEvidenceStore
from commander_gym.identity import IdentityRef
from commander_gym.manifest_benchmark import (
    BenchmarkSubjectIdentity,
    ManifestBenchmarkError,
    build_manifest_benchmark_cases,
    build_manifest_benchmark_evaluation,
    write_manifest_benchmark_evaluation,
)
from commander_gym.records import ActionRecord, DecisionRecord, PilotProvenance
from commander_gym.run_records import EngineProvenance, RunParticipant, RunRecord, RunTermination
from commander_gym.storage import LocalArtifactStore, StorageLayout


class ManifestBenchmarkTests(unittest.TestCase):
    def binding_ref(self):
        return IdentityRef("binding", "benchmark-binding", "r1", "a" * 64)

    def pilot_provenance(self):
        return PilotProvenance(
            source="commander-gym",
            implementation="fixture-composed-pilot",
            version="pilot-r1",
            model="fixture-teacher",
        )

    def make_record(self, *, decision_id, turn, chosen_action_id):
        return DecisionRecord(
            game_id="benchmark-game",
            decision_id=decision_id,
            decision_type="priority",
            seat=0,
            observation_schema="benchmark-seat-safe-v1",
            observation={
                "public": {"turn": turn},
                "private": {"hand_size": 4},
            },
            legal_actions=[
                ActionRecord(action_id="pass", payload={"kind": "pass"}),
                ActionRecord(
                    action_id="cast",
                    payload={"kind": "cast", "card": "Synthetic Spell"},
                ),
            ],
            chosen_action_id=chosen_action_id,
            pilot=self.pilot_provenance(),
            deck_id="benchmark-deck",
            deck_version="r1",
            primer_version="knowledge-r1",
            binding=self.binding_ref(),
            outcome={"winner": 0, "post_choice_only": True},
            metadata={
                "input_state_digest": f"before-{decision_id}",
                "result_state_digest": f"after-{decision_id}",
                "pilot_metadata": {
                    "routing": {
                        "path": "composed",
                        "handledBy": {
                            "role": "frontier_escalation",
                            "component": {
                                "kind": "provider",
                                "artifactId": "fixture-teacher-provider",
                                "version": "r7",
                                "digest": "b" * 64,
                            },
                        },
                    },
                    "private_diagnostic": "must-not-enter-benchmark-input",
                },
            },
        )

    def write_frozen_manifest(self, layout):
        records = [
            self.make_record(
                decision_id="benchmark-game:decision:0",
                turn=1,
                chosen_action_id="cast",
            ),
            self.make_record(
                decision_id="benchmark-game:decision:1",
                turn=2,
                chosen_action_id="pass",
            ),
        ]
        run = RunRecord(
            run_id="benchmark-run",
            game_id="benchmark-game",
            started_at="2026-09-24T12:30:00Z",
            finished_at="2026-09-24T12:30:05Z",
            engine=EngineProvenance(
                implementation="argentum",
                version="fixture-engine",
                schema="benchmark-seat-safe-v1",
                revision="fixture-engine-r1",
            ),
            termination=RunTermination(status="completed"),
            participants=[
                RunParticipant(
                    seat=0,
                    pilot=self.pilot_provenance(),
                    deck_id="benchmark-deck",
                    deck_version="r1",
                    primer_version="knowledge-r1",
                    binding=self.binding_ref(),
                )
            ],
            decision_ids=[record.decision_id for record in records],
        )
        evidence = RawEvidenceStore(layout).write(
            run,
            records,
            commander_gym_revision="manifest-benchmark-fixture",
        )
        evidence_id = evidence.artifact.artifact_id
        manifest = DatasetManifest(
            dataset_id="public-frozen-benchmark",
            version="r1",
            purpose="model-independent frozen decision evaluation",
            created_at="2026-09-24T12:31:00Z",
            source_population="immutable binding-v1 raw evidence",
            source_query={"fixture": True},
            selection_rules={"completed_only": True},
            exclusion_rules={"diagnostic_only": True},
            required_annotation_artifact_ids=(),
            transformations=(),
            splits=(
                DatasetSplit(
                    name="held-out",
                    role="frozen_test",
                    selections=(
                        DatasetEvidenceSelection(
                            evidence_artifact_id=evidence_id,
                            decision_ids=tuple(record.decision_id for record in records),
                        ),
                    ),
                ),
            ),
            generator_revision="manifest-benchmark-fixture",
        )
        written = DatasetManifestStore(layout).write(manifest)
        return written.artifact.artifact_id, records

    def candidate_identity(self):
        return BenchmarkSubjectIdentity(
            role="candidate",
            runtime_name="candidate-runtime",
            runtime_version="r1",
            component=ArtifactRef(
                kind="learned-policy",
                artifact_id="candidate-policy",
                version="r1",
                digest="c" * 64,
            ),
            model=ArtifactRef(
                kind="model",
                artifact_id="candidate-local-model",
                version="checkpoint-7",
                digest="d" * 64,
            ),
            provider="local-fixture",
        )

    def teacher_identity(self):
        return BenchmarkSubjectIdentity(
            role="teacher",
            runtime_name="teacher-runtime",
            runtime_version="r7",
            component=ArtifactRef(
                kind="provider",
                artifact_id="teacher-provider",
                version="r7",
                digest="e" * 64,
            ),
            model=ArtifactRef(
                kind="model",
                artifact_id="teacher-model",
                version="snapshot-2026-09-24",
                digest="f" * 64,
            ),
            provider="frontier-fixture",
        )

    def test_candidate_and_teacher_share_identical_seat_safe_frozen_inputs(self):
        with tempfile.TemporaryDirectory() as tempdir:
            layout = StorageLayout.create(Path(tempdir) / "data")
            layout.ensure_directories()
            manifest_id, _ = self.write_frozen_manifest(layout)
            candidate_inputs = []
            teacher_inputs = []

            def candidate_choose(value: BenchmarkInput):
                candidate_inputs.append(value)
                return "pass"

            def teacher_choose(value: BenchmarkInput):
                teacher_inputs.append(value)
                return "cast" if value.observation["public"]["turn"] == 1 else "pass"

            report = build_manifest_benchmark_evaluation(
                layout,
                manifest_id,
                split_name="held-out",
                candidate_pilot=CallablePilot("candidate-runtime", "r1", candidate_choose),
                candidate_identity=self.candidate_identity(),
                teacher_pilot=CallablePilot("teacher-runtime", "r7", teacher_choose),
                teacher_identity=self.teacher_identity(),
            )

            self.assertEqual(candidate_inputs, teacher_inputs)
            self.assertEqual(len(candidate_inputs), 2)
            self.assertEqual(
                set(candidate_inputs[0].__dataclass_fields__),
                {"decision_type", "seat", "observation_schema", "observation", "legal_actions"},
            )
            for forbidden in (
                "judgment",
                "chosen_action_id",
                "outcome",
                "binding",
                "pilot",
                "metadata",
                "dataset_manifest",
            ):
                self.assertFalse(hasattr(candidate_inputs[0], forbidden), forbidden)

            self.assertEqual(report["dataset_manifest"]["split_role"], "frozen_test")
            self.assertEqual(report["candidate"]["identity"]["component"]["digest"], "c" * 64)
            self.assertEqual(report["candidate"]["identity"]["model"]["digest"], "d" * 64)
            self.assertEqual(report["teacher"]["identity"]["component"]["digest"], "e" * 64)
            self.assertEqual(report["teacher"]["identity"]["model"]["digest"], "f" * 64)
            self.assertEqual(report["teacher"]["result"]["summary"]["preferred_rate"], 1.0)
            self.assertEqual(report["candidate"]["result"]["summary"]["preferred_rate"], 0.5)
            self.assertEqual(
                report["comparison"]["summary"]["preferred_rate"]["candidate"],
                0.5,
            )
            self.assertNotIn("telemetry", report["comparison"])
            self.assertNotIn("mean_latency_ms", report["candidate"]["result"]["summary"])

    def test_evaluation_artifact_is_deterministic_and_content_addressed(self):
        with tempfile.TemporaryDirectory() as tempdir:
            layout = StorageLayout.create(Path(tempdir) / "data")
            layout.ensure_directories()
            manifest_id, _ = self.write_frozen_manifest(layout)
            candidate = CallablePilot("candidate-runtime", "r1", lambda _: "pass")
            teacher = CallablePilot(
                "teacher-runtime",
                "r7",
                lambda value: "cast" if value.observation["public"]["turn"] == 1 else "pass",
            )

            first = write_manifest_benchmark_evaluation(
                layout,
                manifest_id,
                split_name="held-out",
                candidate_pilot=candidate,
                candidate_identity=self.candidate_identity(),
                teacher_pilot=teacher,
                teacher_identity=self.teacher_identity(),
            )
            second = write_manifest_benchmark_evaluation(
                layout,
                manifest_id,
                split_name="held-out",
                candidate_pilot=candidate,
                candidate_identity=self.candidate_identity(),
                teacher_pilot=teacher,
                teacher_identity=self.teacher_identity(),
            )

            self.assertEqual(first.report, second.report)
            self.assertEqual(first.artifact.artifact_id, second.artifact.artifact_id)
            stored = LocalArtifactStore(layout).read_bytes(first.artifact.artifact_id)
            self.assertEqual(first.artifact.size_bytes, len(stored))

    def test_cases_retain_reference_target_outside_model_input(self):
        with tempfile.TemporaryDirectory() as tempdir:
            layout = StorageLayout.create(Path(tempdir) / "data")
            layout.ensure_directories()
            manifest_id, records = self.write_frozen_manifest(layout)
            cases = build_manifest_benchmark_cases(
                layout,
                manifest_id,
                split_name="held-out",
            )

            self.assertEqual(
                [case.judgment.preferred_action_ids for case in cases],
                [[record.chosen_action_id] for record in records],
            )
            self.assertTrue(all(case.judgment.provenance["source"] == "frozen_raw_target" for case in cases))
            self.assertTrue(all(case.held_out for case in cases))

    def test_runtime_identity_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as tempdir:
            layout = StorageLayout.create(Path(tempdir) / "data")
            layout.ensure_directories()
            manifest_id, _ = self.write_frozen_manifest(layout)

            with self.assertRaises(ManifestBenchmarkError):
                build_manifest_benchmark_evaluation(
                    layout,
                    manifest_id,
                    split_name="held-out",
                    candidate_pilot=CallablePilot("wrong-runtime", "r1", lambda _: "pass"),
                    candidate_identity=self.candidate_identity(),
                    teacher_pilot=CallablePilot("teacher-runtime", "r7", lambda _: "pass"),
                    teacher_identity=self.teacher_identity(),
                )


if __name__ == "__main__":
    unittest.main()
