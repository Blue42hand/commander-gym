import plistlib
import stat
import tempfile
import unittest
from pathlib import Path

from scripts.argentum_gateway_launchd import ensure_token, render_plist


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


if __name__ == "__main__":
    unittest.main()
