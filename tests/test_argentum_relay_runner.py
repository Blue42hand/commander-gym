import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.argentum_issue_relay import relay_comment_from_environment


class ArgentumRelayRunnerTests(unittest.TestCase):
    def test_command_file_becomes_strict_argentum_comment(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "relay-command.json"
            path.write_text('{"op":"status"}\n', encoding="utf-8")
            with patch.dict(
                os.environ,
                {
                    "ARGENTUM_RELAY_COMMAND_FILE": str(path),
                    "ARGENTUM_RELAY_COMMENT": "/argentum {\"op\":\"health\"}",
                },
                clear=False,
            ):
                self.assertEqual(
                    relay_comment_from_environment(),
                    '/argentum {"op":"status"}',
                )

    def test_comment_is_used_when_no_command_file_is_set(self):
        with patch.dict(
            os.environ,
            {
                "ARGENTUM_RELAY_COMMAND_FILE": "",
                "ARGENTUM_RELAY_COMMENT": '/argentum {"op":"health"}',
            },
            clear=False,
        ):
            self.assertEqual(
                relay_comment_from_environment(),
                '/argentum {"op":"health"}',
            )


if __name__ == "__main__":
    unittest.main()
