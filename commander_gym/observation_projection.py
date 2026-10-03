"""Versioned, reversible model-facing compression of a masked Argentum view.

Only card fields with explicit JSON defaults are omitted. Native observations,
legal offers, decisions, and provenance remain in their original form.
"""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, Mapping


SPARSE_CARD_VIEW_VERSION = "argentum-seat-sparse-cards-v1"


def _empty_default(value: Any) -> bool:
    return value is None or value is False or (type(value) in (list, dict) and not value)


def _same_json(left: Any, right: Any) -> bool:
    return json.dumps(left, sort_keys=True, separators=(",", ":")) == json.dumps(
        right, sort_keys=True, separators=(",", ":"),
    )


def compact_seat_observation(observation: Mapping[str, Any]) -> dict[str, Any]:
    """Encode only common card defaults; fail closed to a complete wrapped view."""
    original = deepcopy(dict(observation))
    view = {"format": SPARSE_CARD_VIEW_VERSION, "cardDefaults": {}, "observation": original}
    state = original.get("state")
    cards = state.get("cards") if isinstance(state, Mapping) else None
    if not isinstance(cards, Mapping) or not cards or not all(
        isinstance(card, Mapping) for card in cards.values()
    ):
        return view
    card_list = list(cards.values())
    fields = set(card_list[0])
    if not all(set(card) == fields for card in card_list):
        return view

    defaults: dict[str, Any] = {}
    for field in sorted(fields):
        empty_values = [card[field] for card in card_list if _empty_default(card[field])]
        if empty_values and all(_same_json(value, empty_values[0]) for value in empty_values):
            defaults[field] = empty_values[0]
    if not defaults:
        return view
    sparse_cards = {
        card_id: {
            field: value for field, value in card.items()
            if field not in defaults or not _same_json(value, defaults[field])
        }
        for card_id, card in cards.items()
    }
    view["cardDefaults"] = defaults
    view["observation"]["state"]["cards"] = sparse_cards
    if not _same_json(expand_seat_observation(view), observation):
        raise ValueError("compact seat view did not round-trip exactly")
    return view


def expand_seat_observation(view: Mapping[str, Any]) -> dict[str, Any]:
    """Recreate the exact sanitized native view for offline contract checks."""
    if set(view) != {"format", "cardDefaults", "observation"} or view.get("format") != SPARSE_CARD_VIEW_VERSION:
        raise ValueError("unsupported compact seat view")
    defaults = view.get("cardDefaults")
    original = view.get("observation")
    if not isinstance(defaults, Mapping) or not isinstance(original, Mapping):
        raise ValueError("malformed compact seat view")
    if any(not isinstance(key, str) or not _empty_default(value) for key, value in defaults.items()):
        raise ValueError("card defaults must be explicit JSON empty values")
    result = deepcopy(dict(original))
    if not defaults:
        return result
    state = result.get("state")
    cards = state.get("cards") if isinstance(state, Mapping) else None
    if defaults and (not isinstance(cards, Mapping) or not all(
        isinstance(card, Mapping) for card in cards.values()
    )):
        raise ValueError("card defaults require native card objects")
    if isinstance(cards, Mapping):
        result["state"]["cards"] = {
            card_id: {**deepcopy(dict(defaults)), **card}
            for card_id, card in cards.items()
        }
    return result
