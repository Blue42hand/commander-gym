from __future__ import annotations

import json
import socket
import tempfile
import unittest
from pathlib import Path

from commander_gym.deck_package import ArtifactRef
from commander_gym.game_server_binding_openai_sidecar import (
    BindingOpenAIGameServerConfig,
    binding_openai_game_server_config_from_environment,
    build_binding_openai_game_server_sidecar,
)
from commander_gym.game_server_openai_sidecar import (
    OpenAIGameServerSidecarConfig,
    OpenAIGameServerSidecarConfigurationError,
)
from commander_gym.game_server_sidecar import UnknownProfileError
from commander_gym.identity import Binding, Deck, Pilot


class FakeClient:
    class Responses:
        def create(self, **kwargs):  # pragma: no cover - forced-pass smoke avoids a model wake.
            raise AssertionError("synthetic catalog smoke must not call the model")

    def __init__(self):
        self.responses = self.Responses()


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def synthetic_catalog(root: Path, *, active_binding: str = "seat-a") -> Path:
    deck_artifact = ArtifactRef(
        kind="decklist",
        artifact_id="synthetic-decklist",
        version="r1",
        digest="sha256:synthetic-decklist",
    )
    deck = Deck(
        deck_id="synthetic-deck",
        revision="r1",
        format_id="commander",
        deck_artifact=deck_artifact,
        format_metadata={"commander": "Synthetic Commander"},
    )
    pilot = Pilot(pilot_id="synthetic-openai-pilot", revision="r1")
    binding = Binding(
        binding_id="seat-a",
        revision="r1",
        deck=deck.ref(),
        pilot=pilot.ref(),
        metadata={
            "display": {
                "name": "Synthetic Binding",
                "deck_name": "Synthetic Deck",
            }
        },
    )

    write_json(root / "bindings" / "seat-a.json", binding.to_dict())
    write_json(root / "decks" / "deck.json", deck.to_dict())
    write_json(
        root / "decks" / "payload.json",
        {
            "commander": "Synthetic Commander",
            "cards": {
                "Synthetic Commander": 1,
                "Forest": 99,
            },
        },
    )
    write_json(root / "pilots" / "pilot.json", pilot.to_dict())
    catalog = root / "instance" / "bindings.json"
    write_json(
        catalog,
        {
            "schema_version": 1,
            "bindings": ["bindings/seat-a.json"],
            "decks": [
                {
                    "manifest": "decks/deck.json",
                    "payload": "decks/payload.json",
                }
            ],
            "pilots": ["pilots/pilot.json"],
            "deck_knowledge": [],
            "active_bindings": [active_binding],
        },
    )
    return catalog


class BindingOpenAIGameServerSidecarTests(unittest.TestCase):
    def test_environment_accepts_instance_supplied_catalog_and_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = synthetic_catalog(root)
            config = binding_openai_game_server_config_from_environment(
                {
                    "COMMANDER_GYM_SIDECAR_TOKEN": "sidecar-secret",
                    "OPENAI_API_KEY": "sk-test-secret",
                    "COMMANDER_GYM_BINDING_CATALOG": str(catalog),
                    "COMMANDER_GYM_INSTANCE_ROOT": str(root),
                }
            )
            self.assertEqual(config.catalog_path, catalog.resolve())
            self.assertEqual(config.instance_root, root.resolve())

    def test_binding_catalog_configures_exact_deck_and_exact_pilot_without_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = synthetic_catalog(root)
            config = BindingOpenAIGameServerConfig(
                sidecar=OpenAIGameServerSidecarConfig(
                    token="sidecar-secret",
                    api_key="sk-test-secret",
                    port=free_port(),
                ),
                catalog_path=catalog,
                instance_root=root,
            )
            server = build_binding_openai_game_server_sidecar(
                config,
                client=FakeClient(),
            )
            try:
                self.assertEqual(len(server.controller_profiles), 1)
                profile = server.controller_profiles[0]
                self.assertEqual(profile["id"], "seat-a")
                self.assertEqual(profile["deck"]["commander"], "Synthetic Commander")
                self.assertEqual(profile["deck"]["cards"], {"Forest": 99})

                seat = server.resolve_seat("ai-one", "seat-a")
                result = seat.choose_action(
                    {"viewingPlayerId": "ai-one"},
                    [
                        {
                            "kind": "PassPriority",
                            "actionType": "PassPriority",
                            "semanticId": "argentum-action-v1:pass",
                            "description": "Pass priority",
                            "affordable": True,
                            "action": {
                                "type": "PassPriority",
                                "playerId": "ai-one",
                            },
                        }
                    ],
                    None,
                    (),
                )
                self.assertEqual(result.action["type"], "PassPriority")
                self.assertEqual(result.metadata["binding"]["artifact_id"], "seat-a")
                self.assertEqual(
                    result.metadata["pilot"]["artifact_id"],
                    "synthetic-openai-pilot",
                )

                with self.assertRaises(UnknownProfileError):
                    server.resolve_seat("ai-one", "stale-binding")
            finally:
                server.server_close()

    def test_unresolvable_active_binding_fails_at_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = synthetic_catalog(root, active_binding="missing-binding")
            config = BindingOpenAIGameServerConfig(
                sidecar=OpenAIGameServerSidecarConfig(
                    token="sidecar-secret",
                    api_key="sk-test-secret",
                    port=free_port(),
                ),
                catalog_path=catalog,
                instance_root=root,
            )
            with self.assertRaisesRegex(
                OpenAIGameServerSidecarConfigurationError,
                "missing-binding",
            ):
                build_binding_openai_game_server_sidecar(config, client=FakeClient())

    def test_catalog_path_cannot_escape_instance_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            elsewhere = root.parent / "outside-bindings.json"
            try:
                elsewhere.write_text("{}", encoding="utf-8")
                with self.assertRaisesRegex(
                    OpenAIGameServerSidecarConfigurationError,
                    "below COMMANDER_GYM_INSTANCE_ROOT",
                ):
                    binding_openai_game_server_config_from_environment(
                        {
                            "COMMANDER_GYM_SIDECAR_TOKEN": "sidecar-secret",
                            "OPENAI_API_KEY": "sk-test-secret",
                            "COMMANDER_GYM_BINDING_CATALOG": str(elsewhere),
                            "COMMANDER_GYM_INSTANCE_ROOT": str(root),
                        }
                    )
            finally:
                elsewhere.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
