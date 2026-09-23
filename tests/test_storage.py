import shutil
import tempfile
import unittest
from pathlib import Path

from commander_gym.storage import (
    DURABLE_STORAGE_ROUTES,
    EPHEMERAL_STORAGE_ROUTES,
    LocalArtifactStore,
    StorageError,
    StorageLayout,
    artifact_id_for_bytes,
)


class StorageTests(unittest.TestCase):
    def test_layout_defaults_and_overrides_are_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "node-data"
            external_models = Path(directory) / "large-models"
            layout = StorageLayout.create(
                root,
                overrides={
                    "runs": "qualified-runs",
                    "models": external_models,
                },
            )

            self.assertEqual(layout.path("artifacts"), root / "artifacts")
            self.assertEqual(layout.path("runs"), root / "qualified-runs")
            self.assertEqual(layout.path("models"), external_models)
            self.assertEqual(
                set(DURABLE_STORAGE_ROUTES),
                {"artifacts", "runs", "annotations", "datasets", "models", "catalog"},
            )
            self.assertEqual(set(EPHEMERAL_STORAGE_ROUTES), {"logs", "cache"})

            layout.ensure_directories()
            for path in layout.routes.values():
                self.assertTrue(path.is_dir())

    def test_mapping_rejects_unknown_routes(self):
        with self.assertRaises(StorageError):
            StorageLayout.from_mapping(
                {"root": "/tmp/commander-gym", "routes": {"mystery": "elsewhere"}}
            )

    def test_content_identity_does_not_depend_on_root(self):
        payload = b"portable commander gym evidence fixture\n"
        expected = artifact_id_for_bytes(payload)

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            first_root = base / "host-a"
            first_store = LocalArtifactStore(StorageLayout.create(first_root))
            stored = first_store.put_bytes(payload)
            self.assertEqual(stored.artifact_id, expected)

            second_root = base / "host-b"
            shutil.copytree(first_root, second_root)
            second_store = LocalArtifactStore(StorageLayout.create(second_root))

            self.assertEqual(second_store.read_bytes(expected), payload)
            self.assertNotEqual(
                first_store.path_for(expected),
                second_store.path_for(expected),
            )
            self.assertEqual(
                first_store.path_for(expected).relative_to(first_root),
                second_store.path_for(expected).relative_to(second_root),
            )

    def test_store_detects_corruption(self):
        payload = b"original"
        with tempfile.TemporaryDirectory() as directory:
            store = LocalArtifactStore(StorageLayout.create(directory))
            ref = store.put_bytes(payload)
            store.path_for(ref.artifact_id).write_bytes(b"corrupt")
            with self.assertRaises(StorageError):
                store.read_bytes(ref.artifact_id)


if __name__ == "__main__":
    unittest.main()
