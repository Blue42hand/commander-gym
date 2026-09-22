from __future__ import annotations

import json
import socket
import tempfile
import unittest
from pathlib import Path

from commander_gym.game_server_openai_sidecar import (
    DEFAULT_OPENAI_GAME_SERVER_MODEL,
    JsonlSeatProvenanceWriter,
    OpenAIGameServerSidecarConfig,
    OpenAIGameServerSidecarConfigurationError,
    build_openai_game_server_sidecar,
    openai_game_server_sidecar_from_environment,
)


class FakeResponse:
    def __init__(self, payload):
        self.id = "resp-sidecar"
        self.model = DEFAULT_OPENAI_GAME_SERVER_MODEL
        self.status = "completed"
        self.error = None
        self.incomplete_details = None
        self.output_text = json.dumps(payload)
        self.usage = {"input_tokens": 10, "output_tokens": 5}


class FakeResponses:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return FakeResponse(
            {
                "channel": "action",
                "semanticId": "argentum-action-v1:play-land",
                "params": {},
            }
        )


class FakeClient:
    def __init__(self):
        self.responses = FakeResponses()


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class OpenAIGameServerSidecarTests(unittest.TestCase):
    def environment(self, **overrides):
        values = {
            "COMMANDER_GYM_SIDECAR_TOKEN": "sidecar-secret",
            "OPENAI_API_KEY": "sk-test-secret",
        }
        values.update(overrides)
        return values

    def test_environment_defaults_to_luna_and_loopback(self):
        config = openai_game_server_sidecar_from_environment(self.environment())

        self.assertEqual(config.model, "gpt-5.6-luna")
        self.assertEqual(config.bind_host, "127.0.0.1")
        self.assertEqual(config.port, 8083)
        self.assertEqual(config.timeout, 60.0)
        self.assertEqual(config.max_attempts, 2)

    def test_requires_secrets_and_keeps_them_out_of_repr(self):
        with self.assertRaisesRegex(
            OpenAIGameServerSidecarConfigurationError,
            "COMMANDER_GYM_SIDECAR_TOKEN",
        ):
            openai_game_server_sidecar_from_environment({"OPENAI_API_KEY": "key"})

        with self.assertRaisesRegex(
            OpenAIGameServerSidecarConfigurationError,
            "OPENAI_API_KEY",
        ):
            openai_game_server_sidecar_from_environment(
                {"COMMANDER_GYM_SIDECAR_TOKEN": "token"}
            )

        config = openai_game_server_sidecar_from_environment(self.environment())
        rendered = repr(config)
        self.assertNotIn("sidecar-secret", rendered)
        self.assertNotIn("sk-test-secret", rendered)

    def test_rejects_non_loopback_bind_and_invalid_numeric_configuration(self):
        with self.assertRaisesRegex(
            OpenAIGameServerSidecarConfigurationError,
            "loopback",
        ):
            openai_game_server_sidecar_from_environment(
                self.environment(COMMANDER_GYM_SIDECAR_HOST="0.0.0.0")
            )

        with self.assertRaisesRegex(
            OpenAIGameServerSidecarConfigurationError,
            "COMMANDER_GYM_SIDECAR_PORT must be an integer",
        ):
            openai_game_server_sidecar_from_environment(
                self.environment(COMMANDER_GYM_SIDECAR_PORT="nope")
            )

    def test_lazily_binds_one_stable_luna_pilot_per_argentum_player_id(self):
        client = FakeClient()
        provenance = []
        config = OpenAIGameServerSidecarConfig(
            token="sidecar-secret",
            api_key="sk-test-secret",
            port=free_port(),
        )
        server = build_openai_game_server_sidecar(
            config,
            client=client,
            provenance_sink=lambda player_id, event: provenance.append((player_id, event)),
        )
        try:
            seat_one = server.resolve_seat("ai-one")
            self.assertIs(seat_one, server.resolve_seat("ai-one"))
            seat_two = server.resolve_seat("ai-two")
            self.assertIsNot(seat_one, seat_two)

            result = seat_one.choose_action(
                {"viewingPlayerId": "ai-one"},
                [
                    {
                        "kind": "PassPriority",
                        "actionType": "PassPriority",
                        "semanticId": "argentum-action-v1:pass",
                        "description": "Pass priority",
                        "affordable": True,
                        "action": {"type": "PassPriority", "playerId": "ai-one"},
                    },
                    {
                        "kind": "PlayLand",
                        "actionType": "PlayLand",
                        "semanticId": "argentum-action-v1:play-land",
                        "description": "Play a land",
                        "affordable": True,
                        "action": {
                            "type": "PlayLand",
                            "playerId": "ai-one",
                            "cardId": "land-1",
                        },
                    },
                ],
                None,
                (),
            )

            self.assertEqual(result.action_id, 1)
            self.assertEqual(result.action["type"], "PlayLand")
            self.assertEqual(len(client.responses.calls), 1)
            self.assertEqual(
                client.responses.calls[0]["model"],
                DEFAULT_OPENAI_GAME_SERVER_MODEL,
            )
            self.assertEqual(provenance[0][0], "ai-one")
            self.assertEqual(provenance[0][1].callback, "chooseAction")
            self.assertNotIn("snapshot", provenance[0][1].observation)
        finally:
            server.server_close()

    def test_jsonl_provenance_is_tagged_with_the_argentum_player_id(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.jsonl"
            writer = JsonlSeatProvenanceWriter(path)
            config = OpenAIGameServerSidecarConfig(
                token="sidecar-secret",
                api_key="sk-test-secret",
                port=free_port(),
                provenance_path=path,
            )
            server = build_openai_game_server_sidecar(config, client=FakeClient())
            try:
                seat = server.resolve_seat("ai-provenance")
                seat.choose_action(
                    {"viewingPlayerId": "ai-provenance"},
                    [
                        {
                            "kind": "PassPriority",
                            "actionType": "PassPriority",
                            "semanticId": "argentum-action-v1:pass",
                            "description": "Pass priority",
                            "affordable": True,
                            "action": {
                                "type": "PassPriority",
                                "playerId": "ai-provenance",
                            },
                        },
                        {
                            "kind": "PlayLand",
                            "actionType": "PlayLand",
                            "semanticId": "argentum-action-v1:play-land",
                            "description": "Play a land",
                            "affordable": True,
                            "action": {
                                "type": "PlayLand",
                                "playerId": "ai-provenance",
                                "cardId": "land-1",
                            },
                        },
                    ],
                    None,
                    (),
                )
            finally:
                server.server_close()

            records = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["playerId"], "ai-provenance")
            self.assertEqual(records[0]["callback"], "chooseAction")
            self.assertNotIn("snapshot", records[0]["observation"])


if __name__ == "__main__":
    unittest.main()
