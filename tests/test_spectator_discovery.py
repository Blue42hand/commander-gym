import json
import unittest
from unittest.mock import patch
from commander_gym.spectator_discovery import candidates, follow, http_base, lobby_candidates

class Response:
    def __init__(self, data): self.data=data
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def read(self): return json.dumps(self.data).encode("utf-8")


class SpectatorDiscoveryTests(unittest.TestCase):
    def test_http_base(self):
        assert http_base("ws://127.0.0.1:18080/game")=="http://127.0.0.1:18080"
        assert http_base("wss://example.org/game")=="https://example.org"

    def test_public_game_discovery(self):
        def fake_urlopen(url,timeout):
            if "quick-games" in url: return Response([{"gameSessionId":"g2"}])
            return Response([{"gameSessionId":"g1"},{"gameSessionId":"g2"}])
        with patch("commander_gym.spectator_discovery.urlopen",fake_urlopen):
            assert candidates("ws://localhost:18080/game")==["g1","g2"]

    def test_private_lobby_discovery(self):
        with patch("commander_gym.spectator_discovery.lobby_candidates", return_value=["private-1"]) as discover:
            self.assertEqual(candidates("ws://localhost:18080/game", "private-lobby"), ["private-1"])
            discover.assert_called_once_with("ws://localhost:18080/game", "private-lobby")

    def discover_lobby(self, messages, state="TOURNAMENT_ACTIVE"):
        from unittest.mock import Mock
        socket = Mock()
        socket.recv.side_effect = [json.dumps(msg) for msg in messages]
        factory = Mock(return_value=socket)
        with patch("commander_gym.spectator_discovery.urlopen", return_value=Response({"state": state})):
            result = lobby_candidates("ws://localhost:18080/game", "private-lobby", ws_factory=factory)
        return result, socket, factory

    def test_private_pod_ignores_unrelated_messages(self):
        result, socket, _ = self.discover_lobby([
            {"type": "connected", "token": "observer"},
            {"type": "onlinePlayersCount", "count": 4},
            {"type": "freeForAllGameStarting", "lobbyId": "other", "gameSessionId": "wrong"},
            {"type": "freeForAllGameStarting", "lobbyId": "private-lobby", "gameSessionId": "pod-1"},
        ])
        self.assertEqual(result, ["pod-1"])
        self.assertEqual([json.loads(call.args[0]) for call in socket.send.call_args_list], [
            {"type": "connect", "playerName": "Commander Gym Lobby Spectator"},
            {"type": "joinLobby", "lobbyId": "private-lobby"},
        ])
        socket.close.assert_called_once()

    def test_waiting_lobby_never_adds_a_player(self):
        for state in ("WAITING_FOR_PLAYERS", "TOURNAMENT_COMPLETE", "DECK_BUILDING"):
            with self.subTest(state=state):
                result, _, factory = self.discover_lobby([], state)
                self.assertEqual(result, [])
                factory.assert_not_called()

    def test_bracket_matches_are_sorted_and_deduplicated(self):
        result, _, _ = self.discover_lobby([
            {"type": "connected"},
            {"type": "activeMatches", "lobbyId": "private-lobby", "matches": [
                {"gameSessionId": "g2"}, {"gameSessionId": "g1"}, {"gameSessionId": "g2"}, {}]},
        ])
        self.assertEqual(result, ["g1", "g2"])

    def test_lobby_error_closes_connection(self):
        from commander_gym.argentum_spectator_stream import SpectatorProtocolError
        from unittest.mock import Mock
        socket = Mock()
        socket.recv.return_value = json.dumps({"type": "error", "code": "GAME_NOT_FOUND"})
        with patch("commander_gym.spectator_discovery.urlopen", return_value=Response({"state": "TOURNAMENT_ACTIVE"})):
            with self.assertRaises(SpectatorProtocolError):
                lobby_candidates("ws://localhost:18080/game", "private-lobby", ws_factory=lambda *a, **k: socket)
        socket.close.assert_called_once()

    def test_follow_reports_discovery_error_and_retries(self):
        errors = []
        pauses = []
        with patch("commander_gym.spectator_discovery.candidates", side_effect=OSError("unavailable")):
            follow("ws://localhost:18080/game", lambda _: None,
                   stop=lambda: bool(pauses), pause_fn=pauses.append, on_error=errors.append)
        self.assertEqual(len(errors), 1)
        self.assertEqual(pauses, [4])

    def test_follow_selects_and_returns_to_discovery(self):
        calls=[]; pending=[["game-a"],[]]
        def fake_candidates(url,lobby): return pending.pop(0)
        def fake_watch(url,gid,emit,stop): calls.append(gid)
        with patch("commander_gym.spectator_discovery.candidates",fake_candidates):
            follow("ws://localhost:18080/game",lambda _:None,watch_fn=fake_watch,
                   stop=lambda:len(calls)==1 and len(pending)==0,
                   pause_fn=lambda _:None,on_idle=lambda:None)
        assert calls==["game-a"]
