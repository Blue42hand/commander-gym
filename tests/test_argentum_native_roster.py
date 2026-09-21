import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROSTER = ROOT / "rosters" / "argentum-native-v1"
BASICS = {"Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes"}


class ArgentumNativeRosterTest(unittest.TestCase):
    def test_exact_four_deck_roster_is_structurally_legal(self):
        manifest = json.loads((ROSTER / "manifest.json").read_text())
        self.assertEqual(4, len(manifest["decks"]))
        self.assertEqual(4, len({entry["id"] for entry in manifest["decks"]}))
        for entry in manifest["decks"]:
            deck = json.loads((ROSTER / entry["file"]).read_text())
            self.assertEqual(entry["id"], deck["deck_id"])
            self.assertEqual(100, sum(deck["cards"].values()), entry["id"])
            self.assertEqual(1, deck["cards"].get(deck["commander"]), entry["id"])
            self.assertTrue((ROSTER / entry["primer"]).is_file())
            for name, count in deck["cards"].items():
                if name not in BASICS:
                    self.assertEqual(1, count, f"{entry['id']}: {name}")

    def test_builder_emits_direct_full_game_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "game.json"
            subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "build_argentum_roster_game.py"),
                 str(ROSTER / "manifest.json"), "--output", str(output)],
                check=True,
            )
            game = json.loads(output.read_text())
            self.assertEqual(4, len(game["argentum_config"]["players"]))
            self.assertEqual(4, len(game["seats"]))
            for player, seat in zip(game["argentum_config"]["players"], game["seats"]):
                self.assertEqual(100, sum(player["deck"]["cards"].values()))
                self.assertEqual(player["name"], seat["player_name"])
                self.assertTrue(seat["pilot"]["strategy"].startswith("# "))


if __name__ == "__main__":
    unittest.main()
