import tempfile
import unittest
from pathlib import Path

from commander_gym.dataset_manifest import DatasetManifestStore
from commander_gym.deck_package import ArtifactRef
from commander_gym.evidence import RawEvidenceStore
from commander_gym.identity import IdentityRef, Pilot
from commander_gym.manifest_export import ManifestExportError, build_manifest_training_rows
from commander_gym.pilot import ArgentumActionChoice
from commander_gym.pilot_composition import (
    PILOT_ROUTING_MODE_STATIC_ORDER_V1,
    PILOT_ROUTING_PATH_COMPOSED_V1,
    PilotComponentRegistry,
    SubsystemDecision,
    compose_pilot_runtime,
)
from commander_gym.records import ActionRecord, DecisionRecord, PilotProvenance
from commander_gym.run_records import EngineProvenance, RunParticipant, RunRecord, RunTermination
from commander_gym.storage import StorageLayout
from commander_gym.teacher_dataset import (
    TEACHER_FROZEN_TEST_SPLIT,
    TeacherDatasetError,
    build_teacher_dataset_manifest,
)


class FixtureSubsystem:
    def __init__(self, name, version, *, choice=None, reason=None):
        self.name = name
        self.version = version
        self.choice = choice
        self.reason = reason

    def try_choose(self, observation):
        return SubsystemDecision(choice=self.choice, reason=self.reason)


