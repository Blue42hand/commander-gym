import shutil
import tempfile
import unittest
from pathlib import Path

from commander_gym.candidate_promotion import (
    CandidateArtifactRecord,
    CandidatePromotionError,
    CandidatePromotionStore,
    CandidateQualificationRecord,
    DatasetGateRef,
    QualificationStage,
)
from commander_gym.dataset_manifest import (
    DatasetEvidenceSelection,
    DatasetManifest,
    DatasetManifestStore,
    DatasetSplit,
)
from commander_gym.evidence import RawEvidenceStore
from commander_gym.identity import IdentityRef
from commander_gym.records import ActionRecord, DecisionRecord, PilotProvenance
from commander_gym.run_records import EngineProvenance, RunParticipant, RunRecord, RunTermination
from commander_gym.storage import LocalArtifactStore, StorageLayout


class CandidatePromotionTests(unittest.TestCase):
    def binding_ref(self):
        return IdentityRef("binding", "binding-promotion-1", "r1", "a" * 64)

    def pilot(self):
        return PilotProvenance(
            source="commander-gym",
            implementation="composed-pilot",
            version="pilot-r1",
            model="fixture-model",
        )

    def write_evidence(self, layout):
        decision_ids = (
            "run-promotion:decision:replay",
            "run-promotion:decision:heldout",
        )
        decisions = []
        for index, decision_id in enumerate(decision_ids):
            decisions.append(
                DecisionRecord(
                    game_id="game-promotion",
                    decision_id=decision_id,
                    decision_type="PassPriority",
                    seat=0,
                    observation_schema="argentum-schema-v9",
                    observation={
                        "schemaHash": "argentum-schema-v9",
                        "stateDigest": f"before-{index}",
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
                    deck_id="deck-promotion-1",
                    deck_version="r1",
                    primer_version="knowledge-r1",
                    binding=self.binding_ref(),
                    outcome={"result_observation": {"stateDigest": f"after-{index}"}},
                    metadata={
                        "input_state_digest": f"before-{index}",
                        "result_state_digest": f"after-{index}",
                        "pilot_metadata": {
                            "routing": {"path": "composed"},
                            "provider": "fixture-provider",
                            "modelRevision": "model-r1",
                        },
                    },
                )
            )

        run = RunRecord(
            run_id="run-promotion",
            game_id="game-promotion",
            started_at="2026-09-24T10:15:00Z",
            finished_at="2026-09-24T10:15:05Z",
            engine=EngineProvenance(
                implementation="argentum",
                version="engine-build-1",
                schema="argentum-schema-v9",
                revision="engine-build-1",
            ),
            termination=RunTermination(status="completed"),
            participants=[
                RunParticipant(
                    seat=0,
                    pilot=self.pilot(),
                    deck_id="deck-promotion-1",
                    deck_version="r1",
                    primer_version="knowledge-r1",
                    binding=self.binding_ref(),
                )
            ],
            decision_ids=list(decision_ids),
        )
        written = RawEvidenceStore(layout).write(
            run,
            decisions,
            commander_gym_revision="cg-promotion-fixture",
        )
        return written.artifact.artifact_id, decision_ids

    def write_gate_dataset(self, layout, evidence_artifact_id, decision_ids):
        manifest = DatasetManifest(
            dataset_id="candidate-qualification-fixture",
            version="r1",
            purpose="candidate replay and frozen held-out qualification",
            created_at="2026-09-24T10:16:00Z",
            source_population="qualified binding-v1 evidence",
            source_query={"qualification": "completed"},
            selection_rules={"fixture": True},
            exclusion_rules={"diagnostic_only": True},
            required_annotation_artifact_ids=(),
            transformations=(),
            splits=(
                DatasetSplit(
                    name="replay-validation",
                    role="validation",
                    selections=(
                        DatasetEvidenceSelection(
                            evidence_artifact_id,
                            (decision_ids[0],),
                        ),
                    ),
                ),
                DatasetSplit(
                    name="frozen-benchmark",
                    role="frozen_test",
                    selections=(
                        DatasetEvidenceSelection(
                            evidence_artifact_id,
                            (decision_ids[1],),
                        ),
                    ),
                ),
            ),
            generator_revision="candidate-gate-fixture-r1",
        )
        result = DatasetManifestStore(layout).write(manifest)
        return manifest, result.artifact.artifact_id

    def write_candidate(self, layout, *, payload=b"candidate skill payload", version="r1"):
        artifacts = LocalArtifactStore(layout)
        payload_artifact = artifacts.put_bytes(payload)
        record = CandidateArtifactRecord(
            candidate_id="combat-skill-candidate",
            version=version,
            candidate_type="skill",
            component_kind="skill-set",
            created_at="2026-09-24T10:17:00Z",
            producer_revision="skill-producer-r3",
            payload_artifact_id=payload_artifact.artifact_id,
            metadata={"decision_family": "combat"},
        )
        written = CandidatePromotionStore(layout).write_candidate(record)
        return record, written

    def qualification(
        self,
        layout,
        *,
        candidate,
        candidate_record_artifact_id,
        manifest,
        manifest_artifact_id,
        evidence_artifact_id,
        shadow_passed=True,
        heldout_split="frozen-benchmark",
        revision="r1",
    ):
        artifacts = LocalArtifactStore(layout)
        replay_result = artifacts.put_bytes(b'{"stage":"replay","passed":true}')
        heldout_result = artifacts.put_bytes(b'{"stage":"heldout","passed":true}')
        shadow_result = artifacts.put_bytes(
            b'{"stage":"shadow","passed":true}'
            if shadow_passed
            else b'{"stage":"shadow","passed":false}'
        )
        replay_ref = DatasetGateRef(
            manifest_artifact_id=manifest_artifact_id,
            dataset_id=manifest.dataset_id,
            version=manifest.version,
            dataset_digest=manifest.dataset_digest,
            split_name="replay-validation",
        )
        heldout_ref = DatasetGateRef(
            manifest_artifact_id=manifest_artifact_id,
            dataset_id=manifest.dataset_id,
            version=manifest.version,
            dataset_digest=manifest.dataset_digest,
            split_name=heldout_split,
        )
        return CandidateQualificationRecord(
            qualification_id="combat-skill-qualification",
            revision=revision,
            candidate_record_artifact_id=candidate_record_artifact_id,
            candidate_id=candidate.candidate_id,
            candidate_version=candidate.version,
            candidate_digest=candidate.candidate_digest,
            created_at="2026-09-24T10:18:00Z",
            gate_revision="promotion-gate-r1",
            stages=(
                QualificationStage(
                    stage="replay",
                    passed=True,
                    evaluated_at="2026-09-24T10:18:01Z",
                    evaluator_revision="replay-evaluator-r1",
                    result_artifact_id=replay_result.artifact_id,
                    metrics={"cases": 1, "legal_rate": 1.0},
                    dataset=replay_ref,
                ),
                QualificationStage(
                    stage="frozen_held_out",
                    passed=True,
                    evaluated_at="2026-09-24T10:18:02Z",
                    evaluator_revision="heldout-evaluator-r1",
                    result_artifact_id=heldout_result.artifact_id,
                    metrics={"cases": 1, "preferred_rate": 1.0},
                    dataset=heldout_ref,
                ),
                QualificationStage(
                    stage="shadow",
                    passed=shadow_passed,
                    evaluated_at="2026-09-24T10:18:03Z",
                    evaluator_revision="shadow-evaluator-r1",
                    result_artifact_id=shadow_result.artifact_id,
                    metrics={"decisions": 2, "disagreement_rate": 0.0},
                    source_evidence_artifact_ids=(evidence_artifact_id,),
                    failure_reason=None if shadow_passed else "shadow disagreement exceeded gate",
                ),
            ),
        )

    def fixture(self, layout):
        evidence_artifact_id, decision_ids = self.write_evidence(layout)
        manifest, manifest_artifact_id = self.write_gate_dataset(
            layout, evidence_artifact_id, decision_ids
        )
        candidate, candidate_written = self.write_candidate(layout)
        return (
            evidence_artifact_id,
            manifest,
            manifest_artifact_id,
            candidate,
            candidate_written,
        )

    def test_candidate_requires_replay_heldout_shadow_before_promotion_and_survives_move(self):
        with tempfile.TemporaryDirectory() as tempdir:
            first_root = Path(tempdir) / "first"
            layout = StorageLayout.create(first_root)
            layout.ensure_directories()
            (
                evidence_artifact_id,
                manifest,
                manifest_artifact_id,
                candidate,
                candidate_written,
            ) = self.fixture(layout)
            qualification = self.qualification(
                layout,
                candidate=candidate,
                candidate_record_artifact_id=candidate_written.record_artifact.artifact_id,
                manifest=manifest,
                manifest_artifact_id=manifest_artifact_id,
                evidence_artifact_id=evidence_artifact_id,
            )
            store = CandidatePromotionStore(layout)
            written = store.write_qualification(qualification)
            self.assertTrue(written.eligible_for_promotion)
            component = store.qualified_component_ref(qualification)
            self.assertEqual(component.kind, "skill-set")
            self.assertEqual(component.artifact_id, candidate.candidate_id)
            self.assertEqual(component.version, candidate.version)
            self.assertEqual(component.digest, candidate.payload_artifact_id)

            moved_root = Path(tempdir) / "moved"
            shutil.copytree(first_root, moved_root)
            moved_store = CandidatePromotionStore(StorageLayout.create(moved_root))
            recovered = moved_store.read_qualification_artifact(written.artifact.artifact_id)
            self.assertTrue(recovered.eligible_for_promotion)
            self.assertEqual(
                moved_store.qualified_component_ref(recovered),
                component,
            )

    def test_frozen_heldout_gate_must_reference_frozen_test_split(self):
        with tempfile.TemporaryDirectory() as tempdir:
            layout = StorageLayout.create(Path(tempdir) / "data")
            layout.ensure_directories()
            (
                evidence_artifact_id,
                manifest,
                manifest_artifact_id,
                candidate,
                candidate_written,
            ) = self.fixture(layout)
            qualification = self.qualification(
                layout,
                candidate=candidate,
                candidate_record_artifact_id=candidate_written.record_artifact.artifact_id,
                manifest=manifest,
                manifest_artifact_id=manifest_artifact_id,
                evidence_artifact_id=evidence_artifact_id,
                heldout_split="replay-validation",
            )
            with self.assertRaises(CandidatePromotionError):
                CandidatePromotionStore(layout).write_qualification(qualification)

    def test_failed_shadow_gate_is_durable_but_not_promotable(self):
        with tempfile.TemporaryDirectory() as tempdir:
            layout = StorageLayout.create(Path(tempdir) / "data")
            layout.ensure_directories()
            (
                evidence_artifact_id,
                manifest,
                manifest_artifact_id,
                candidate,
                candidate_written,
            ) = self.fixture(layout)
            qualification = self.qualification(
                layout,
                candidate=candidate,
                candidate_record_artifact_id=candidate_written.record_artifact.artifact_id,
                manifest=manifest,
                manifest_artifact_id=manifest_artifact_id,
                evidence_artifact_id=evidence_artifact_id,
                shadow_passed=False,
            )
            store = CandidatePromotionStore(layout)
            written = store.write_qualification(qualification)
            self.assertFalse(written.eligible_for_promotion)
            recovered = store.read_qualification_artifact(written.artifact.artifact_id)
            self.assertFalse(recovered.eligible_for_promotion)
            with self.assertRaises(CandidatePromotionError):
                store.qualified_component_ref(recovered)

    def test_candidate_id_version_is_immutable(self):
        with tempfile.TemporaryDirectory() as tempdir:
            layout = StorageLayout.create(Path(tempdir) / "data")
            layout.ensure_directories()
            self.write_candidate(layout, payload=b"candidate one")
            artifacts = LocalArtifactStore(layout)
            changed_payload = artifacts.put_bytes(b"candidate two")
            changed = CandidateArtifactRecord(
                candidate_id="combat-skill-candidate",
                version="r1",
                candidate_type="skill",
                component_kind="skill-set",
                created_at="2026-09-24T10:17:00Z",
                producer_revision="skill-producer-r3",
                payload_artifact_id=changed_payload.artifact_id,
                metadata={"decision_family": "combat"},
            )
            with self.assertRaises(CandidatePromotionError):
                CandidatePromotionStore(layout).write_candidate(changed)


if __name__ == "__main__":
    unittest.main()
