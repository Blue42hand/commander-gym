import plistlib
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.argentum_gateway_launchd import (
    ensure_token,
    installed_checkout_status,
    render_plist,
)


class ArgentumGatewayLaunchdTests(unittest.TestCase):
    def test_rendered_plist_is_loopback_only_and_contains_no_bearer_secret(self):
        plist = plistlib.loads(
            render_plist(
                repo_root=Path("/repo"),
                revision="abc123",
                stdout_path=Path("/logs/stdout.log"),
                stderr_path=Path("/logs/stderr.log"),
                token_path=Path("/state/bearer-token"),
            )
        )
        env = plist["EnvironmentVariables"]
        self.assertEqual(env["COMMANDER_GYM_GATEWAY_BIND"], "127.0.0.1")
        self.assertEqual(env["COMMANDER_GYM_GATEWAY_PORT"], "8082")
        self.assertEqual(env["COMMANDER_GYM_GATEWAY_UPSTREAM"], "http://127.0.0.1:8081")
        serialized = plistlib.dumps(plist)
        self.assertNotIn(b"COMMANDER_GYM_GATEWAY_TOKEN", serialized)
        self.assertNotIn(b"secret", serialized)

    def test_token_file_is_created_mode_600_and_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bearer-token"
            ensure_token(path)
            first = path.read_text(encoding="utf-8").strip()
            self.assertTrue(first)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

            ensure_token(path)
            second = path.read_text(encoding="utf-8").strip()
            self.assertEqual(first, second)

    def test_installed_checkout_status_detects_revision_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
            (repo / "file.txt").write_text("first\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "file.txt"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "first"], check=True)
            pinned = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
            plist = root / "gateway.plist"
            plist.write_bytes(render_plist(
                repo_root=repo,
                revision=pinned,
                stdout_path=root / "stdout.log",
                stderr_path=root / "stderr.log",
                token_path=root / "token",
            ))

            healthy = installed_checkout_status(plist)
            self.assertTrue(healthy["matchesPinnedRevision"])
            self.assertEqual(pinned, healthy["checkoutRevision"])

            (repo / "file.txt").write_text("second\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "commit", "-qam", "second"], check=True)
            drifted = installed_checkout_status(plist)
            self.assertFalse(drifted["matchesPinnedRevision"])
            self.assertEqual(pinned, drifted["pinnedRevision"])
            self.assertNotEqual(pinned, drifted["checkoutRevision"])
            self.assertIn("revision differs", drifted["error"])


if __name__ == "__main__":
    unittest.main()
