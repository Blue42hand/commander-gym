"""Lossless, opt-in layout for a seat-masked Responses observation.

Every request is built from the current observation. No provider conversation,
cross-call cursor, or unmasked engine event is used. Fixed history boundaries keep
completed prefixes identical as the native game log grows.
"""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, Mapping


_BREAKPOINTS = (32, 128, 512)
_MARK = {"mode": "explicit"}
CACHE_FRIENDLY_INPUT_VERSION = "argentum-seat-cache-v2"
# Copy only values already supplied by the current seat's resolver/projection.
# Perspective is in the first prefix block so different seats do not share a
# knowledge/history cache boundary, even when their visible deck lists match.
_STABLE_FIELDS = ("perspectivePlayerId", "gameId", "knownDeck", "deckKnowledge")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _message(label: str, value: Any, *, breakpoint: bool = False) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "input_text", "text": label + "\n" + _json(value)}
    if breakpoint:
        block["prompt_cache_breakpoint"] = dict(_MARK)
    return {"role": "user", "content": [block]}


def cache_friendly_observation_input(view: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Send the same view with deck and full masked history before live choices.

    The view may be the native sanitized observation or its reversible sparse-card
    wrapper. This function never infers state from an earlier provider response.
    """
    data = deepcopy(dict(view))
    observation = data.get("observation", data)
    if not isinstance(observation, dict):
        raise ValueError("model observation must be an object")
    state = observation.get("state")
    if state is not None and not isinstance(state, dict):
        raise ValueError("model state must be an object")
    state = state if state is not None else {}
    log = state.get("gameLog", [])
    if not isinstance(log, list):
        raise ValueError("masked gameLog must be an array")

    stable = {field: observation.pop(field) for field in _STABLE_FIELDS
              if field in observation}
    if "gameLog" in state:
        stable["hasGameLog"] = True
        state.pop("gameLog")
    messages = [_message(f"Seat-authorized identity and deck knowledge ({CACHE_FRIENDLY_INPUT_VERSION}, JSON):", stable,
                         breakpoint=True)]
    start = 0
    for end in (*_BREAKPOINTS, len(log)):
        if end <= start or end > len(log):
            continue
        messages.append(_message(
            f"Seat-masked gameLog events [{start}:{end}] (JSON array):",
            log[start:end], breakpoint=end in _BREAKPOINTS,
        ))
        start = end
    messages.append(_message(
        "Return one JSON object for this observation. Combine the preceding "
        "deck knowledge and ordered gameLog events with this current JSON view:",
        data,
    ))
    return messages


def reconstruct_cache_friendly_observation(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Decode the layout for offline information-equivalence checks."""
    if len(messages) < 2:
        raise ValueError("cache-friendly input is incomplete")
    texts = [message["content"][0]["text"] for message in messages]
    stable = json.loads(texts[0].split("\n", 1)[1])
    data = json.loads(texts[-1].split("\n", 1)[1])
    observation = data.get("observation", data)
    for field in _STABLE_FIELDS:
        if field in stable:
            observation[field] = stable[field]
    if stable.get("hasGameLog"):
        observation["state"]["gameLog"] = [
            event for part in texts[1:-1]
            for event in json.loads(part.split("\n", 1)[1])
        ]
    return data
