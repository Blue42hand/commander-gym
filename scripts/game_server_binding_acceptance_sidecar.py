#!/usr/bin/env python3
"""Synthetic Binding-first sidecar used only by the normal-Argentum #76 smoke.

The server itself is the production Binding/OpenAI sidecar assembled from an
instance-supplied canonical catalog. The only test seam is a deterministic mulligan
answer so the acceptance can reach the controller without making a model call; the
resolved adapter still contains the exact canonical Binding/Pilot selected by the
production loader.
"""

from __future__ import annotations

import argparse
import json
import threading
from pathlib import Path
from types import MethodType

from commander_gym.deck_package import ArtifactRef
from commander_gym.game_server_binding_openai_sidecar import (
    BindingOpenAIGameServerConfig,
    build_binding_openai_game_server_sidecar,
)
from commander_gym.game_server_openai_sidecar import OpenAIGameServerSidecarConfig
from commander_gym.identity import Binding, Deck, Pilot


class NoModelClient:
    class Responses:
        def create(self, **_kwargs):
            raise AssertionError("foundation smoke must not call the model")

    def __init__(self) -> None:
        self.responses = self.Responses()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def synthetic_catalog(root: Path) -> Path:
    deck_artifact = ArtifactRef(
        kind="decklist",
        artifact_id="synthetic-binding-decklist",
        version="r1",
        digest="sha256:synthetic-binding-decklist",
    )
    deck = Deck(
        deck_id="synthetic-binding-deck",
        revision="r1",
        format_id="commander",
        deck_artifact=deck_artifact,
        format_metadata={"commander": "Zetalpa, Primal Dawn"},
    )
    pilot = Pilot(pilot_id="synthetic-binding-pilot", revision="r1")
    binding = Binding(
        binding_id="seat-a",
        revision="r1",
        deck=deck.ref(),
        pilot=pilot.ref(),
        metadata={
            "display": {
                "name": "Synthetic Binding A",
                "deck_name": "Synthetic Zetalpa",
                "description": "Public synthetic #76 acceptance Binding",
            }
        },
    )

    write_json(root / "bindings" / "seat-a.json", binding.to_dict())
    write_json(root / "decks" / "deck.json", deck.to_dict())
    write_json(
        root / "decks" / "payload.json",
        {
            "commander": "Zetalpa, Primal Dawn",
            "cards": {"Zetalpa, Primal Dawn": 1, "Plains": 99},
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
            "active_bindings": ["seat-a"],
        },
    )
    return catalog


def append_event(path: Path, event: dict[str, object]) -> None:
    line = json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()

    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    evidence = args.evidence.resolve()
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text("", encoding="utf-8")
    catalog = synthetic_catalog(root)

    config = BindingOpenAIGameServerConfig(
        sidecar=OpenAIGameServerSidecarConfig(
            token=args.token,
            api_key="sk-synthetic-foundation-smoke",
            port=args.port,
        ),
        catalog_path=catalog,
        instance_root=root,
    )
    server = build_binding_openai_game_server_sidecar(config, client=NoModelClient())

    # Trace the production profile factory at the moment Argentum first calls the
    # selected controller. The deterministic keep is acceptance-only policy: this
    # smoke is about Binding/deck/pilot wiring, not gameplay quality or API behavior.
    original_factory = server._profile_seat_factory
    assert original_factory is not None
    lock = threading.Lock()

    def traced_factory(player_id: str, profile_id: str):
        adapter = original_factory(player_id, profile_id)
        canonical = adapter._pilot
        with lock:
            append_event(
                evidence,
                {
                    "event": "binding_seat_resolved",
                    "playerId": player_id,
                    "profileId": profile_id,
                    "bindingId": canonical.binding.artifact_id,
                    "bindingRevision": canonical.binding.revision,
                    "pilotId": canonical.pilot.artifact_id,
                    "pilotRevision": canonical.pilot.revision,
                },
            )
        adapter.decide_mulligan = MethodType(lambda _self, _message: True, adapter)
        return adapter

    server._profile_seat_factory = traced_factory
    print(
        f"Commander Gym synthetic Binding acceptance sidecar listening on 127.0.0.1:{server.server_port}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
