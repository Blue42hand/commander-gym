import copy
import unittest
from collections import deque

from commander_gym.console_spectator import player_views, render


def snapshot():
    players = [{"playerId": f"p{i}", "name": f"Player {i}", "life": 40-i, "handSize": i}
               for i in range(1, 5)]
    cards = {f"c{i}": {"name": f"Permanent {i}", "controllerId": f"p{i}",
                       "ownerId": f"p{i}", "zone": {"zoneType": "Battlefield", "ownerId": f"p{i}"}}
             for i in range(1, 5)}
    return {"gameSessionId": "game", "currentPhase": "MAIN", "activePlayerId": "p3",
            "priorityPlayerId": "p4", "players": [{"playerId": p["playerId"], "name": p["name"]}
                                                    for p in players],
            "gameState": {"players": players, "cards": cards},
            "player1": {"playerId": "p1", "playerName": "Stale legacy", "life": 99}}


class ConsoleSpectatorTests(unittest.TestCase):
    def test_all_four_seats_use_public_state_in_roster_order(self):
        data = snapshot()
        data["gameState"]["players"].reverse()
        before = copy.deepcopy(data)
        frame = render(data, deque())
        for i in range(1, 5):
            self.assertIn(f"{i}. Player {i}", frame)
            self.assertIn(f"LIFE {40-i}", frame)
            self.assertIn(f"Permanent {i}", frame)
        self.assertIn("Active: Player 3  Priority: Player 4", frame)
        self.assertNotIn("Stale legacy", frame)
        self.assertNotIn("LIFE 99", frame)
        self.assertEqual(data, before)

    def test_battlefield_follows_controller_instead_of_owner(self):
        data = snapshot()
        data["gameState"]["cards"]["c1"]["controllerId"] = "p4"
        views = player_views(data)
        self.assertEqual(views[0]["battlefield"], [])
        self.assertEqual({c["name"] for c in views[3]["battlefield"]}, {"Permanent 1", "Permanent 4"})

    def test_hidden_zones_and_face_down_names_are_not_rendered(self):
        data = snapshot()
        cards = data["gameState"]["cards"]
        for zone in ("Hand", "Library", "Command", "Graveyard", "Exile"):
            cards[zone] = {"name": f"SECRET {zone}", "controllerId": "p3",
                           "zone": {"zoneType": zone, "ownerId": "p3"}}
        cards["c3"].update(name="SECRET MORPH", isFaceDown=True)
        frame = render(data, deque())
        self.assertNotIn("SECRET", frame)
        self.assertIn("Face-down card", frame)

    def test_duplicate_cards_tapped_and_creature_stats(self):
        data = snapshot()
        cards = data["gameState"]["cards"]
        cards["c1"].update(name="Forest")
        cards["copy"] = copy.deepcopy(cards["c1"])
        cards["c2"].update(isTapped=True, power=3, toughness=4)
        frame = render(data, deque())
        self.assertIn("2x Forest", frame)
        self.assertIn("Permanent 2 3/4 [tapped]", frame)

    def test_wraps_long_battlefields_without_dropping_after_ten_cards(self):
        data = snapshot()
        for i in range(20):
            data["gameState"]["cards"][f"extra{i}"] = {
                "name": f"Card-{i:02}", "controllerId": "p4", "zone": {"zoneType": "Battlefield"}}
        for width in (40, 80, 240):
            frame = render(data, deque(), width)
            self.assertTrue(all(len(line) <= min(width, 100) for line in frame.splitlines()))
            for i in range(20):
                self.assertIn(f"Card-{i:02}", frame)

    def test_legacy_fallback_and_missing_seat_are_explicit(self):
        data = snapshot()
        data.pop("gameState")
        data["player1"].update(battlefield=[{"name": "Legacy land"}])
        frame = render(data, deque())
        self.assertIn("Legacy land", frame)
        self.assertEqual(frame.count("Public board state unavailable"), 3)

    def test_control_characters_cannot_change_layout(self):
        data = snapshot()
        data["players"][0]["name"] = "Name\r\n\t\x1b\x07\x9b"
        frame = render(data, deque(["event\r\n\x1b\x07"]))
        self.assertFalse(any(c in frame for c in "\r\t\x1b\x07\x9b"))