class TeacherDatasetTests(unittest.TestCase):
    def setUp(self):
        self.skill_ref = ArtifactRef(
            kind="skill-set",
            artifact_id="fixture-skills",
            version="r1",
            digest="b" * 64,
        )
        self.frontier_ref = ArtifactRef(
            kind="provider",
            artifact_id="frontier-teacher",
            version="r7",
            digest="c" * 64,
        )
        self.pilot_identity = Pilot(
            pilot_id="fixture-composed-pilot",
            revision="2026-09-24.1",
            skill_set=self.skill_ref,
            escalation_provider=self.frontier_ref,
            routing_config={"mode": PILOT_ROUTING_MODE_STATIC_ORDER_V1},
        )

    def binding_ref(self):
        return IdentityRef("binding", "binding-public-1", "r1", "a" * 64)

    def pilot_provenance(self):
        return PilotProvenance(
            source="commander-gym",
            implementation="composed-pilot",
            version=self.pilot_identity.revision,
            model="replaceable-frontier-teacher",
        )

    def route(self, *, handled_by):
        if handled_by == "skill":
            skill = FixtureSubsystem(
                "fixture-skill-runtime",
                "r1",
                choice=ArgentumActionChoice(action_id=7),
            )
        else:
            skill = FixtureSubsystem(
                "fixture-skill-runtime",
                "r1",
                reason="no-matching-skill",
            )
        frontier = FixtureSubsystem(
            "fixture-frontier-runtime",
            "r7",
            choice=ArgentumActionChoice(action_id=7),
        )
        registry = PilotComponentRegistry(
            {
                PilotComponentRegistry.key_for(self.skill_ref): skill,
                PilotComponentRegistry.key_for(self.frontier_ref): frontier,
            }
        )
        choice = compose_pilot_runtime(self.pilot_identity, registry).choose(
            {"legalActions": [], "pendingDecision": None}
        )
        routing = choice.metadata["routing"]
        self.assertEqual(routing["path"], PILOT_ROUTING_PATH_COMPOSED_V1)
        return routing

    def make_record(self, *, game_id, decision_id, before, after, routing):
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
            pilot=self.pilot_provenance(),
            deck_id="deck-public-1",
            deck_version="r1",
            primer_version="knowledge-r1",
            binding=self.binding_ref(),
            outcome={"winner": 0, "result_observation": {"stateDigest": after}},
            metadata={
                "input_state_digest": before,
                "result_state_digest": after,
                "pilot_metadata": {
                    "routing": routing,
                    "provider": "fixture-provider",
                },
            },
        )

    def make_run(self, *, run_id, game_id, decision_ids, status="completed"):
        if status == "completed":
            termination = RunTermination(status="completed")
        else:
            termination = RunTermination(
                status=status,
                reason="fixture provider failure",
                failure_domain="provider",
            )
        return RunRecord(
            run_id=run_id,
            game_id=game_id,
            started_at="2026-09-24T09:00:00Z",
            finished_at="2026-09-24T09:00:05Z",
            engine=EngineProvenance(
                implementation="argentum",
                version="engine-build-123",
                schema="argentum-schema-v9",
                revision="engine-build-123",
            ),
            termination=termination,
            participants=[
                RunParticipant(
                    seat=0,
                    pilot=self.pilot_provenance(),
                    deck_id="deck-public-1",
                    deck_version="r1",
                    primer_version="knowledge-r1",
                    binding=self.binding_ref(),
                )
            ],
            decision_ids=list(decision_ids),
        )

    def write_evidence(self, layout, *, suffix, routes, status="completed"):
        game_id = f"game-{suffix}"
        run_id = f"run-{suffix}"
        records = []
        decision_ids = []
        for index, routing in enumerate(routes):
            decision_id = f"{run_id}:decision:{index}"
            decision_ids.append(decision_id)
            records.append(
                self.make_record(
                    game_id=game_id,
                    decision_id=decision_id,
                    before=f"before-{suffix}-{index}",
                    after=f"after-{suffix}-{index}",
                    routing=routing,
                )
            )
        result = RawEvidenceStore(layout).write(
            self.make_run(
                run_id=run_id,
                game_id=game_id,
                decision_ids=decision_ids,
                status=status,
            ),
            records,
            commander_gym_revision="teacher-dataset-fixture",
        )
        return result.artifact.artifact_id, decision_ids

    def test_frontier_teacher_decisions_use_existing_manifest_export_path(self):
        with tempfile.TemporaryDirectory() as tempdir:
            layout = StorageLayout.create(Path(tempdir) / "data")
            layout.ensure_directories()

            train_source, train_ids = self.write_evidence(
                layout,
                suffix="train",
                routes=[self.route(handled_by="skill"), self.route(handled_by="frontier")],
            )
            held_out_source, held_out_ids = self.write_evidence(
                layout,
                suffix="held-out",
                routes=[self.route(handled_by="frontier")],
            )
            skill_decision, teacher_decision = train_ids
            held_out_decision = held_out_ids[0]

            manifest = build_teacher_dataset_manifest(
                layout,
                dataset_id="frontier-teacher-imitation",
                version="r1",
                created_at="2026-09-24T09:05:00Z",
                generator_revision="teacher-selector-r1",
                evidence_artifact_ids=[held_out_source, train_source],
                frozen_test_decision_ids=[held_out_decision],
            )
            self.assertEqual(manifest.split("train").decision_ids, {teacher_decision})
            self.assertNotIn(skill_decision, manifest.split("train").decision_ids)
            self.assertEqual(
                manifest.split(TEACHER_FROZEN_TEST_SPLIT).decision_ids,
                {held_out_decision},
            )
            self.assertEqual(
                manifest.source_query["routing"]["handled_by_role"],
                "frontier_escalation",
            )

            written = DatasetManifestStore(layout).write(manifest)
            rows = build_manifest_training_rows(layout, written.artifact.artifact_id)
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["target"], {"chosen_action_id": "pass"})
            self.assertNotIn("routing", row["input"])
            self.assertNotIn("binding", row["input"])
            self.assertEqual(row["provenance"]["decision_id"], teacher_decision)
            self.assertEqual(
                row["provenance"]["binding"]["artifact_id"],
                "binding-public-1",
            )
            route = row["provenance"]["routing"]
            self.assertEqual(route["pilotId"], self.pilot_identity.pilot_id)
            self.assertEqual(route["pilotRevision"], self.pilot_identity.revision)
            self.assertEqual(route["handledBy"]["role"], "frontier_escalation")
            self.assertEqual(
                route["handledBy"]["component"]["artifactId"],
                self.frontier_ref.artifact_id,
            )
            self.assertEqual(
                route["handledBy"]["component"]["digest"],
                self.frontier_ref.digest,
            )
            self.assertEqual(
                row["provenance"]["dataset_manifest"]["source_evidence_artifact_id"],
                train_source,
            )

            with self.assertRaises(ManifestExportError):
                build_manifest_training_rows(
                    layout,
                    written.artifact.artifact_id,
                    split_name=TEACHER_FROZEN_TEST_SPLIT,
                )

    def test_diagnostic_teacher_decision_cannot_become_held_out_label(self):
        with tempfile.TemporaryDirectory() as tempdir:
            layout = StorageLayout.create(Path(tempdir) / "data")
            layout.ensure_directories()
            train_source, _ = self.write_evidence(
                layout,
                suffix="completed",
                routes=[self.route(handled_by="frontier")],
            )
            failed_source, failed_ids = self.write_evidence(
                layout,
                suffix="failed",
                routes=[self.route(handled_by="frontier")],
                status="failed",
            )
            with self.assertRaises(TeacherDatasetError):
                build_teacher_dataset_manifest(
                    layout,
                    dataset_id="frontier-teacher-imitation",
                    version="r1",
                    created_at="2026-09-24T09:05:00Z",
                    generator_revision="teacher-selector-r1",
                    evidence_artifact_ids=[train_source, failed_source],
                    frozen_test_decision_ids=failed_ids,
                )


if __name__ == "__main__":
    unittest.main()
