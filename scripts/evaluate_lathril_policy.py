#!/usr/bin/env python3
"""Build a private, hash-checked masked-position corpus and review without inference.

This is counterfactual decision coverage on historical states, not game replay or
win-rate evidence. Never writes a capture, changes production, or dispatches a model.
"""
import argparse
from collections import Counter
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import statistics
import sys
from time import perf_counter
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from commander_gym.captured_choices import verified_observation
from commander_gym.game_journal import _json
from commander_gym.record_codec import decode_record
from commander_gym.heuristic_facts import CardFacts
from commander_gym.lathril_policy import LathrilDeckSubsystem, lathril_component_ref, policy_document
from commander_gym.native_forced_pass import NativeForcedPassSubsystem, _VerifiedNativeMenu
from commander_gym.pilot import validate_pilot_choice


def extract(captures, output, engine_revision):
    sources = []
    with output.open('xb') as raw, gzip.GzipFile(fileobj=raw, mode='wb', mtime=0) as corpus:
        output.chmod(0o600)
        for capture in captures:
            previous = '0'*64; sequence = 0; count = 0; files = []
            for path in sorted(capture.glob('*.jsonl')):
                before = path.stat(); digest = hashlib.sha256()
                with path.open('rb') as stream:
                    for physical in stream:
                        digest.update(physical)
                        record = json.loads(decode_record(physical))
                        value = record.pop('sha256')
                        if (record['sequence'] != sequence or record['previous_sha256'] != previous
                            or hashlib.sha256(_json(record)).hexdigest() != value):
                            raise ValueError('journal chain mismatch: '+str(path))
                        previous = value; sequence += 1
                        if record['kind'] == 'manifest' and record['payload']['pins'].get('engine') != engine_revision:
                            raise ValueError('capture engine differs from candidate public facts')
                        if record['kind'] != 'seat_callback':
                            continue
                        payload = record['payload']; lineage = payload['lineage']
                        if lineage['deck']['fingerprint'] != policy_document()['deck_fingerprint']:
                            continue
                        if lineage.get('deck_knowledge') and lineage['deck_knowledge']['fingerprint'] != policy_document()['primer_fingerprint']:
                            raise ValueError('corpus primer differs from reviewed policy')
                        native = verified_observation(payload['decision_evidence'], record['seat_id'])
                        observation = payload['observation']
                        if any(observation.get(k) != v for k, v in native.items()):
                            raise ValueError('callback differs from authenticated native observation')
                        enriched = {**native, **{k: observation[k] for k in ['knownDeck', 'deckKnowledge', 'schemaHash', 'stateDigest'] if k in observation}}
                        if 'knownDeck' in enriched and enriched['knownDeck'].get('cards') != lineage['deck_cards']:
                            raise ValueError('registered deck knowledge differs from recorded binding')
                        row = {'capture': capture.name, 'sequence': record['sequence'], 'journal_sha256': value,
                               'native_observation_sha256': payload['decision_evidence']['stateDigest'],
                               'observation': enriched, 'nativeEvidence':payload['decision_evidence'], 'baseline': payload['choice']}
                        corpus.write((json.dumps(row, separators=(',', ':'))+'\n').encode());count += 1
                after = path.stat()
                if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                    raise ValueError('capture changed during snapshot')
                files.append({'name':path.name, 'sha256':digest.hexdigest(), 'bytes':before.st_size})
            sources.append({'capture':str(capture), 'callbacks':count, 'chain_rows':sequence, 'chain_tail':previous, 'files':files})
    return sources


