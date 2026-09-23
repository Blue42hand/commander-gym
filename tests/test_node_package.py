import json
from pathlib import Path
import tempfile
import unittest

from commander_gym.node_package import (
    NODE_PACKAGE_VERSION,
    NodePackageConfig,
    NodePackageError,
    render_node_package,
)


CG_REV = "1" * 40
ARGENTUM_REV = "2" * 40


def config_mapping(tmp: Path, *, with_model: bool = False):
    config = {
        "version": NODE_PACKAGE_VERSION,
        "service_user": "cgsvc",
        "service_group": "cgsvc",
        "commander_gym": {
            "root": str(tmp / "commander gym"),
            "revision": CG_REV,
        },
        "argentum": {
            "root": str(tmp / "argentum engine"),
            "revision": ARGENTUM_REV,
        },
        "gateway_token_file": str(tmp / "secrets" / "gateway.token"),
        "gateway": {
            "bind": "127.0.0.1",
            "port": 18082,
            "upstream": "http://127.0.0.1:18081",
        },
        "storage": {
            "root": str(tmp / "data root"),
            "routes": {
                "models": str(tmp / "bulk models"),
                "cache": str(tmp / "cache"),
            },
        },
    }
    if with_model:
        config["local_inference"] = {
            "command": ["/usr/bin/example-model", "serve", "--port", "18000"],
            "working_directory": str(tmp / "model work"),
            "environment_file": str(tmp / "secrets" / "model.env"),
        }
    return config


class NodePackageConfigTests(unittest.TestCase):
    def test_requires_full_pinned_revisions(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = config_mapping(Path(directory))
            raw["argentum"]["revision"] = "main"
            with self.assertRaisesRegex(NodePackageError, "40-character git SHA"):
                NodePackageConfig.from_mapping(raw)

    def test_rejects_relative_host_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = config_mapping(Path(directory))
            raw["gateway_token_file"] = "relative/token"
            with self.assertRaisesRegex(NodePackageError, "must be absolute"):
                NodePackageConfig.from_mapping(raw)

    def test_storage_contract_reuses_public_storage_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            config = NodePackageConfig.from_mapping(config_mapping(tmp))
            self.assertEqual(config.storage.path("runs"), tmp / "data root" / "runs")
            self.assertEqual(config.storage.path("models"), tmp / "bulk models")


class RenderNodePackageTests(unittest.TestCase):
    def test_render_is_deterministic_and_host_path_configurable(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            config = NodePackageConfig.from_mapping(config_mapping(tmp))
            first = render_node_package(config, tmp / "package")
            snapshot = {
                path.relative_to(first): path.read_bytes()
                for path in first.rglob("*")
                if path.is_file()
            }
            second = render_node_package(config, tmp / "package")
            rerendered = {
                path.relative_to(second): path.read_bytes()
                for path in second.rglob("*")
                if path.is_file()
            }
            self.assertEqual(snapshot, rerendered)

            manifest = json.loads((first / "node-package.json").read_text())
            self.assertEqual(manifest["commander_gym"]["revision"], CG_REV)
            self.assertEqual(manifest["argentum"]["revision"], ARGENTUM_REV)
            self.assertEqual(manifest["storage"]["root"], str(tmp / "data root"))

            gateway = (first / "run-commander-gym-gateway.sh").read_text()
            self.assertIn(str(tmp / "commander gym"), gateway)
            self.assertIn("COMMANDER_GYM_STORAGE_CONFIG", gateway)
            self.assertNotIn("/srv/commander-gym", gateway)

            argentum = (first / "run-argentum-gym.sh").read_text()
            self.assertIn(str(tmp / "argentum engine"), argentum)
            self.assertNotIn("/srv/argentum", argentum)

    def test_render_quotes_paths_with_spaces(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            config = NodePackageConfig.from_mapping(config_mapping(tmp))
            package = render_node_package(config, tmp / "rendered package")
            gateway = (package / "run-commander-gym-gateway.sh").read_text()
            self.assertIn("'" + str(tmp / "commander gym"), gateway)
            unit = (package / "systemd" / "commander-gym-gateway.service").read_text()
            self.assertIn('ExecStart="', unit)
            self.assertIn("rendered package/run-commander-gym-gateway.sh", unit)

    def test_optional_local_inference_is_generic_service_scaffolding(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            config = NodePackageConfig.from_mapping(config_mapping(tmp, with_model=True))
            package = render_node_package(config, tmp / "package")
            runner = (package / "run-local-inference.sh").read_text()
            self.assertIn("/usr/bin/example-model serve --port 18000", runner)
            unit = (package / "systemd" / "commander-gym-model.service").read_text()
            self.assertIn("Optional Local Inference Service", unit)
            self.assertIn(str(tmp / "secrets" / "model.env"), unit)

    def test_no_model_unit_when_local_inference_is_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            config = NodePackageConfig.from_mapping(config_mapping(tmp))
            package = render_node_package(config, tmp / "package")
            self.assertFalse((package / "run-local-inference.sh").exists())
            self.assertFalse((package / "systemd" / "commander-gym-model.service").exists())


if __name__ == "__main__":
    unittest.main()
