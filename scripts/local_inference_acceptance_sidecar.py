#!/usr/bin/env python3
"""Opt-in local-only synthetic native-game fixture; never a human/Commander acceptance claim."""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import signal
import threading

from commander_gym.deck_package import ArtifactRef
from commander_gym.game_server_binding_lmstudio_sidecar import LocalBindingConfig, build_binding_lmstudio_sidecar, local_component_ref
from commander_gym.game_server_binding_openai_sidecar import BUILTIN_FORCED_PARAMETERLESS_COMPONENT_REF
from commander_gym.game_server_sidecar import GameServerSidecarConfig
from commander_gym.identity import Deck, Pilot, Binding
from commander_gym.lmstudio_pilot import load_lmstudio_pilot
from commander_gym.native_game_capture import NativeGameCapture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    settings = json.loads(args.config.read_text())
    root = Path(settings["fixtureRoot"])
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    inference = load_lmstudio_pilot(settings["inferenceConfig"]).config
    payload = {"argentum_player": {"deck": {"type": "Explicit", "cards": {"Plains": 8}}}}
    raw = json.dumps(payload, sort_keys=True)
    deck = Deck(deck_id="synthetic-eight-plains", revision="1", format_id="synthetic-native-test",
                deck_artifact=ArtifactRef(kind="decklist", artifact_id="synthetic-eight-plains", version="1",
                                          digest="sha256:" + hashlib.sha256(raw.encode()).hexdigest()))
    pilot = Pilot(pilot_id="local-qualification", revision="1", generalist_base=local_component_ref(inference),
                  deterministic_policy=BUILTIN_FORCED_PARAMETERLESS_COMPONENT_REF)
    binding = Binding(binding_id="local-qualification", revision="1", deck=deck.ref(), pilot=pilot.ref(),
                      metadata={"display": {"name": inference.model + " synthetic eight-Plains", "deck_name": "Synthetic eight-Plains"}})
    def write(name, value):
        path = root / name
        path.write_text(json.dumps(value, sort_keys=True)); path.chmod(0o600)
    write("deck.json", deck.to_dict()); write("payload.json", payload)
    write("pilot.json", pilot.to_dict()); write("binding.json", binding.to_dict())
    write("catalog.json", {"schema_version": 1, "bindings": ["binding.json"],
          "decks": [{"manifest": "deck.json", "payload": "payload.json"}],
          "pilots": ["pilot.json"], "deck_knowledge": [], "active_bindings": [binding.binding_id]})
    pins = dict(engine=settings["engineSha"], gym=settings["gymSha"], models=asdict(inference),
                decks=deck.to_dict(), bindings=binding.to_dict(), config="synthetic-eight-Plains-local-only", rng="native-recorded")
    capture = NativeGameCapture(Path(settings["recordingRoot"]), pins)
    server = build_binding_lmstudio_sidecar(LocalBindingConfig(
        GameServerSidecarConfig(Path(settings["tokenPath"]).read_text().strip(), port=settings["sidecarPort"]),
        inference, root / "catalog.json", root,
    ), game_capture=capture)
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    thread = threading.Thread(target=server.serve_forever)
    capture.start(); thread.start()
    print("Isolated local native acceptance sidecar ready", flush=True)
    try:
        stop.wait(600)  # Fixture cannot become a persistent service.
    finally:
        server.shutdown(); server.server_close(); thread.join()
        capture.scan(); capture.close()
        print(json.dumps({"captureErrors": capture.errors}), flush=True)


if __name__ == "__main__":
    main()
