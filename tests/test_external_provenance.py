import json
import tempfile
import unittest
from pathlib import Path

from commander_gym.external_provenance import (
    ExternalDataSourceManifest,
    ExternalLicense,
    ExternalProvenanceError,
    ExternalProvenanceStore,
)
from commander_gym.storage import LocalArtifactStore, StorageLayout


class ExternalSourceTests(unittest.TestCase):
    def test_external_source_is_content_addressed_and_native_class_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            layout = StorageLayout.create(Path(td))
            layout.ensure_directories()
            artifacts = LocalArtifactStore(layout)
            payload = artifacts.put_bytes(b"rows")
            index = artifacts.put_bytes(json.dumps({"records": [
                {"record_id":"r1","leakage_group_id":"game-1"}
            ]}).encode())
            license_info = ExternalLicense("fixture","fixture://terms",True,False)
            source = ExternalDataSourceManifest(
                source_id="fixture", version="r1", retrieved_at="2026-09-24T19:00:00Z",
                source_class="external_observation_action_record",
                source_reference="fixture://source", source_revision="abc",
                payload_artifact_id=payload.artifact_id,
                record_index_artifact_id=index.artifact_id,
                license=license_info, information_completeness="partial",
                acting_player_information_boundary="seat_visible",
                missing_fields=("within_turn_order",), reconstruction_transforms=(),
                uncertainty={"contains_privileged_or_future_information":False},
                deduplication_identity="games-v1", record_schema={"kind":"fixture"},
            )
            written = ExternalProvenanceStore(layout).write_data_source(source)
            recovered = ExternalProvenanceStore(layout).read_data_source_artifact(
                written.artifact.artifact_id
            )
            self.assertEqual(recovered.source_digest, source.source_digest)
            bad = ExternalDataSourceManifest(
                **{**source.__dict__, "source_class":"native_engine_trajectory"}
            )
            with self.assertRaises(ExternalProvenanceError):
                bad.validate()


if __name__ == "__main__":
    unittest.main()
