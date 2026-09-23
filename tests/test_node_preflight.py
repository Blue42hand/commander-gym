import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from commander_gym.node_package import NodePackageConfig
from commander_gym.node_preflight import check_node_preflight, main


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _checkout(root: Path, files: dict[str, str]) -> str:
    root.mkdir(parents=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Commander Gym Tests")
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        path.chmod(path.stat().st_mode | 0o111)
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "fixture")
    return _git(root, "rev-parse", "HEAD")


def _config(tmp: Path, *, with_model: bool = False) -> NodePackageConfig:
    cg_root = tmp / "cg"
    argentum_root = tmp / "argentum"
    cg_revision = _checkout(
        cg_root,
        {"scripts/run_argentum_gateway_service.sh": "#!/bin/bash\nexit 0\n"},
    )
    argentum_revision = _checkout(
        argentum_root,
        {
            "scripts/run-gym-server-service.sh": "#!/bin/bash\nexit 0\n",
            "gradlew": "#!/bin/bash\nexit 0\n",
        },
    )
    token = tmp / "secrets" / "gateway.token"
    token.parent.mkdir(parents=True)
    token.write_text("test-token\n", encoding="utf-8")

    raw = {
        "commander_gym": {"root": str(cg_root), "revision": cg_revision},
        "argentum": {"root": str(argentum_root), "revision": argentum_revision},
        "gateway_token_file": str(token),
        "storage": {"root": str(tmp / "data")},
    }
    if with_model:
        model = tmp / "bin" / "model-server"
        model.parent.mkdir(parents=True)
        model.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        model.chmod(0o755)
        work = tmp / "model-work"
        work.mkdir()
        env = tmp / "secrets" / "model.env"
        env.write_text("MODEL_PORT=18000\n", encoding="utf-8")
        raw["local_inference"] = {
            "command": [str(model), "serve"],
            "working_directory": str(work),
            "environment_file": str(env),
        }
    return NodePackageConfig.from_mapping(raw)


def _commands(command: str) -> str:
    return f"/test-bin/{command}"


class NodePreflightTests(unittest.TestCase):
    def test_healthy_clean_host_prerequisites_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            config = _config(Path(directory), with_model=True)
            report = check_node_preflight(
                config,
                initialize_storage=True,
                command_resolver=_commands,
            )
            self.assertTrue(report.ok, json.dumps(report.to_dict(), indent=2))
            self.assertTrue(report.storage.ok)
            self.assertTrue(all(item.ok for item in report.checkouts))
            self.assertTrue(all(item.ok for item in report.local_inference))
            for path in config.storage.routes.values():
                self.assertTrue(path.is_dir())

    def test_revision_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            config = _config(tmp)
            raw = config.to_dict()
            raw["commander_gym"]["revision"] = "f" * 40
            mismatched = NodePackageConfig.from_mapping(raw)
            report = check_node_preflight(
                mismatched,
                initialize_storage=True,
                command_resolver=_commands,
            )
            self.assertFalse(report.ok)
            checkout = next(item for item in report.checkouts if item.name == "commander-gym")
            self.assertIn("revision mismatch", checkout.error or "")

    def test_dirty_tracked_checkout_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            config = _config(tmp)
            script = config.commander_gym.root / "scripts/run_argentum_gateway_service.sh"
            script.write_text("#!/bin/bash\nexit 1\n", encoding="utf-8")
            report = check_node_preflight(
                config,
                initialize_storage=True,
                command_resolver=_commands,
            )
            self.assertFalse(report.ok)
            checkout = next(item for item in report.checkouts if item.name == "commander-gym")
            self.assertFalse(checkout.clean)
            self.assertEqual(checkout.error, "tracked checkout files are dirty")

    def test_blank_gateway_token_fails_without_exposing_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            config = _config(tmp)
            config.gateway_token_file.write_text("", encoding="utf-8")
            report = check_node_preflight(
                config,
                initialize_storage=True,
                command_resolver=_commands,
            )
            self.assertFalse(report.ok)
            self.assertFalse(report.gateway_token.ok)
            self.assertEqual(report.gateway_token.error, "file is empty")
            serialized = json.dumps(report.to_dict())
            self.assertNotIn("test-token", serialized)

    def test_missing_host_command_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            config = _config(Path(directory))

            def resolver(command: str):
                return None if command == "java" else f"/test-bin/{command}"

            report = check_node_preflight(
                config,
                initialize_storage=True,
                command_resolver=resolver,
            )
            self.assertFalse(report.ok)
            java = next(item for item in report.commands if item.command == "java")
            self.assertFalse(java.ok)

    def test_cli_can_skip_systemd_on_render_only_host(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            config = _config(tmp)
            config_path = tmp / "node.json"
            config_path.write_text(json.dumps(config.to_dict()), encoding="utf-8")
            # The CLI uses the real PATH, so only exercise argument/config parsing here.
            # A missing runtime dependency is a normal fail-closed result, not a config error.
            result = main(
                [
                    "--config",
                    str(config_path),
                    "--initialize-storage",
                    "--no-systemd",
                ]
            )
            self.assertIn(result, (0, 1))


if __name__ == "__main__":
    unittest.main()
