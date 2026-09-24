from __future__ import annotations

import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from commander_gym.binding_resolver import BindingResolver
from commander_gym.deck_package import ArtifactRef
from commander_gym.game_server_bindings import GameServerBindingRegistry
from commander_gym.game_server_sidecar import GameServerSidecarConfig, GameServerSidecarServer
from commander_gym.identity import Binding, Deck, Pilot
from commander_gym.pilot import ArgentumActionChoice


class MarkerPilot:
    version = "1"

    def __init__(self, binding_id: str, created: list[str]) -> None:
        self.name = f"pilot-for-{binding_id}"
        self.binding_id = binding_id
        created.append(binding_id)

    def choose(self, observation):
        return ArgentumActionChoice(0)


def resolver_for_profiles(created: list[str]) -> BindingResolver:
    decks = []
    pilots = []
    bindings = []
    payloads = {}
    for index in range(2):
        artifact = ArtifactRef(
            kind="decklist",
            artifact_id=f"payload-{index}",
            version="v1",
            digest=f"sha256:{index + 10:064x}",
        )
        commander = f"Commander {index}"
        deck = Deck(
            deck_id=f"deck-{index}",
            revision="r1",
            format_id="commander",
            deck_artifact=artifact,
            format_metadata={"commander": commander},
        )
        pilot = Pilot(pilot_id=f"pilot-{index}", revision="r1")
        binding = Binding(
            binding_id=f"binding-{index}",
            revision="r1",
            deck=deck.ref(),
            pilot=pilot.ref(),
            metadata={
                "display": {
                    "name": f"Binding {index}",
                    "deck_name": f"Deck {index}",
                }
            },
        )
        payloads[artifact.artifact_id] = {
            "schema_version": 1,
            "deck_id": deck.deck_id,
            "name": f"Deck {index}",
            "commander": commander,
            "cards": {commander: 1, "Forest": 99},
        }
        decks.append(deck)
        pilots.append(pilot)
        bindings.append(binding)

    return BindingResolver(
        bindings=bindings,
        decks=decks,
        pilots=pilots,
        deck_payload_loader=lambda ref: payloads[ref.artifact_id],
        pilot_factory=lambda _pilot, binding: MarkerPilot(binding.binding_id, created),
    )


class GameServerBindingTests(unittest.TestCase):
    def setUp(self):
        self.created: list[str] = []
        self.registry = GameServerBindingRegistry(
            resolver_for_profiles(self.created),
            ["binding-0", "binding-1"],
        )
        config = GameServerSidecarConfig(token="local-secret")
        self.server = GameServerSidecarServer(
            ("127.0.0.1", 0),
            config,
            profiles=self.registry.profile_payloads(),
            profile_seat_factory=lambda player_id, profile_id: self.registry.create_seat(
                player_id, profile_id
            ),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def request(self, method: str, path: str, body=None, token="local-secret"):
        data = None if body is None else json.dumps(body).encode()
        headers = {"Authorization": f"Bearer {token}"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(
            self.base_url + path,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=2) as response:
                return response.status, json.loads(response.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_catalog_projects_binding_id_exact_deck_and_pilot_metadata(self):
        status, body = self.request("GET", "/v1/controller-profiles")
        self.assertEqual(status, 200)
        profiles = body["profiles"]
        self.assertEqual([profile["id"] for profile in profiles], ["binding-0", "binding-1"])
        self.assertEqual(
            profiles[0],
            {
                "id": "binding-0",
                "displayName": "Binding 0",
                "description": "pilot-for-binding-0 1",
                "deck": {
                    "label": "Deck 0",
                    "cards": {"Forest": 99},
                    "commander": "Commander 0",
                },
            },
        )

    def test_explicit_profile_resolves_exact_binding_pilot_and_is_stable_per_seat(self):
        initial_resolutions = len(self.created)
        body = {
            "playerId": "ai-seat",
            "profileId": "binding-1",
            "state": {"viewingPlayerId": "ai-seat"},
            "legalActions": [
                {
                    "actionType": "PassPriority",
                    "action": {"type": "PassPriority", "playerId": "ai-seat"},
                }
            ],
            "pendingDecision": None,
            "recentGameLog": [],
        }
        self.assertEqual(self.request("POST", "/v1/choose-action", body)[0], 200)
        self.assertEqual(self.request("POST", "/v1/choose-action", body)[0], 200)
        self.assertEqual(self.created[initial_resolutions:], ["binding-1"])

    def test_unknown_explicit_profile_fails_closed_without_generic_fallback(self):
        body = {
            "playerId": "ai-seat",
            "profileId": "missing-binding",
            "state": {"viewingPlayerId": "ai-seat"},
            "legalActions": [],
            "pendingDecision": None,
            "recentGameLog": [],
        }
        status, response = self.request("POST", "/v1/choose-action", body)
        self.assertEqual(status, 404)
        self.assertEqual(response, {"error": "unknown_profile"})

    def test_profile_catalog_requires_authentication(self):
        self.assertEqual(
            self.request("GET", "/v1/controller-profiles", token="wrong")[0],
            401,
        )


if __name__ == "__main__":
    unittest.main()