def evaluate(corpus, facts):
    policy = LathrilDeckSubsystem(facts)
    forced = NativeForcedPassSubsystem()
    games = {}; details = []
    with gzip.open(corpus, 'rt') as stream:
        for physical in stream:
            row = json.loads(physical); o = row['observation']; before = deepcopy(o)
            native = verified_observation(row['nativeEvidence'], o['agentToAct'])
            if any(o.get(k) != v for k,v in native.items()):
                raise ValueError('corpus observation differs from native evidence')
            baseline = row['baseline'].get('metadata', {})
            game = games.setdefault(row['capture'], {'positions':0, 'baseline_model_calls':0, 'candidate_model_calls':0,
                'candidate_unsupported':0, 'choices_changed':0, 'historical_provider_ms_covered':0,
                'historical_request_bytes_covered':0, 'baseline_input_tokens':0, 'candidate_policy_ms':[], 'rules':Counter(), 'changes':[]})
            game['positions'] += 1
            calls = baseline.get('modelIo', {}).get('attempts', [])
            game['baseline_model_calls'] += bool(calls)
            game['baseline_input_tokens'] += baseline.get('usage', {}).get('prompt_tokens',0)
            started = perf_counter(); error = None
            try:
                # Only this offline reader may issue the process-local certificate:
                # native evidence was authenticated before corpus construction.
                result = forced.try_choose(_VerifiedNativeMenu(o))
                if result.choice is None:
                    result = policy.try_choose(o)
                validate_pilot_choice(result.choice, o)
                if hasattr(result.choice, 'action_id'):
                    chosen = {'actionId':result.choice.action_id, 'channel':'action', 'params':dict(result.choice.params)}
                else:
                    chosen = {'channel':'decision', 'response':dict(result.choice.response)}
                old = {k:v for k,v in row['baseline'].items() if k != 'metadata'}
                comparable = {'keep':chosen['actionId'] == 0} if 'keep' in old and chosen.get('channel') == 'action' else chosen
                changed = comparable != old
                reason = result.metadata.get('reason', 'native-forced-pass')
                game['rules'][reason] += 1
                game['choices_changed'] += changed
                if calls:
                    game['historical_provider_ms_covered'] += baseline.get('providerWallTimeMs',0)
                    game['historical_request_bytes_covered'] += sum(len(json.dumps(a['request'], ensure_ascii=False).encode()) for a in calls)
                if changed:
                    game['changes'].append({'sequence':row['sequence'], 'turn':o['state'].get('turnNumber'), 'rule':reason, 'baseline':old, 'candidate':chosen})
            except Exception as exc:
                error = type(exc).__name__+': '+str(exc)
                game['candidate_unsupported'] += 1
                chosen = None;reason = 'unsupported'
            elapsed = (perf_counter()-started)*1000
            if before != o:
                raise AssertionError('policy mutated captured observation')
            game['candidate_policy_ms'].append(elapsed)
            details.append({'capture':row['capture'],'sequence':row['sequence'],'rule':reason, 'error':error, 'choice':chosen})
    for game in games.values():
        times = game.pop('candidate_policy_ms')
        game['timing'] = {'median_ms':statistics.median(times), 'max_ms':max(times), 'total_ms':sum(times)}
        game['rules'] = dict(game['rules'])
    return {'scope':'counterfactual masked-position coverage; not live replay, Elo or win rate',
            'network_calls':0, 'component':vars(lathril_component_ref(facts)), 'games':games, 'decisions':details}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture', action='append', type=Path, required=True)
    parser.add_argument('--facts', type=Path, required=True)
    parser.add_argument('--corpus', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('evaluation output already exists')
    facts = CardFacts.load(args.facts)
    sources = extract(args.capture, args.corpus, facts.engine_revision)
    if not sum(source['callbacks'] for source in sources):
        raise ValueError('no reviewed Lathril positions in captures')
    result = evaluate(args.corpus, facts);result['sources'] = sources
    result['corpus_sha256'] = hashlib.sha256(args.corpus.read_bytes()).hexdigest()
    with args.output.open('x') as out:
        args.output.chmod(0o600);json.dump(result,out,indent=2)
    print(json.dumps({k:v for k,v in result.items() if k not in ['decisions','sources']},indent=2))


if __name__ == '__main__':
    main()
