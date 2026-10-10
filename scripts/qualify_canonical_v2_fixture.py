#!/usr/bin/env python3
"""Offline qualification of CanonicalCallbackFixtureTest's real native writer.

The JVM test records deterministic fake answers before calling native authority.
This driver replays those captured answers into the recorder, reopening it between
callbacks. It never runs a controller, game, provider, credential loader or service.
It certifies recording behavior only, not source release or host access.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from commander_gym.captured_choices import verified_observation
from commander_gym.game_journal import PIN_KEYS, verify_finalized_manifest, inspect_journal
from commander_gym.game_server_seat import SeatProvenance
from commander_gym.native_game_capture import NativeGameCapture, read_native_source


def require(condition, message):
    if not condition:
        raise ValueError(message)


def qualify(root: Path, gym_sha: str):
    game = root / 'native-canonical-fixture'
    native = [json.loads(row['source_body']) for row in read_native_source(game)]
    initial = next(row for row in native if row['kind'] == 'initialization')
    require(native[-1]['kind'] == 'terminal', "native fixture qualification check failed at 30")
    require(any(row['kind'] == 'resume' for row in native), "native fixture qualification check failed at 31")
    require(any(row['kind'] == 'callback_resume_state' for row in native), "native fixture qualification check failed at 32")
    inputs = {row['payload']['correlationId']: row for row in native if row['kind'] == 'ai_callback_input'}
    results = {row['payload']['correlationId']: row for row in native if row['kind'] == 'ai_callback_result'}
    require(len(inputs) == len(results) == 5, "native fixture qualification check failed at 35")
    require(not any(row['kind'] == 'ai_callback_disposition' for row in native), "native fixture qualification check failed at 36")
    source = root / 'fixture-callbacks.ndjson'
    require(not source.is_symlink() and source.stat().st_mode & 0o077 == 0, "native fixture qualification check failed at 38")
    callbacks = [json.loads(line) for line in source.read_text().splitlines()]
    require(len(callbacks) == 5, "native fixture qualification check failed at 40")
    pins = {key: 'keyless-native-fixture' for key in PIN_KEYS}
    pins.update(engine=initial['engineRevision'], gym=gym_sha)
    players = {player['playerId']: player for player in initial['payload']['setup']['players']}
    joined = set()
    for callback in callbacks:
        context, seat = callback['decisionEvidence'], callback['seatId']
        cid = context['correlationId']
        require(cid not in joined and inputs[cid]['payload'] == context, "native fixture qualification check failed at 48")
        require(inputs[cid]['sequence'] <= callback['nativeSequenceBeforeApplication'] < results[cid]['sequence'], "native fixture qualification check failed at 49")
        joined.add(cid)
        observation = verified_observation(context, seat)
        observation.update(schemaHash=context['schemaHash'], stateDigest=context['stateDigest'])
        deck = players[seat]['deck']
        names = [entry['name'] for entry in deck['cardEntries']] if deck.get('cardEntries') else deck['cards']
        deck_cards = dict(Counter(names))
        if observation['callbackKind'] == 'chooseAction':
            observation['knownDeck'] = {'cards': deck_cards}
        lineage = {key: {'artifact_type': key, 'artifact_id': 'native-fixture-' + key + '-' + seat,
                         'revision': 'fixture-v2', 'fingerprint': hashlib.sha256((key + seat).encode()).hexdigest()}
                   for key in ('binding', 'deck', 'pilot')}
        lineage.update(deck_cards=deck_cards, pilot_name='precommitted-fake-callback', pilot_version='fixture-v2')
        event = SeatProvenance(observation['callbackKind'], observation, callback['choice'], context, lineage)
        # A recorder restart between every callback exercises durable open-journal recovery.
        capture = NativeGameCapture(root, pins)
        try:
            sink = capture.seat_sink(game.name, seat)
            sink.started(event)
            sink.finished(event)
        finally:
            capture.close()
    require(joined == set(inputs) == set(results), "native fixture qualification check failed at 71")
    capture = NativeGameCapture(root, pins)
    try:
        capture.scan()
    finally:
        capture.close()
    manifest = verify_finalized_manifest(game)
    require(manifest['recording_complete'], 'native fixture canonical recording incomplete: ' + str(manifest['gaps']))
    raw = [row['payload']['envelope'] for row in inspect_journal(game)['rows'] if row['kind'] == 'raw_evidence']
    require(len(raw) == 1 and raw[0]['evidence_schema_version'] == 2, "native fixture qualification check failed at 80")
    require(len(raw[0]['decisions']) == 5, "native fixture qualification check failed at 81")
    return {'fixture': 'real-native-writer-precommitted-fake-answers', 'callbacks': 5,
            'native_restore_verified': True, 'recorder_restarts': 5, 'recording_complete': True,
            'provider_calls': 0, 'deployment_authorized': False,
            'evidence_scope': raw[0]['run']['metadata']['evidence_scope']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--gym-sha', required=True)
    args = parser.parse_args()
    print(json.dumps(qualify(args.root, args.gym_sha), sort_keys=True))
