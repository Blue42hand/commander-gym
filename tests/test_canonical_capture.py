"""Synthetic writer protocol fixtures; no provider, engine execution or game launch."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from commander_gym.captured_choices import CALLBACK_SCHEMA_HASH
from commander_gym.game_journal import PIN_KEYS, ZERO, inspect_journal, PrivateGameJournal, verify_finalized_manifest
from commander_gym.game_server_seat import SeatProvenance
from commander_gym.native_game_capture import NativeGameCapture


def context(observation, cid):
    body = json.dumps(observation, separators=(',', ':'))
    return {'version': 2, 'correlationId': cid, 'observationBody': body,
            'schemaHash': CALLBACK_SCHEMA_HASH, 'stateDigest': hashlib.sha256(body.encode()).hexdigest()}


class CanonicalCaptureTests(unittest.TestCase):
    def fixture(self, variant=None, *, recovery=False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        root.chmod(0o700)
        game = root / 'synthetic-canonical'
        game.mkdir(mode=0o700)
        path = game / 'native-000000.ndjson'
        path.touch(mode=0o600)
        sequence, previous, native_state = 0, ZERO, 'c' * 64
        def add(kind, payload, seat=None):
            nonlocal sequence, previous
            sequence += 1
            body = dict(schemaVersion=1, gameId=game.name, sequence=sequence, previousSha256=previous,
                        engineRevision='a'*40, clockEpoch='synthetic-native-clock', utc='2026-01-01T00:00:00Z',
                        monotonicNanos=sequence, kind=kind, visibility='seat' if seat else 'admin', payload=payload)
            if seat:
                body['seatId'] = seat
            exact = json.dumps(body, separators=(',', ':'))
            previous = hashlib.sha256(exact.encode()).hexdigest()
            with path.open('a') as stream:
                stream.write(json.dumps({'body': exact, 'sha256': previous}) + '\n')
        add('initialization', {'callbackEvidenceVersion': 2, 'initialStateDigest': native_state,
            'setup': {'seed': 42, 'players': [{'playerId': 'seat-a', 'deck': {'cards': ['Forest', 'Forest']}}]},
            'pinnedCards': ['synthetic-card']})
        pins = {key: 'synthetic-fixture' for key in PIN_KEYS}
        pins['gym'] = 'b'*40
        capture = NativeGameCapture(root, pins)
        self.addCleanup(capture.close)
        lineage = {key: {'artifact_type': key, 'artifact_id': key+'-a', 'revision': 'v1', 'fingerprint': 'a'*64}
                   for key in ('binding', 'deck', 'pilot')}
        lineage.update(deck_cards={'Forest': 2}, pilot_name='synthetic-pilot', pilot_version='fixture-v1')
        cases = [
            ('decideMulligan', {'mulligan': {'hand': ['visible-card'], 'mulliganCount': 0}},
             [{'semanticId': 'native-keep', 'actionId': 0, 'kind': 'KeepHand', 'action': {'type': 'KeepHand', 'playerId': 'seat-a'},
               'parameterSpec': {'allowedFields': {}}}], None, {'keep': True},
             {'channel': 'action', 'semanticId': 'native-keep', 'actionId': 0, 'params': {}}),
            ('chooseBottomCards', {}, [], {'kind': 'BottomCards', 'decisionId': 'native-bottom',
             'semanticId': 'native-bottom-semantic', 'requiresStructuredResponse': True}, {'selectedCards': ['visible-card']},
             {'channel': 'decision', 'semanticId': 'native-bottom-semantic',
              'response': {'type': 'CardsSelectedResponse', 'selectedCards': ['visible-card']}}),
            ('chooseAction', {'viewingPlayerId': 'seat-a'}, [{'semanticId': 'native-cast', 'actionId': 0, 'kind': 'CastSpell',
             'action': {'type': 'CastSpell', 'playerId': 'seat-a'},
             'parameterSpec': {'allowedFields': {'targets': 'ENTITY_ID_ARRAY', 'xValue': 'INTEGER'}}}], None,
             {'channel': 'action', 'actionId': 0, 'params': {'targets': ['visible-target'], 'xValue': 3}},
             {'channel': 'action', 'semanticId': 'native-cast', 'actionId': 0, 'params': {'targets': ['visible-target'], 'xValue': 3}}),
            ('chooseAction', {'viewingPlayerId': 'seat-a'}, [], {'kind': 'YesNoDecision', 'id': 'native-question', 'decisionId': 'native-question',
             'semanticId': 'native-yes-no', 'requiresStructuredResponse': True},
             {'channel': 'decision', 'response': {'type': 'YesNoResponse', 'decisionId': 'native-question', 'yes': True}},
             {'channel': 'decision', 'semanticId': 'native-yes-no', 'response': {'type': 'YesNoResponse', 'yes': True}}),
        ]
        for index, (kind, state, legal, pending, chosen, accepted) in enumerate(cases):
            if index == 2 and recovery:
                capture.close()
                capture = NativeGameCapture(root, pins)
                self.addCleanup(capture.close)
            if index == 2 and variant in {'resume', 'bad_resume', 'resume_checkpoint', 'duplicate_resume_checkpoint', 'callback_before_resume'}:
                add('resume', {'previousSourceSequence': sequence, 'exactReplayVerified': False})
                if variant in {'resume_checkpoint', 'duplicate_resume_checkpoint'}:
                    add('state_checkpoint', {'beforeStateDigest': None, 'after': {'admin': 'ADMIN-PRIVATE-MARKER'}})
                    if variant == 'duplicate_resume_checkpoint':
                        add('state_checkpoint', {'beforeStateDigest': None, 'after': {'admin': 'ADMIN-PRIVATE-MARKER'}})
                if variant == 'callback_before_resume':
                    add('seat_observation', {'unexpected': True}, 'seat-a')
                add('callback_resume_state', {'version': 2, 'restoredStateDigest': 'f'*64 if variant == 'bad_resume' else native_state})
            before = {'type': 'GameServerSeat', 'callbackKind': kind, 'state': state, 'legalActions': legal,
                      'pendingDecision': pending, 'recentGameLog': [], 'perspectivePlayerId': 'seat-a', 'agentToAct': 'seat-a', 'terminated': False}
            evidence = context(before, f'synthetic-cid-{index}')
            observation = {**before, 'schemaHash': evidence['schemaHash'], 'stateDigest': evidence['stateDigest']}
            if kind == 'chooseAction':
                observation['knownDeck'] = {'cards': {'Forest': 2}}
            add('ai_callback_input', evidence, 'seat-a')
            event = SeatProvenance(kind, observation, {**chosen, 'metadata': {}}, evidence, lineage)
            sink = capture.seat_sink(game.name, 'seat-a')
            sink.started(event)
            sink.finished(event)
            action = {'type': 'SyntheticAppliedAction', 'playerId': 'seat-a', 'ordinal': index}
            next_state = hashlib.sha256(str(index).encode()).hexdigest()
            add('native_transition', {'action': action, 'beforeStateDigest': native_state,
                'effectiveStateDigest': next_state, 'administrativeStall': None,
                'result': {'state': {'opponentHand': ['ADMIN-PRIVATE-MARKER']}, 'error': None, 'events': [], 'outcome': {'type': 'Done'}}})
            native_state = next_state
            after = {**before, 'callbackKind': 'result', 'state': {'viewingPlayerId': 'seat-a'},
                     'legalActions': [], 'pendingDecision': None}
            result = {'version': 2, 'correlationId': evidence['correlationId'], 'status': 'accepted',
                      'inputStateDigest': evidence['stateDigest'], 'choice': accepted, 'action': action,
                      'resultObservation': context(after, f'synthetic-result-{index}')}
            if index == 2 and variant in {'failed', 'stale', 'cancelled'}:
                add('ai_callback_disposition', {'version': 2, 'correlationId': evidence['correlationId'], 'status': variant}, 'seat-a')
            elif not (index == 2 and variant == 'missing_result'):
                if index == 2 and variant == 'unmatched':
                    result['choice'] = {**accepted, 'params': {'xValue': 1}}
                add('ai_callback_result', result, 'seat-a')
        add('terminal', {'nativeGameOver': True, 'winnerId': 'seat-a', 'administrativeStall': None})
        return capture, game

    def test_all_callback_targets_complete_without_admin_features(self):
        capture, game = self.fixture()
        capture.scan()
        manifest = verify_finalized_manifest(game)
        self.assertTrue(manifest['recording_complete'], manifest['gaps'])
        envelope = next(row['payload']['envelope'] for row in inspect_journal(game)['rows'] if row['kind'] == 'raw_evidence')
        self.assertEqual(envelope['evidence_schema_version'], 2)
        self.assertEqual(envelope['source_schemas']['decision_records'], [1, 2])
        self.assertEqual(len(envelope['decisions']), 4)
        self.assertNotIn('native-question', json.dumps([row['input'] for row in envelope['decisions']]))
        self.assertEqual(envelope['decisions'][2]['target']['chosen_action_params'], {'targets': ['visible-target'], 'xValue': 3})
        self.assertNotIn('ADMIN-PRIVATE-MARKER', json.dumps([row['input'] for row in envelope['decisions']]))
        self.assertNotIn('ADMIN-PRIVATE-MARKER', json.dumps([row['target'] for row in envelope['decisions']]))

    def test_decoded_conversion_budget_remains_incomplete(self):
        with patch('commander_gym.canonical_capture.MAX_CANONICAL_BYTES', 1):
            capture, game = self.fixture()
            capture.scan()
            manifest = verify_finalized_manifest(game)
            self.assertFalse(manifest['recording_complete'])
            self.assertIn('canonical_v2_callback_or_lifecycle_unavailable', manifest['gaps'])

    def test_failed_stale_cancelled_missing_unmatched_remain_incomplete(self):
        for variant in ('failed', 'stale', 'cancelled', 'missing_result', 'unmatched', 'bad_resume', 'duplicate_resume_checkpoint', 'callback_before_resume'):
            with self.subTest(variant=variant):
                capture, game = self.fixture(variant)
                capture.scan()
                manifest = verify_finalized_manifest(game)
                self.assertFalse(manifest['recording_complete'])
                self.assertIn('canonical_v2_callback_or_lifecycle_unavailable', manifest['gaps'])

    def test_recorder_restart_and_authenticated_native_resume(self):
        for variant in (None, 'resume', 'resume_checkpoint'):
            with self.subTest(variant=variant):
                capture, game = self.fixture(variant, recovery=True)
                capture.scan()
                manifest = verify_finalized_manifest(game)
                self.assertTrue(manifest['recording_complete'], manifest['gaps'])

    def test_envelope_append_interruption_reuses_durable_attachment(self):
        capture, game = self.fixture()
        with patch.object(PrivateGameJournal, 'finish', side_effect=OSError('synthetic interrupted sealing')):
            capture.scan()
        self.assertEqual(sum(row['kind'] == 'raw_evidence' for row in inspect_journal(game)['rows']), 1)
        root, pins = capture.root, capture.declared_pins
        capture.close()
        recovered = NativeGameCapture(root, pins)
        self.addCleanup(recovered.close)
        recovered.scan()
        manifest = verify_finalized_manifest(game)
        self.assertTrue(manifest['recording_complete'], manifest['gaps'])
        self.assertEqual(sum(row['kind'] == 'raw_evidence' for row in inspect_journal(game)['rows']), 1)


if __name__ == '__main__':
    unittest.main()
