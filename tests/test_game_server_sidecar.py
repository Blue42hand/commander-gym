from __future__ import annotations

import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from commander_gym.game_server_seat import GameServerSeatAdapter
from commander_gym.game_server_sidecar import (
    GameServerSidecarConfig,
    GameServerSidecarConfigurationError,
    GameServerSidecarServer,
)
from commander_gym.pilot import ArgentumActionChoice, ArgentumDecisionChoice


class ScriptedPilot:
    name = "sidecar-proof"
    version = "1"

    def __init__(self, *choices):
        self.choices = list(choices)
        self.observations = []

    def choose(self, observation):
        self.observations.append(observation)
        choice = self.choices.pop(0)
        if isinstance(choice, Exception):
            raise choice
        return choice


class GameServerSidecarTests(unittest.TestCase):
    def setUp(self):
        self.pilot = ScriptedPilot(
            ArgentumActionChoice(0),
            ArgentumDecisionChoice(
                {"type": "CardsSelectedResponse", "decisionId": "bottom-cards:ai", "selectedCards": ["b"]}
            ),
        )
        adapter = GameServerSeatAdapter(self.pilot, "ai")
        config = GameServerSidecarConfig(token="local-secret")
        self.server = GameServerSidecarServer(("127.0.0.1", 0), config, {"ai": adapter})
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def post(self, path, body, token="local-secret"):
        request = Request(
            self.base_url + path,
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=2) as response:
                return response.status, json.loads(response.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_action_round_trip_uses_only_masked_callback_values(self):
        body = {
            "playerId": "ai",
            "state": {"viewingPlayerId": "ai", "hand": ["mountain"]},
            "legalActions": [
                {"actionType": "PassPriority", "action": {"type": "PassPriority", "playerId": "ai"}}
            ],
            "pendingDecision": None,
            "recentGameLog": ["Human played a land"],
        }
        status, response = self.post("/v1/choose-action", body)

        self.assertEqual(status, 200)
        self.assertEqual(response["kind"], "action")
        self.assertEqual(response["action"], body["legalActions"][0]["action"])
        self.assertEqual(response["params"], {})
        self.assertEqual(self.pilot.observations[0]["state"], body["state"])
        self.assertNotIn("snapshot", self.pilot.observations[0])

    def test_parameterized_action_round_trip_defers_completion_to_native_edge(self):
        self.server.seats["ai"] = GameServerSeatAdapter(
            ScriptedPilot(
                ArgentumActionChoice(
                    0,
                    params={
                        "attackers": {"creature-1": "human"},
                        "xValue": 3,
                    },
                )
            ),
            "ai",
        )
        body = {
            "playerId": "ai",
            "state": {"viewingPlayerId": "ai"},
            "legalActions": [
                {
                    "actionType": "DeclareAttackers",
                    "action": {"type": "DeclareAttackers", "playerId": "ai", "attackers": {}},
                }
            ],
            "pendingDecision": None,
            "recentGameLog": [],
        }

        status, response = self.post("/v1/choose-action", body)

        self.assertEqual(status, 200)
        self.assertEqual(response["kind"], "action")
        self.assertEqual(
            response["params"],
            {"attackers": {"creature-1": "human"}, "xValue": 3},
        )
        self.assertEqual(response["action"], body["legalActions"][0]["action"])

    def test_mulligan_bottom_cards_and_fail_closed_provider_path(self):
        bottom_pilot = ScriptedPilot(
            ArgentumDecisionChoice(
                {"type": "CardsSelectedResponse", "decisionId": "bottom-cards:ai", "selectedCards": ["b"]}
            )
        )
        self.server.seats["ai"] = GameServerSeatAdapter(bottom_pilot, "ai")
        status, response = self.post(
            "/v1/choose-bottom-cards",
            {
                "playerId": "ai",
                "bottomCards": {
                    "hand": ["a", "b"],
                    "cardsToPutOnBottom": 1,
                },
            },
        )
        self.assertEqual((status, response), (200, {"cardIds": ["b"]}))

        failing = GameServerSeatAdapter(ScriptedPilot(RuntimeError("offline")), "broken")
        self.server.seats["broken"] = failing
        status, response = self.post(
            "/v1/decide-mulligan",
            {"playerId": "broken", "mulligan": {"hand": ["a"]}},
        )
        self.assertEqual(status, 503)
        self.assertEqual(response["error"], "pilot_failure")

    def test_lazy_factory_binds_generated_player_id_once(self):
        self.server.shutdown()
        self.server.server_close()

        created = []
        pilots = {}

        def factory(player_id):
            created.append(player_id)
            pilot = ScriptedPilot(ArgentumActionChoice(0), ArgentumActionChoice(0))
            pilots[player_id] = pilot
            return GameServerSeatAdapter(pilot, player_id)

        config = GameServerSidecarConfig(token="local-secret")
        self.server = GameServerSidecarServer(
            ("127.0.0.1", 0),
            config,
            seat_factory=factory,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

        body = {
            "playerId": "ai-generated-123",
            "state": {"viewingPlayerId": "ai-generated-123"},
            "legalActions": [
                {
                    "actionType": "PassPriority",
                    "action": {"type": "PassPriority", "playerId": "ai-generated-123"},
                }
            ],
            "pendingDecision": None,
            "recentGameLog": [],
        }
        self.assertEqual(self.post("/v1/choose-action", body)[0], 200)
        self.assertEqual(self.post("/v1/choose-action", body)[0], 200)
        self.assertEqual(created, ["ai-generated-123"])
        self.assertEqual(len(pilots["ai-generated-123"].observations), 2)

    def test_rejects_snapshot_unknown_fields_and_cross_seat_state(self):
        base = {
            "playerId": "ai",
            "state": {"viewingPlayerId": "ai"},
            "legalActions": [],
            "pendingDecision": None,
            "recentGameLog": [],
        }
        for addition in ({"snapshot": {"hands": "all"}}, {"authoritativeState": {}}):
            with self.subTest(addition=addition):
                status, _ = self.post("/v1/choose-action", base | addition)
                self.assertEqual(status, 422)

        status, _ = self.post(
            "/v1/choose-action",
            base | {"state": {"viewingPlayerId": "human"}},
        )
        self.assertEqual(status, 422)

    def test_requires_authentication_and_loopback_binding(self):
        status, _ = self.post("/v1/choose-action", {}, token="wrong")
        self.assertEqual(status, 401)
        with self.assertRaises(GameServerSidecarConfigurationError):
            GameServerSidecarConfig(token="x", bind_host="0.0.0.0")


if __name__ == "__main__":
    unittest.main()
