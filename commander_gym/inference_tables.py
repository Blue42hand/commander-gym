"""Request-local, reversible tables for already masked model observations.

This is a wire encoding only: it neither selects actions nor summarizes history.
Native identifiers and response schemas retain their original values.
"""

from collections import Counter
from copy import deepcopy
import json


FORMAT = "argentum-seat-shared-v4"
REFERENCE = "$gymValue"
INSTRUCTIONS = (
    "\nFor argentum-seat-shared-v4, first expand sharedValues: an object containing "
    "only $gymValue is a zero-based index into that table. Table entries can refer "
    "only to earlier entries. Expand references throughout view and its tables. "
    "In view.observation.legalActions, action=[templateIndex,overrides] means merge "
    "actionTemplates[templateIndex] with overrides. In view.observation.state.cards, "
    "each value [shapeIndex,values...] uses cardColumns[shapeIndex] as its field names. "
    "Empty actionTemplates/cardColumns mean those fields retain their original form. "
    "Then interpret view using its own format and defaults. All values and history "
    "are preserved. Return original native semanticId strings, never table references."
)


def _key(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _size(value):
    return len(json.dumps(value, separators=(",", ":"), allow_nan=False).encode())


def _message_size(value):
    # A compact observation is a JSON string inside the request's JSON envelope.
    return len(json.dumps(json.dumps(value, separators=(",", ":")), ensure_ascii=False).encode())


def _walk(value):
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def compact_shared_view(base):
    """Use tables only when their complete envelope is smaller than the base view."""
    # Reserve the reference syntax without ever reinterpreting a native object.
    if any(isinstance(v, dict) and REFERENCE in v for v in _walk(base)):
        return deepcopy(base)
    view = deepcopy(base)
    native = view['observation']
    templates = []
    actions = native.get('legalActions')
    if isinstance(actions, list):
        groups = {}
        for offer in actions:
            action = offer.get('action') if isinstance(offer, dict) else None
            if isinstance(action, dict) and isinstance(action.get('type'), str):
                groups.setdefault(action['type'], []).append(offer)
        for group in groups.values():
            if len(group) < 2:
                continue
            bodies = [a['action'] for a in group]
            common = {k: deepcopy(v) for k, v in bodies[0].items()
                      if all(k in b and _key(b[k]) == _key(v) for b in bodies[1:])}
            replacements = [[len(templates), {k: v for k, v in b.items() if k not in common}]
                            for b in bodies]
            if _size(common) + _size(replacements) >= _size(bodies):
                continue
            templates.append(common)
            for offer, replacement in zip(group, replacements):
                offer['action'] = replacement

    columns = []
    cards = native.get('state', {}).get('cards')
    if isinstance(cards, dict) and cards and all(isinstance(c, dict) for c in cards.values()):
        rows = {}
        for cid, card in cards.items():
            keys = list(card)
            if keys not in columns:
                columns.append(keys)
            rows[cid] = [columns.index(keys), *card.values()]
        if _size(columns) + _size(rows) < _size(cards):
            native['state']['cards'] = rows
        else:
            columns = []

    payload = {'actionTemplates': templates, 'cardColumns': columns, 'view': view}
    # Never alias semantic ID strings; the output contract needs no alias mapper.
    counts = Counter()
    for value in _walk(payload):
        if isinstance(value, (dict, list)) or (isinstance(value, str) and len(value) >= 48
                                             and not value.startswith('argentum-action-')):
            key = _key(value)
            if len(key) >= 64:
                counts[key] += 1
    shared = []
    indices = {}

    def encode(value, path=()):
        # Keep the established stable deck prefix independent of live table indices.
        if path in {('view', 'deckTable'), ('view', 'observation', 'deckKnowledge'),
                    ('view', 'observation', 'state', 'deck')}:
            return deepcopy(value)
        key = _key(value)
        candidate = counts.get(key, 0) > 1
        if candidate and key in indices:
            return {REFERENCE: indices[key]}
        if isinstance(value, dict):
            result = {k: encode(v, (*path, k)) for k, v in value.items()}
        elif isinstance(value, list):
            result = [encode(v, (*path, i)) for i, v in enumerate(value)]
        else:
            result = value
        if candidate:
            indices[key] = len(shared)
            shared.append(result)
            return {REFERENCE: indices[key]}
        return result

    encoded = encode(payload)
    result = {'format': FORMAT, 'sharedValues': shared, **encoded}
    instruction_bytes = len(json.dumps(INSTRUCTIONS).encode()) - 2
    return result if _message_size(result) + instruction_bytes < _message_size(base) else deepcopy(base)


def expand_shared_view(value):
    """Restore a legacy compact view, rejecting invalid or forward references."""
    if not isinstance(value, dict) or set(value) != {
        'format', 'sharedValues', 'actionTemplates', 'cardColumns', 'view'
    } or value['format'] != FORMAT or not isinstance(value['sharedValues'], list):
        raise ValueError('invalid shared observation envelope')
    shared = []

    def decode(item):
        if isinstance(item, dict):
            if REFERENCE in item:
                index = item[REFERENCE]
                if set(item) != {REFERENCE} or type(index) is not int or not 0 <= index < len(shared):
                    raise ValueError('invalid shared observation reference')
                return deepcopy(shared[index])
            return {k: decode(v) for k, v in item.items()}
        if isinstance(item, list):
            return [decode(v) for v in item]
        return item

    for item in value['sharedValues']:
        shared.append(decode(item))
    templates = decode(value['actionTemplates'])
    columns = decode(value['cardColumns'])
    view = decode(value['view'])
    if not isinstance(templates, list) or not all(isinstance(t, dict) for t in templates):
        raise ValueError('invalid action templates')
    if not isinstance(columns, list) or any(
        not isinstance(c, list) or not all(isinstance(k, str) for k in c) or len(set(c)) != len(c)
        for c in columns
    ):
        raise ValueError('invalid card columns')
    if not isinstance(view, dict) or not isinstance(view.get('observation'), dict):
        raise ValueError('invalid shared observation view')
    native = view['observation']
    if templates:
        for offer in native.get('legalActions', []):
            action = offer.get('action')
            if isinstance(action, list):
                if (len(action) != 2 or type(action[0]) is not int or not 0 <= action[0] < len(templates)
                        or not isinstance(action[1], dict) or set(action[1]) & set(templates[action[0]])):
                    raise ValueError('invalid action template row')
                offer['action'] = {**deepcopy(templates[action[0]]), **action[1]}
    if columns:
        cards = native.get('state', {}).get('cards')
        if not isinstance(cards, dict):
            raise ValueError('invalid card table')
        restored = {}
        for cid, row in cards.items():
            if (not isinstance(row, list) or not row or type(row[0]) is not int
                    or not 0 <= row[0] < len(columns) or len(row) != len(columns[row[0]]) + 1):
                raise ValueError('invalid card table row')
            restored[cid] = dict(zip(columns[row[0]], row[1:]))
        native['state']['cards'] = restored
    return view
