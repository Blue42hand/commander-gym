import io
import os
import unittest
from unittest.mock import patch

from commander_gym.local_display_text import main, render_status


class LocalDisplayTextTests(unittest.TestCase):
    def test_render_status_is_host_only(self):
        status = {"host": {"uptime": "up 2 days", "load": "0.1 0.2 0.3",
                           "memory": "2 GiB / 64 GiB", "temperature": "42C"},
                  "services": {"argentum-gym.service": "active"},
                  "live": {"state": {"hand": ["PRIVATE CARD"]},
                           "choice": {"reason": "PRIVATE REASON"}}}
        frame = render_status(status, revisions={"Display": "abc123"})
        self.assertIn("argentum-gym.service", frame)
        self.assertIn("abc123", frame)
        self.assertIn("not authenticated health", frame)
        self.assertNotIn("PRIVATE", frame)

    def test_once_uses_no_provenance_and_no_terminal_codes(self):
        status = {"host": {"uptime": "up", "load": "0", "memory": "0", "temperature": "n/a"},
                  "services": {}}
        output = io.StringIO()
        with (patch("commander_gym.local_display_text.build_status", return_value=status) as builder,
              patch("sys.stdout", output),
              patch("commander_gym.local_display_text._revisions_from_environment", return_value={})):
            self.assertEqual(main(["--once"]), 0)
        builder.assert_called_once_with(None)
        self.assertIn("Pilot data: off", output.getvalue())
        self.assertNotIn("\x1b", output.getvalue())

    def test_data_cannot_write_terminal_controls(self):
        status = {"host": {"uptime": "up\x1b]52;c;SECRET\x07\nagain",
                           "load": "0", "memory": "0", "temperature": "n/a"},
                  "services": {"evil\x1b[2Junit": "active\rFAKE"}}
        frame = render_status(status, revisions={"Display": "sha\x9b31m"})
        self.assertNotIn("\x1b", frame)
        self.assertNotIn("\x07", frame)
        self.assertNotIn("\x9b", frame)
        self.assertNotIn("\r", frame)
        self.assertNotIn("SECRET\n", frame)
        self.assertIn("up]52;c;SECRETagain", frame)

    def test_once_sanitizes_operator_revision(self):
        status = {"host": {"uptime": "up", "load": "0", "memory": "0", "temperature": "n/a"},
                  "services": {}}
        output = io.StringIO()
        with (patch("commander_gym.local_display_text.build_status", return_value=status),
              patch("sys.stdout", output),
              patch.dict(os.environ, {"COMMANDER_GYM_DISPLAY_REVISION": "sha\x1b]52;c;SECRET\x07"})):
            self.assertEqual(main(["--once"]), 0)
        self.assertNotIn("\x1b", output.getvalue())
        self.assertNotIn("\x07", output.getvalue())

    def test_continuous_mode_restores_cursor_on_interrupt(self):
        class Terminal(io.StringIO):
            def isatty(self):
                return True

        status = {"host": {"uptime": "up", "load": "0", "memory": "0", "temperature": "n/a"},
                  "services": {}}
        output = Terminal()
        with (patch("commander_gym.local_display_text.build_status", return_value=status) as builder,
              patch("commander_gym.local_display_text._revisions_from_environment", return_value={}),
              patch("commander_gym.local_display_text.time.sleep", side_effect=KeyboardInterrupt),
              patch("sys.stdout", output)):
            self.assertEqual(main([]), 0)
        builder.assert_called_once_with(None)
        self.assertTrue(output.getvalue().startswith("\x1b[?25l\x1b[H\x1b[2J"))
        self.assertTrue(output.getvalue().endswith("\x1b[?25h\n"))


if __name__ == "__main__":
    unittest.main()
