import json
import tempfile
import unittest
from pathlib import Path

from commander_gym.external_derivation import ExternalParentModelRef, ModelDerivationRecord
from commander_gym.external_derivation_store import ModelDerivationStore
from commander_gym.external_provenance import ExternalLicense, ExternalModelManifest, ExternalProvenanceStore
from commander_gym.model_lineage import DatasetLineageRef, ModelArtifactRef, ModelLineageRecord
from commander_gym.storage import LocalArtifactStore, StorageLayout


class ExternalDerivationTests(unittest.TestCase):
    def test_derived_model_traces_to_exact_imported_checkpoint(self):
        with tempfile.TemporaryDirectory() as td:
            layout = StorageLayout.create(Path(td)); layout.ensure_directories()
            artifacts = LocalArtifactStore(layout)
            checkpoint = artifacts.put_bytes(b"imported")
            parent = ExternalModelManifest(
                "parent","r1","2026-09-24T19:00:00Z","fixture","fixture://model","abc",
                checkpoint.artifact_id,ExternalLicense("fixture","fixture://terms",True,False),
                "pytorch","transformer",{"source":"fixture"},{"kind":"obs"},{"kind":"act"},
                {"kind":"value"},("1v1",),("deck",),{"kind":"self-play"},
                {"opponent_hand":False},(),(),"qualified",
            )
            imported = ExternalProvenanceStore(layout).write_model(parent)
            derived = artifacts.put_bytes(b"derived")
            zero = "sha256:" + "0" * 64
            lineage = ModelLineageRecord(
                "derived","r1","2026-09-24T19:10:00Z","trainer",
                (DatasetLineageRef(zero,"dataset","r1",zero),),
                (ModelArtifactRef("checkpoint",derived.artifact_id),),
            )
            lineage_artifact = artifacts.put_bytes(json.dumps(
                lineage.to_dict(),sort_keys=True,separators=(",",":"),ensure_ascii=True
            ).encode())
            derivation = ModelDerivationRecord(
                "derived","r1",lineage_artifact.artifact_id,
                (ExternalParentModelRef(
                    imported.artifact.artifact_id,parent.model_id,parent.version,
                    parent.model_digest,parent.checkpoint_artifact_id,"initialization",
                ),),
                ({"name":"fine_tune","revision":"trainer"},),
            )
            stored = ModelDerivationStore(layout).write(derivation)
            self.assertTrue(stored.artifact_id.startswith("sha256:"))


if __name__ == "__main__":
    unittest.main()
