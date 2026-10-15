"""Optional JSON ordering of the complete current seat view; no cached state.

Stable seat identity and card data precede changing offers. This permits prefix
reuse when supported by the server, but neither requests nor assumes a cache hit.
"""
from collections.abc import Mapping
import json


def local_observation_input(view: Mapping) -> str:
    def ordered(value, first=()):
        return {key: value[key] for key in (*first, *sorted(value))
                if key in value}  # Reassigning preserves the first insertion.

    data = json.loads(json.dumps(dict(view), sort_keys=True, allow_nan=False))
    wrapped = data.get("format") == "argentum-seat-sparse-cards-v1"
    observation = dict(data["observation"] if wrapped else data)
    state = observation.get("state")
    if isinstance(state, Mapping):
        # Every field/value remains present. Arrays keep native order.
        observation["state"] = ordered(state, ("cards", "gameLog"))
    observation = ordered(observation, ("perspectivePlayerId", "gameId", "knownDeck",
                                        "deckKnowledge", "state"))
    if wrapped:
        data["observation"] = observation
        observation = ordered(data, ("format", "cardDefaults", "observation"))
    return json.dumps(observation, separators=(",", ":"), allow_nan=False)
