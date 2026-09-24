import json
import tempfile
import unittest
from pathlib import Path

from commander_gym.dataset_manifest import (
    DatasetManifest, DatasetManifestError, DatasetManifestStore, DatasetSplit,
    ExternalDatasetSelection,
)
from commander_gym.external_provenance import (
    ExternalDataSourceManifest, ExternalLicense, ExternalProvenanceStore,
)
from commander_gym.storage import LocalArtifactStore, StorageLayout


class ExternalDatasetTests(unittest.TestCase):
    def source(self, layout, *, boundary="seat_visible", future=False, training=True):
        artifacts = LocalArtifactStore(layout)
        payload = artifacts.put_bytes(b"rows")
        index = artifacts.put_bytes(json.dumps({"records":[
            {"record_id":"a","leakage_group_id":"same-game"},
            {"record_id":"b","leakage_group_id":"same-game"},
        ]}).encode())
        manifest = ExternalDataSourceManifest(
            "fixture","r1","2026-09-24T19:00:00Z",
            "external_observation_action_record","fixture://source","abc",
            payload.artifact_id,index.artifact_id,
            ExternalLicense("fixture","fixture://terms",training,False),
            "partial",boundary,(),(),
            {"contains_privileged_or_future_information":future},
            "games-v1",{"kind":"fixture"},
        )
        return ExternalProvenanceStore(layout).write_data_source(manifest).artifact.artifact_id

    def dataset(self, source_id):
        return DatasetManifest(
            "external-fixture","r1","external split test","2026-09-24T19:00:00Z",
            "external fixture",{}, {}, {}, (), (),
            (
                DatasetSplit("train","train",(),(
                    ExternalDatasetSelection(source_id,("a",)),
                )),
                DatasetSplit("test","frozen_test",(),(
                    ExternalDatasetSelection(source_id,("b",)),
                )),
            ),
            "fixture-generator",
        )

    def test_leakage_group_cannot_cross_splits(self):
        with tempfile.TemporaryDirectory() as td:
            layout = StorageLayout.create(Path(td)); layout.ensure_directories()
            source_id = self.source(layout)
            with self.assertRaises(DatasetManifestError):
                DatasetManifestStore(layout).write(self.dataset(source_id))

    def test_training_source_must_be_seat_visible_explicitly_safe_and_licensed(self):
        for boundary, future, training in (
            ("full_state",False,True), ("seat_visible",True,True), ("seat_visible",False,None),
        ):
            with self.subTest(boundary=boundary, future=future, training=training):
                with tempfile.TemporaryDirectory() as td:
                    layout = StorageLayout.create(Path(td)); layout.ensure_directories()
                    source_id = self.source(
                        layout, boundary=boundary, future=future, training=training
                    )
                    with self.assertRaises(DatasetManifestError):
                        DatasetManifestStore(layout).write(self.dataset(source_id))


if __name__ == "__main__":
    unittest.main()
