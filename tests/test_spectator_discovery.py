import json
import unittest
from unittest.mock import patch
from commander_gym.spectator_discovery import candidates, follow, http_base

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
        with patch("commander_gym.spectator_discovery.urlopen",lambda url,timeout:Response({"liveGames":[{"gameSessionId":"private-1"}]})):
            assert candidates("ws://localhost:18080/game","private-lobby")==["private-1"]

    def test_follow_selects_and_returns_to_discovery(self):
        calls=[]; pending=[["game-a"],[]]
        def fake_candidates(url,lobby): return pending.pop(0)
        def fake_watch(url,gid,emit,stop): calls.append(gid)
        with patch("commander_gym.spectator_discovery.candidates",fake_candidates):
            follow("ws://localhost:18080/game",lambda _:None,watch_fn=fake_watch,
                   stop=lambda:len(calls)==1 and len(pending)==0,
                   pause_fn=lambda _:None,on_idle=lambda:None)
        assert calls==["game-a"]
