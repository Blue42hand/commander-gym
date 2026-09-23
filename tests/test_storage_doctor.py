import tempfile
import unittest
from pathlib import Path

from commander_gym.storage import StorageError, StorageLayout
from commander_gym.storage_doctor import check_storage, main


class StorageDoctorTests(unittest.TestCase):
    def test_create_initializes_routes_and_reports_capacity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "data"
            layout = StorageLayout.create(root)

            health = check_storage(layout, create=True)

            self.assertTrue(health.ok)
            self.assertEqual(len(health.routes), len(layout.routes))
            for route in health.routes:
                self.assertTrue(route.exists)
                self.assertTrue(route.writable)
                self.assertIsInstance(route.free_bytes, int)
                self.assertGreater(route.total_bytes, 0)
                self.assertIsNone(route.error)

    def test_missing_routes_fail_without_create(self):
        with tempfile.TemporaryDirectory() as directory:
            layout = StorageLayout.create(Path(directory) / "missing")

            health = check_storage(layout)

            self.assertFalse(health.ok)
            self.assertTrue(all(not route.exists for route in health.routes))
            self.assertTrue(
                all(route.error == "directory does not exist" for route in health.routes)
            )

    def test_capacity_threshold_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            layout = StorageLayout.create(Path(directory) / "data")

            health = check_storage(
                layout,
                create=True,
                min_free_bytes=2**63 - 1,
            )

            self.assertFalse(health.ok)
            self.assertTrue(all(route.ok for route in health.routes))
            self.assertTrue(
                all(route.free_bytes < health.min_free_bytes for route in health.routes)
            )

    def test_invalid_capacity_threshold_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            layout = StorageLayout.create(Path(directory) / "data")
            with self.assertRaises(StorageError):
                check_storage(layout, min_free_bytes=-1)

    def test_cli_returns_nonzero_for_missing_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(main(["--root", str(Path(directory) / "missing")]), 1)

    def test_cli_can_initialize_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                main(["--root", str(Path(directory) / "data"), "--create"]),
                0,
            )


if __name__ == "__main__":
    unittest.main()
