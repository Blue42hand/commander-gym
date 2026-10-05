import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from commander_gym.local_display import _records, build_status, main


class LocalDisplayTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "provenance.jsonl"

    def status(self):
        with (patch("commander_gym.local_display._host", return_value={}),
              patch("commander_gym.local_display._services", return_value={}),
              patch("commander_gym.local_display._revisions", return_value={})):
            return build_status(self.path)

    def test_records_ignore_bad_and_keep_policy(self):
        self.path.write_text("bad\n" + json.dumps({"event": "other"}) + "\n" +
            json.dumps({"event": "game_server_policy", "playerId": "p1",
                "callback": "chooseAction", "observation": {"state": {"phase": "MAIN1"},
                "recentGameLog": ["cast Sol Ring"]},
                "choice": {"channel": "action", "actionId": 2}}) + "\n")
        rows = _records(self.path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["playerId"], "p1")

    def test_status_uses_existing_provenance(self):
        rows = [
            {"event": "game_server_policy", "playerId": "p1", "callback": "decideMulligan",
             "observation": {"state": {}}, "choice": {"keep": True}},
            {"event": "game_server_policy", "playerId": "p2", "callback": "chooseAction",
             "observation": {"state": {"phase": "COMBAT", "turnNumber": 4},
                             "recentGameLog": ["attack"]},
             "choice": {"channel": "decision", "metadata": {"reason": "block"}}},
        ]
        self.path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        status = self.status()
        self.assertEqual(status["policy"],
            {"calls": 2, "seats": 2, "actions": 0, "decisions": 1, "mulligans": 1})
        self.assertEqual(status["live"]["state"]["phase"], "COMBAT")
        self.assertNotIn("recentGameLog", status["live"])

    def test_status_does_not_publish_seat_private_fields(self):
        self.path.write_text(json.dumps({
            "event": "game_server_policy", "playerId": "private-seat",
            "callback": "chooseAction",
            "observation": {"state": {"phase": "MAIN1", "turnNumber": 2,
                "players": [{"name": "private-name", "life": 37, "handSize": 5,
                             "hand": ["private-card"], "battlefield": ["private-board"]}],
                "secret": "private-state"}, "recentGameLog": ["private-log"]},
            "choice": {"channel": "action", "metadata": {"reason": "private-reason"}},
        }) + "\n")
        status = self.status()
        self.assertEqual(status["live"]["state"],
            {"phase": "MAIN1", "turnNumber": 2,
             "players": [{"life": 37, "handSize": 5}]})
        self.assertNotIn("private-", json.dumps(status))

    def test_display_refuses_non_loopback_bind(self):
        with patch.dict(os.environ, {"COMMANDER_GYM_DISPLAY_HOST": "0.0.0.0"}):
            with self.assertRaisesRegex(SystemExit, "loopback"):
                main()


if __name__ == "__main__":
    unittest.main()
