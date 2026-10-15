#!/usr/bin/env python3
"""Offline layout evidence only; never imports an SDK or dispatches a request."""

from copy import deepcopy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from commander_gym.cache_friendly_input import cache_friendly_observation_input
from commander_gym.observation_projection import compact_seat_observation, expand_seat_observation
from commander_gym.openai_responses_pilot import _without_live_routing


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def measure(observation):
    original = _without_live_routing(observation)
    compact = compact_seat_observation(original)
    assert expand_seat_observation(compact) == original
    messages = cache_friendly_observation_input(compact)
    prefix = json.loads(messages[0]["content"][0]["text"].split("\n", 1)[1])
    # Before v2 only knownDeck + hasGameLog were in the first prefix payload.
    legacy_prefix = {key: value for key, value in prefix.items()
                     if key in ("knownDeck", "hasGameLog")}
    current_live = json.loads(messages[-1]["content"][0]["text"].split("\n", 1)[1])
    legacy_live = deepcopy(current_live)
    for key in ("perspectivePlayerId", "gameId", "deckKnowledge"):
        if key in prefix:
            legacy_live["observation"][key] = prefix[key]
    return {
        "nativeSanitizedJsonBytes": len(encoded(original)),
        "existingSparseV1JsonBytes": len(encoded(compact)),
        "legacyStablePayloadBytes": len(encoded(legacy_prefix)),
        "v2StablePayloadBytes": len(encoded(prefix)),
        "legacyLivePayloadBytes": len(encoded(legacy_live)),
        "v2LivePayloadBytes": len(encoded(current_live)),
        "removedRedundantSerializationBytesPerCacheRequest": len(encoded(compact)),
    }


if __name__ == "__main__":
    fixture = Path(__file__).resolve().parents[1] / "fixtures/observations/cache_layout_held_out_v1.json"
    cases = json.loads(fixture.read_text())["cases"]
    print(json.dumps({
        "scope": "Offline synthetic byte counts, not provider tokens, cache hits, cost or latency.",
        "fixtures": {case["case"]: measure(case["observation"]) for case in cases},
    }, indent=2, sort_keys=True))
