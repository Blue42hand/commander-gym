from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from commander_gym.captured_choices import SCHEMA_HASH, accepted_choice_records, verified_observation, main
from commander_gym.game_journal import JournalError, PIN_KEYS, ZERO
from commander_gym.game_server_seat import SeatProvenance
from commander_gym.native_game_capture import NativeGameCapture


def context(observation, cid='synthetic-correlation'):
    body = json.dumps(observation, separators=(',', ':'))
    return {'version': 1, 'correlationId': cid, 'observationBody': body,
            'schemaHash': SCHEMA_HASH, 'stateDigest': hashlib.sha256(body.encode()).hexdigest()}


def observation():
    return {'type': 'GameServerSeat', 'state': {'viewingPlayerId': 'seat-a', 'ownHand': ['Forest']},
        'legalActions': [{'actionId': 0, 'semanticId': 'native-pass', 'kind': 'PassPriority',
                         'action': {'type': 'PassPriority', 'playerId': 'seat-a'}}],
        'pendingDecision': None, 'recentGameLog': [], 'perspectivePlayerId': 'seat-a',
        'agentToAct': 'seat-a', 'terminated': False}


class CapturedChoicesTests(unittest.TestCase):
    def fixture(self, variant='accepted', *, compressed_source=False, large_observation=False, choice_ids=('synthetic-correlation',)):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        root.chmod(0o700)
        game = root / 'synthetic-game'
        game.mkdir(mode=0o700)
        path = game / 'native-000000.ndjson'
        path.touch(mode=0o600)
        sequence, previous = 0, ZERO
        def add(kind, payload, seat=None):
            nonlocal sequence, previous
            sequence += 1
            body = dict(schemaVersion=1, gameId=game.name, sequence=sequence, previousSha256=previous,
                engineRevision='a'*40, clockEpoch='synthetic-clock', utc='2026-01-01T00:00:00Z',
                monotonicNanos=sequence, kind=kind, visibility='seat' if seat else 'admin', payload=payload)
            if seat: body['seatId'] = seat
            exact = json.dumps(body, separators=(',', ':'))
            previous = hashlib.sha256(exact.encode()).hexdigest()
            from commander_gym.record_codec import encode_record
            physical = (json.dumps({'body': exact, 'sha256': previous})+'\n').encode()
            with path.open('ab') as stream: stream.write(encode_record(physical, compress=compressed_source))
        add('initialization', {'setup': {'seed': 42, 'players': [{'playerId': 'seat-a',
            'deck': {'cards': ['Forest', 'Forest']}}]}, 'pinnedCards': []})
        pins = {key: 'synthetic' for key in PIN_KEYS}
        pins['gym'] = 'b'*40
        capture = NativeGameCapture(root, pins)
        self.addCleanup(capture.close)
        sink = capture.seat_sink(game.name, 'seat-a')
        for cid in choice_ids:
            before = observation()
            if large_observation: before['state']['ownHand'].append('synthetic-own-card-' * 2000)
            evidence = context(before, cid)
            add('ai_decision_input', evidence, 'seat-a')
            lineage = {key: {'artifact_type': key, 'artifact_id': key+'-a', 'revision': 'v1', 'fingerprint': 'a'*64}
                       for key in ('binding', 'deck', 'pilot')}
            lineage.update(deck_cards={'Forest': 2}, pilot_name='synthetic-pilot', pilot_version='v1')
            enriched = {**before, 'schemaHash': evidence['schemaHash'], 'stateDigest': evidence['stateDigest'],
                        'knownDeck': {'cards': {'Forest': 2}}}
            if variant == 'observation_mismatch': enriched['state'] = {'viewingPlayerId': 'seat-a', 'ownHand': ['Island']}
            if variant == 'lineage_mismatch': lineage['deck_cards'] = {'Island': 2}
            if variant == 'admin_addition': enriched['refereeState'] = {'opponentHidden': 'synthetic-secret'}
            if variant == 'deck_addition': enriched['knownDeck']['opponentDeck'] = ['synthetic-secret']
            event = SeatProvenance('chooseAction', enriched, {'channel': 'action', 'actionId': 0,
                'params': {'targets': ['synthetic']} if variant == 'parameters' else {},
                'metadata': {'input_tokens': 17, 'rationale': 'actual synthetic rationale'}}, evidence, lineage)
            sink.started(event)
            sink.finished(event)
            if variant == 'duplicate_callback':
                sink.started(event)
                sink.finished(event)
            action = before['legalActions'][0]['action']
            add('native_transition', {'action': action, 'result': {'state': {'opponentHidden': 'synthetic-admin-secret'},
                'events': [], 'outcome': {'type': 'Done'}}, 'beforeStateDigest': 'c'*64, 'effectiveStateDigest': 'd'*64})
            after = deepcopy(before)
            after['state']['ownHand'] = []
            after['legalActions'] = []
            result = {'version': 1, 'correlationId': evidence['correlationId'], 'status': 'accepted',
                      'inputStateDigest': evidence['stateDigest'], 'semanticId': 'native-pass',
                      'actionId': 0, 'action': action, 'resultObservation': context(after)}
            if variant in {'rejected', 'stale', 'overridden'}:
                add('ai_decision_disposition', {'correlationId': evidence['correlationId'], 'status': variant}, 'seat-a')
            elif variant != 'missing_result':
                if variant == 'wrong_seat': result['resultObservation'] = context({**after, 'perspectivePlayerId': 'seat-b'})
                add('ai_decision_result', result, 'seat-a')
                if variant == 'duplicate_result': add('ai_decision_result', result, 'seat-a')
        add('terminal', {'nativeGameOver': True, 'winnerId': 'seat-a', 'administrativeStall': None})
        capture.scan()
        return game

    def test_accepted_join_returns_existing_canonical_record_with_provenance_and_no_admin_state(self):
        records, diagnostics = accepted_choice_records(self.fixture())
        self.assertEqual(diagnostics, {})
        self.assertEqual(len(records), 1)
        record = records[0].to_dict()
        self.assertEqual(record['chosen_action_id'], 'native-pass')
        self.assertEqual(record['deck_id'], 'deck-a')
        self.assertEqual(record['metadata']['pilot_metadata']['input_tokens'], 17)
        self.assertIn('journalRoot', record['metadata']['pilot_metadata']['recordingProvenance'])
        self.assertIsNone(record['metadata']['timing']['submission_elapsed_ms'])
        self.assertNotIn('synthetic-admin-secret', json.dumps(record))
        self.assertNotIn('synthetic-correlation', json.dumps(record['observation']))

    def test_callback_order_is_chronological_for_reverse_lexical_correlation_ids(self):
        records, diagnostics = accepted_choice_records(self.fixture(choice_ids=('z-first', 'a-second')))
        self.assertEqual([r.to_dict()['decision_id'] for r in records], ['z-first', 'a-second'])
        self.assertEqual(diagnostics, {})

    def test_rejected_incomplete_stale_duplicate_overridden_and_parameterized_choices_stay_diagnostic(self):
        for variant in ('rejected', 'stale', 'overridden', 'missing_result', 'duplicate_callback',
                        'duplicate_result', 'parameters', 'wrong_seat', 'observation_mismatch', 'lineage_mismatch',
                        'admin_addition', 'deck_addition'):
            with self.subTest(variant=variant):
                records, diagnostics = accepted_choice_records(self.fixture(variant))
                self.assertEqual(records, [])
                self.assertTrue(diagnostics)

    def test_exact_native_bytes_and_version_are_required(self):
        evidence = context(observation())
        for field, value in [('version', True), ('schemaHash', 'invented'), ('stateDigest', 'wrong'),
                             ('observationBody', evidence['observationBody']+' ')]:
            with self.subTest(field=field):
                with self.assertRaises(JournalError): verified_observation({**evidence, field: value}, 'seat-a')

    def test_explicit_export_is_private_never_overwrites_and_cannot_pollute_journal(self):
        game = self.fixture()
        output = game.parent / 'accepted.jsonl'
        with patch('sys.argv', ['export', str(game), '--output', str(output)]): main()
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        prior = output.read_bytes()
        with patch('sys.argv', ['export', str(game), '--output', str(output)]):
            with self.assertRaises(FileExistsError): main()
        self.assertEqual(output.read_bytes(), prior)
        with patch('sys.argv', ['export', str(game), '--output', str(game / 'derived.jsonl')]):
            with self.assertRaises(JournalError): main()
        self.assertEqual(len(accepted_choice_records(game)[0]), 1)

    def test_native_source_corruption_or_partial_tail_prevents_export(self):
        for corruption in (b'partial', b'\n'):
            game = self.fixture()
            with (game / 'native-000000.ndjson').open('ab') as stream: stream.write(corruption)
            with self.assertRaises(JournalError): accepted_choice_records(game)
