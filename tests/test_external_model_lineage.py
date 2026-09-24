import tempfile
import unittest
from pathlib import Path

from commander_gym.external_provenance import (
    ExternalLicense, ExternalModelManifest, ExternalProvenanceStore,
)
from commander_gym.model_lineage import (
    ExternalModelLineageRef, ModelArtifactRef, ModelLineageRecord, ModelLineageStore,
)
from commander_gym.storage import LocalArtifactStore, StorageLayout


class ExternalModelLineageTests(unittest.TestCase):
    def test_imported_checkpoint_can_be_exact_parent_of_derived_model(self):
        with tempfile.TemporaryDirectory() as td:
            layout = StorageLayout.create(Path(td)); layout.ensure_directories()
            artifacts = LocalArtifactStore(layout)
            checkpoint = artifacts.put_bytes(b"external checkpoint")
            parent = ExternalModelManifest(
                model_id="external-policy", version="r1",
                retrieved_at="2026-09-24T19:00:00Z",
                source_project="fixture", source_reference="fixture://model",
                source_revision="abc", checkpoint_artifact_id=checkpoint.artifact_id,
                license=ExternalLicense("fixture","fixture://terms",True,False),
                framework="pytorch", architecture="transformer-policy",
                training_provenance={"source":"fixture"},
                observation_schema={"kind":"fixture-observation"},
                action_schema={"kind":"fixture-action"}, value_schema={"kind":"scalar"},
                formats=("1v1",), decks=("fixture-deck",),
                opponent_population={"kind":"self-play"},
                privileged_information_exposure={"opponent_hand":False,"future_draws":False},
                evaluation_caveats=("fixture only",), adapter_history=(),
                seat_safe_qualification="qualified",
            )
            imported = ExternalProvenanceStore(layout).write_model(parent)
            derived = artifacts.put_bytes(b"derived checkpoint")
            lineage = ModelLineageRecord(
                model_id="derived-policy", version="r1",
                created_at="2026-09-24T19:10:00Z", producer_revision="trainer-r1",
                datasets=(), artifacts=(ModelArtifactRef("checkpoint",derived.artifact_id),),
                external_parent_models=(ExternalModelLineageRef(
                    imported.artifact.artifact_id, parent.model_id, parent.version,
                    parent.model_digest, parent.checkpoint_artifact_id,
                ),),
            )
            written = ModelLineageStore(layout).write(lineage)
            self.assertEqual(
                written.external_parent_model_manifest_artifact_ids,
                (imported.artifact.artifact_id,),
            )


if __name__ == "__main__":
    unittest.main()
