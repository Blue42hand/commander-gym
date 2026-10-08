"""Offline diagnostics for schema-1 callback captures, never dataset conversion.

Only aggregate field availability leaves this module. Native admin states cannot
substitute for a seat observation or establish an executed-choice correlation.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any, Mapping

from .game_journal import inspect_journal


def _present(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def audit_capture_training(directory: Path) -> dict[str, Any]:
    """Verify the journal before inspecting callbacks; return no game/seat content.

    Fields are availability evidence, not proof of native authorship. Schema 1
    does not join a callback UUID to native application and a subsequent own-seat
    result. Even a callback containing every field is therefore not convertible.
    """
    report = inspect_journal(directory)
    counts: Counter[str] = Counter()
    if report['issues']:
        return {'audit_schema_version': 1, 'capture_verified': False,
                'issues': sorted(set(report['issues'])), 'callbacks': 0,
                'field_availability': {}, 'canonical_conversion_supported': False,
                'exact_replay_verified': False}
    for row in report['rows']:
        if row['kind'] != 'seat_callback':
            continue
        counts['callbacks'] += 1
        payload = row['payload']
        observation = payload.get('observation')
        observation = observation if isinstance(observation, Mapping) else {}
        for name in ('schemaHash', 'stateDigest'):
            counts[name] += int(_present(observation.get(name)))
        pending = observation.get('pendingDecision')
        if isinstance(pending, Mapping) and pending.get('requiresStructuredResponse') is True:
            ids = [pending.get('semanticId')]
        else:
            legal = observation.get('legalActions')
            ids = [item.get('semanticId') if isinstance(item, Mapping) else None
                   for item in legal] if isinstance(legal, list) else []
        # Callback-only mulligan/bottom aliases are Gym routing conveniences.
        semantic = bool(ids) and all(_present(value) and
                    not value.startswith('commander-gym-callback-v1:') for value in ids)
        semantic = semantic and len(set(ids)) == len(ids)
        counts['native_semantic_menu'] += int(semantic)
    return {'audit_schema_version': 1, 'capture_verified': True, 'issues': [],
            'callbacks': counts.pop('callbacks', 0), 'field_availability': dict(counts),
            'canonical_conversion_supported': False, 'exact_replay_verified': False,
            'implementation_blockers': [
                'schema_1_callback_to_native_application_join_unavailable',
                'schema_1_joined_native_own_seat_result_unavailable',
                'capture_to_canonical_execution_trace_adapter_unimplemented'],
            'lineage_status': 'requires_per_decision_deck_and_binding_validation',
            'replay_status': 'requires_pinned_native_full_state_and_event_reexecution'}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    print(json.dumps(audit_capture_training(args.directory), sort_keys=True))


if __name__ == '__main__':
    main()
