import json
from pathlib import Path
import tempfile
import unittest

from commander_gym.capture_training_audit import audit_capture_training
from commander_gym.game_journal import PIN_KEYS, PrivateGameJournal, RecorderSeatSink
from commander_gym.game_server_seat import SeatProvenance


class CaptureTrainingAuditTests(unittest.TestCase):
    def audit(self, observation, *, close=True, corrupt=False):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'synthetic'
            pins = {key: 'synthetic' for key in PIN_KEYS}
            journal = PrivateGameJournal(directory, 'synthetic', pins, game_id='synthetic')
            sink = RecorderSeatSink(journal, 'seat-a')
            sink.started(SeatProvenance('chooseAction', observation, {}))
            sink.finished(SeatProvenance('chooseAction', observation,
                {'channel': 'action', 'actionId': 0, 'metadata': {}}))
            if close:
                journal.finish({'kind': 'finalized_aborted'}, expected_sources={'seat:seat-a': 1}, gaps=[])
            journal.close()
            if corrupt:
                path = directory / '000000.jsonl'
                path.write_bytes(path.read_bytes().replace(b'synthetic-private-card', b'synthetic-altered-card'))
            return audit_capture_training(directory)

    def test_existing_semantic_menu_is_not_reported_as_absent(self):
        report = self.audit({'perspectivePlayerId': 'seat-a',
                             'legalActions': [{'semanticId': 'native-pass', 'actionId': 0}]})
        self.assertTrue(report['capture_verified'])
        self.assertEqual(report['callbacks'], 1)
        self.assertEqual(report['field_availability']['native_semantic_menu'], 1)
        self.assertEqual(report['field_availability']['schemaHash'], 0)
        self.assertFalse(report['canonical_conversion_supported'])

    def test_populated_fields_do_not_invent_application_join_or_replay(self):
        report = self.audit({'perspectivePlayerId': 'seat-a', 'schemaHash': 'schema',
            'stateDigest': 'digest', 'legalActions': [{'semanticId': 'native-pass'}]})
        self.assertEqual(report['field_availability']['stateDigest'], 1)
        self.assertFalse(report['canonical_conversion_supported'])
        self.assertFalse(report['exact_replay_verified'])

    def test_structured_native_identity_is_available(self):
        report = self.audit({'perspectivePlayerId': 'seat-a', 'pendingDecision': {
            'semanticId': 'native-select', 'requiresStructuredResponse': True}})
        self.assertEqual(report['field_availability']['native_semantic_menu'], 1)

    def test_gym_alias_and_duplicate_menu_are_not_native_identity_evidence(self):
        for ids in (['commander-gym-callback-v1:mulligan:keep'], ['duplicate', 'duplicate'], [None]):
            with self.subTest(ids=ids):
                report = self.audit({'perspectivePlayerId': 'seat-a',
                                     'legalActions': [{'semanticId': value} for value in ids]})
                self.assertEqual(report['field_availability']['native_semantic_menu'], 0)

    def test_partial_source_is_not_a_verified_training_capture(self):
        report = self.audit({'perspectivePlayerId': 'seat-a'}, close=False)
        self.assertFalse(report['capture_verified'])
        self.assertIn('missing_terminal', report['issues'])

    def test_aggregate_report_does_not_emit_observation_content(self):
        report = self.audit({'perspectivePlayerId': 'seat-a', 'ownHand': ['synthetic-private-card']})
        self.assertNotIn('synthetic-private-card', json.dumps(report))
        self.assertNotIn('seat-a', json.dumps(report))

    def test_corrupt_capture_is_rejected_before_callback_fields_are_counted(self):
        report = self.audit({'perspectivePlayerId': 'seat-a',
                             'ownHand': ['synthetic-private-card']}, corrupt=True)
        self.assertFalse(report['capture_verified'])
        self.assertIn('hash_mismatch', report['issues'])
        self.assertEqual(report['field_availability'], {})
