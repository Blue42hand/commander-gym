"""Offline counterfactual review of extracted, seat-authenticated callback rows.

This measures decisions covered, not games won or actual replay speedups. No model,
engine execution, production writes, or terminal-result claims are made here.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from .captured_choices import verified_observation
from .heuristic_facts import CardFacts, heuristic_component_ref, canonical
from .heuristic_policy import MaskedHeuristicSubsystem
from .pilot import ArgentumActionChoice


def review_callbacks(rows, facts, engine_revision):
    if facts.engine_revision != engine_revision:
        raise ValueError('review native engine pin differs from facts')
    policy = MaskedHeuristicSubsystem(facts)
    counts, reasons, decisions = Counter(), Counter(), []
    provider_seconds = 0.
    policy_seconds = 0.
    for row in rows:
        if row.get('kind') != 'seat_callback':
            continue
        payload = row['payload']
        original = payload['observation']
        native = verified_observation(payload['decision_evidence'], original['agentToAct'])
        if any(original.get(k) != v for k, v in native.items()):
            raise ValueError('recorded callback differs from seat-authenticated observation')
        # These fields are Binding-owned additions, not hidden native state.
        observation = {**native, **{k: original[k] for k in ('knownDeck', 'deckKnowledge') if k in original}}
        counts['callbacks'] += 1
        old_attempts = payload['choice'].get('metadata', {}).get('modelIo', {}).get('attempts', [])
        counts['baseline_model_callbacks'] += bool(old_attempts)
        result = policy.try_choose(observation)
        reason = result.metadata.get('reason', result.reason)
        reasons[reason] += 1
        policy_seconds += result.metadata['policyWallTimeMs'] / 1000
        decision = {'sequence': row['sequence'], 'observationSha256': hashlib.sha256(canonical(native)).hexdigest(),
                    'handled': result.choice is not None, 'reason': reason}
        if result.choice is None:
            counts['deferred'] += 1
        else:
            counts['handled'] += 1
            counts['baseline_model_callbacks_handled'] += bool(old_attempts)
            provider_seconds += sum(a['response'].get('providerWallTimeMs', 0) for a in old_attempts) / 1000
            if isinstance(result.choice, ArgentumActionChoice):
                a = next(a for a in native['legalActions'] if a['actionId'] == result.choice.action_id)
                decision.update(kind=a['kind'], semanticId=a['semanticId'], params=dict(result.choice.params))
            else:
                decision['response'] = dict(result.choice.response)
        decisions.append(decision)
    return {'scope': 'counterfactual-position-coverage-only', 'engineRevision': engine_revision,
            'component': vars(heuristic_component_ref(facts)), 'counts': dict(counts), 'reasons': dict(reasons),
            'policySeconds': round(policy_seconds, 6),
            'historicalAggregateProviderSecondsOnHandledPositions': round(provider_seconds, 3), 'decisions': decisions}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--callbacks', type=Path, required=True, help='Private JSON array of extracted journal rows')
    parser.add_argument('--card-facts', type=Path, required=True)
    parser.add_argument('--engine-revision', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = review_callbacks(json.loads(args.callbacks.read_text()), CardFacts.load(args.card_facts), args.engine_revision)
    # New private artifact only; never overwrite a capture or existing result.
    import os
    with os.fdopen(os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'w') as out:
        json.dump(result, out, sort_keys=True, indent=2)
    print(json.dumps({k: v for k, v in result.items() if k != 'decisions'}, sort_keys=True))


if __name__ == '__main__':
    main()
