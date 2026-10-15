import json
import unittest
from commander_gym.argentum_spectator_stream import handle_message, message, validate_url, watch, SpectatorProtocolError


class ArgentumSpectatorStreamTests(unittest.TestCase):
    def test_handshake_and_public_state_filter(self):
        got=[]
        assert handle_message('{"type":"connected","token":"observer"}',"g1",got.append)=="connected"
        handle_message('{"type":"spectatorStateUpdate","gameSessionId":"other","player1":{"life":20}}',"g1",got.append)
        handle_message('{"type":"spectatorStateUpdate","gameSessionId":"g1","player1":{"life":18}}',"g1",got.append)
        handle_message('{"type":"stateUpdate","gameSessionId":"g1","privateHand":["secret"]}',"g1",got.append)
        assert len(got)==1
        assert got[0]["player1"]["life"]==18

    def test_error_and_url_validation(self):
        with self.assertRaises(SpectatorProtocolError):
            handle_message('{"type":"error","code":"GAME_NOT_FOUND"}',"g1",lambda _:None)
        for url in ("https://example.com/game","ws://example.com/other","ws://x:y@example.com/game"):
            with self.assertRaises(ValueError): validate_url(url)
        validate_url("ws://127.0.0.1:8080/game")

    def test_connect_then_subscribe_then_snapshot(self):
        class FakeSocket:
            def __init__(self):
                self.sent=[]
                self.messages=iter([
                    '{"type":"connected","playerId":"observer","token":"abc"}',
                    '{"type":"spectatorStateUpdate","gameSessionId":"game-1","currentPhase":"MAIN1"}',
                ])
            def send(self, value): self.sent.append(json.loads(value))
            def recv(self): return next(self.messages)
            def close(self): pass
        sock=FakeSocket()
        result=[]
        watch("ws://localhost:8080/game","game-1",lambda x:result.append(x),
              ws_factory=lambda *a,**k:sock,stop=lambda:bool(result))
        assert [x["type"] for x in sock.sent]==["connect","spectateGame"]
        assert sock.sent[1]["gameSessionId"]=="game-1"
        assert result[0]["currentPhase"]=="MAIN1"
