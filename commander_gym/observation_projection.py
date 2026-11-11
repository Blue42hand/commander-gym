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


def compact_model_observation(observation: Mapping[str, Any]) -> dict[str, Any]:
    """Add a lossless column table for the public deck summary."""
    import os
    view = compact_seat_observation(observation)
    deck = view['observation'].get('state', {}).get('deck')
    if not isinstance(deck, list) or not deck or not all(isinstance(row, dict) for row in deck):
        return view
    columns = sorted(deck[0])
    if not all(set(row) == set(columns) for row in deck):
        return view
    rows = [[row[key] for key in columns] for row in deck]
    prefixes = {}
    for index, key in enumerate(columns):
        values = [row[index] for row in rows]
        if all(isinstance(value, str) for value in values):
            prefix = os.path.commonprefix(values)
            if len(prefix) >= 8:
                prefixes[key] = prefix
                for row in rows:
                    row[index] = row[index][len(prefix):]
    view['format'] = 'argentum-seat-deck-table-v2'
    view['deckTable'] = {'columns': columns, 'stringPrefixes': prefixes}
    view['observation']['state']['deck'] = rows
    if not _same_json(expand_model_observation(view), observation):
        raise ValueError('deck table did not round-trip exactly')
    return view


def expand_model_observation(view: Mapping[str, Any]) -> dict[str, Any]:
    if view.get('format') == SPARSE_CARD_VIEW_VERSION:
        return expand_seat_observation(view)
    if view.get('format') != 'argentum-seat-deck-table-v2':
        raise ValueError('unsupported model observation')
    restored = deepcopy(dict(view))
    table = restored.pop('deckTable')
    columns = table['columns']
    rows = restored['observation']['state']['deck']
    if len(set(columns)) != len(columns) or any(len(row) != len(columns) for row in rows):
        raise ValueError('malformed deck table')
    restored['observation']['state']['deck'] = [
        {key: table['stringPrefixes'].get(key, '') + value
         if key in table['stringPrefixes'] else value
         for key, value in zip(columns, row)} for row in rows
    ]
    restored['format'] = SPARSE_CARD_VIEW_VERSION
    return expand_seat_observation(restored)


def compact_inference_observation(observation: Mapping[str, Any]) -> dict[str, Any]:
    """Losslessly encode repeated action defaults and chronological log field names."""
    view = compact_model_observation(observation)
    native = view['observation']
    actions = native.get('legalActions')
    defaults = {}
    if isinstance(actions, list) and actions and all(isinstance(a, dict) for a in actions):
        fields = set.intersection(*(set(a) for a in actions))
        for field in sorted(fields):
            empty = [a[field] for a in actions if _empty_default(a[field])]
            if empty and all(_same_json(v, empty[0]) for v in empty):
                defaults[field] = empty[0]
        native['legalActions'] = [
            {k: v for k, v in a.items() if k not in defaults or not _same_json(v, defaults[k])}
            for a in actions]
    columns = []
    state = native.get('state')
    log = state.get('gameLog') if isinstance(state, dict) else None
    if isinstance(log, list) and log and all(isinstance(event, dict) for event in log):
        rows = []
        for event in log:
            keys = (['type'] if 'type' in event else []) + sorted(k for k in event if k != 'type')
            if keys not in columns:
                columns.append(keys)
            rows.append([columns.index(keys), *[event[k] for k in keys]])
        state['gameLog'] = rows
    if not defaults and not columns:
        return view
    view['baseFormat'] = view['format']
    view['format'] = 'argentum-seat-tables-v3'
    view['actionDefaults'] = defaults
    view['gameLogColumns'] = columns
    if not _same_json(expand_inference_observation(view), observation):
        raise ValueError('inference table did not round-trip exactly')
    return view


def expand_inference_observation(view: Mapping[str, Any]) -> dict[str, Any]:
    if view.get('format') != 'argentum-seat-tables-v3':
        return expand_model_observation(view)
    restored = deepcopy(dict(view))
    restored['format'] = restored.pop('baseFormat')
    defaults = restored.pop('actionDefaults')
    columns = restored.pop('gameLogColumns')
    if not isinstance(defaults, dict) or any(not _empty_default(v) for v in defaults.values()):
        raise ValueError('invalid action defaults')
    native = restored['observation']
    if defaults:
        native['legalActions'] = [{**deepcopy(defaults), **a} for a in native['legalActions']]
    if columns:
        log = []
        for row in native['state']['gameLog']:
            if (not isinstance(row, list) or not row or type(row[0]) is not int
                    or not 0 <= row[0] < len(columns) or len(row) != len(columns[row[0]]) + 1):
                raise ValueError('invalid game log row')
            keys = columns[row[0]]
            if len(set(keys)) != len(keys):
                raise ValueError('duplicate game log field')
            log.append(dict(zip(keys, row[1:])))
        native['state']['gameLog'] = log
    return expand_model_observation(restored)
