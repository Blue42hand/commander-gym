from __future__ import annotations
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from commander_gym.game_journal import (
    PIN_KEYS, JournalError, PrivateGameJournal, RecorderSeatSink,
    inspect_journal, publish_finalized_manifest, seat_projection,
)
from commander_gym.game_server_seat import GameServerSeatAdapter, SeatProvenance
from commander_gym.pilot import ArgentumActionChoice

PINS = {k: {'revision': 'synthetic-' + k} for k in PIN_KEYS}
TRANSITION = {'game_id': 'g', 'native_schema': 'synthetic-v1',
              'action': {'type': 'PassPriority'}, 'events': [],
              'result': {'status': 'applied'}, 'before_state_digest': 'before',
              'after_state_digest': 'after'}
OBS = {'perspectivePlayerId': 'a', 'state': {'viewingPlayerId': 'a'}, 'legalActions': []}

class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'game'
    def tearDown(self):
        self.tmp.cleanup()
    def writer(self, **kw):
        return PrivateGameJournal(self.root, 'r', PINS, game_id='g', **kw)
    def native(self, writer):
        writer.append('native_transition', TRANSITION, source='native', source_sequence=0)
    def test_seal_manifest_rotation_and_private_projection(self):
        with self.writer(segment_bytes=700, required_sources=('native', 'seat:a')) as w:
            self.native(w)
            sink = RecorderSeatSink(w, 'a')
            event = SeatProvenance('chooseAction', OBS, {'metadata': {'reason': 'supplied explanation'}})
            sink.started(event)
            sink.finished(event)
            w.finish({'kind': 'native_terminal', 'winner_id': 'a'},
                     expected_sources={'native': 1, 'seat:a': 1}, gaps=[])
        report = inspect_journal(self.root)
        self.assertTrue(report['closed'])
        self.assertFalse(report['recording_complete'])
        self.assertGreater(len(list(self.root.glob('*.jsonl'))), 1)
        projected = seat_projection(report, 'a')
        self.assertEqual(len(projected), 2)
        self.assertNotIn('native_transition', json.dumps(projected))
        self.assertEqual(seat_projection(report, 'b'), [])
        manifest = json.loads((self.root / 'manifest.json').read_text())
        self.assertTrue(manifest['ready_for_analysis'])
        self.assertEqual(manifest, publish_finalized_manifest(self.root))
        for f in self.root.iterdir():
            self.assertEqual(f.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
    def test_duplicate_missing_events_and_pending_choice(self):
        with self.writer() as w:
            self.native(w)
            for seq in (0, 2):
                with self.assertRaisesRegex(JournalError, 'gap or duplicate'):
                    w.append('native_transition', TRANSITION, source='native', source_sequence=seq)
            RecorderSeatSink(w, 'a').started(SeatProvenance('chooseAction', OBS, {}))
            w.finish({'kind': 'operator_stop'}, expected_sources={'native': 2}, gaps=['human_menus_unavailable'])
        report = inspect_journal(self.root)
        self.assertFalse(report['recording_complete'])
        self.assertEqual(report['rows'][-1]['payload']['gaps'],
                         ['canonical_training_evidence_unavailable', 'human_menus_unavailable', 'non_native_terminal', 'pending_decisions', 'source_counts'])
        self.assertEqual(report['rows'][-1]['payload']['outcome']['kind'], 'operator_stop')
    def test_crash_prefix_resumes_but_closed_journal_does_not(self):
        with self.writer() as w:
            self.native(w)
        self.assertEqual(inspect_journal(self.root)['issues'], ['missing_terminal'])
        with self.writer() as w:
            self.assertEqual(w.sources, {'native': 1})
            w.finish({'kind': 'abort'}, expected_sources={'native': 1}, gaps=['abort'])
        with self.assertRaisesRegex(JournalError, 'closed'):
            self.writer()
    def test_partial_tail_refuses_resume_preserving_history(self):
        with self.writer(): pass
        path = self.root / '000000.jsonl'
        with path.open('ab') as f: f.write(b'{"unfinished":')
        before = path.read_bytes()
        self.assertIn('partial_tail', inspect_journal(self.root)['issues'])
        with self.assertRaisesRegex(JournalError, 'damaged'): self.writer()
        self.assertEqual(path.read_bytes(), before)
    def test_schema_hash_and_missing_segment_detection(self):
        with self.writer(segment_bytes=300) as w: self.native(w)
        path = self.root / '000000.jsonl'
        row = json.loads(path.read_text())
        row['schema_version'] = 999
        path.write_text(json.dumps(row) + '\n')
        self.assertIn('unsupported_schema', inspect_journal(self.root)['issues'])
    def test_missing_segment_is_detected(self):
        with self.writer(segment_bytes=300) as w: self.native(w)
        (self.root / '000001.jsonl').rename(self.root / '000003.jsonl')
        self.assertIn('missing_segment', inspect_journal(self.root)['issues'])
    def test_resume_changed_pins_is_refused(self):
        with self.writer(): pass
        changed = dict(PINS, gym={'revision': 'different'})
        with self.assertRaisesRegex(JournalError, 'pins mismatch'):
            PrivateGameJournal(self.root, 'r', changed, game_id='g')
    def test_hash_tampering(self):
        with self.writer() as w: self.native(w)
        path = self.root / '000000.jsonl'
        path.write_bytes(path.read_bytes().replace(b'PassPriority', b'FakePriority'))
        self.assertIn('hash_mismatch', inspect_journal(self.root)['issues'])
    def test_limits_preserve_old_data_and_allow_partial_seal(self):
        with self.writer(max_bytes=6000, terminal_reserve=1500) as w:
            self.native(w)
            before = (self.root / '000000.jsonl').read_bytes()
            with self.assertRaisesRegex(JournalError, 'storage limit'):
                w.append('coverage_gap', {'detail': 'x' * 6000})
            self.assertEqual((self.root / '000000.jsonl').read_bytes(), before)
            w.finish({'kind': 'storage_limit'}, expected_sources={'native': 1}, gaps=['storage_limit'])
        self.assertFalse(inspect_journal(self.root)['recording_complete'])
    def test_credentials_and_cross_seat_never_written(self):
        with self.writer() as w:
            for value in ({'headers': {}}, {'extra_headers': {}}, {'auth': 'private'}, {'api_key': 'private'}, {'reconnectToken': 'private'}):
                with self.assertRaisesRegex(JournalError, 'forbidden'):
                    w.append('coverage_gap', value)
            with self.assertRaisesRegex(JournalError, 'matching'):
                w.append('decision_started', {'decision_id': 'd', 'observation': OBS}, seat_id='b')
            with self.assertRaisesRegex(JournalError, 'admin'):
                w.append('native_transition', TRANSITION, seat_id='a')
        self.assertNotIn('private', (self.root / '000000.jsonl').read_text())
    def test_concurrent_process_writer_exclusion(self):
        with self.writer():
            with self.assertRaises(BlockingIOError): self.writer()
    def test_disk_failure_stops_further_writes(self):
        with self.writer() as w:
            with patch('commander_gym.game_journal.os.fsync', side_effect=OSError('synthetic disk error')):
                with self.assertRaises(OSError): self.native(w)
            with self.assertRaisesRegex(JournalError, 'failed'): w.append('coverage_gap', {})
    def test_native_replay_stays_admin_and_is_not_replay_certification(self):
        with self.writer() as w:
            w.native_replay({'version': 4, 'gameId': 'g', 'setup': {'seed': 42},
                             'actions': [{'type': 'PassPriority'}], 'pinnedCards': ['synthetic']})
            w.finish({'kind': 'native_terminal'}, expected_sources={}, gaps=['native_events_unavailable'])
        report = inspect_journal(self.root)
        self.assertFalse(report['recording_complete'])
        self.assertFalse(report['rows'][-1]['payload']['exact_replay_verified'])
        self.assertEqual(seat_projection(report, 'a'), [])
    def test_start_is_durable_before_pilot_runs(self):
        with self.writer() as w:
            sink = RecorderSeatSink(w, 'a')
            root = self.root
            class Pilot:
                name = 'synthetic'
                version = '1'
                def choose(self, observation):
                    self.seen = inspect_journal(root)['rows'][-1]['kind']
                    return ArgentumActionChoice(0)
            pilot = Pilot()
            adapter = GameServerSeatAdapter(pilot, 'a', decision_start_sink=sink.started,
                                            provenance_sink=sink.finished)
            adapter.choose_action({'viewingPlayerId': 'a'}, [
                {'semanticId': 'synthetic:pass', 'action': {'type': 'PassPriority', 'playerId': 'a'}}], None)
            self.assertEqual(pilot.seen, 'decision_started')
        self.assertEqual(inspect_journal(self.root)['rows'][-1]['kind'], 'seat_callback')

    def test_existing_raw_evidence_contract_reused_without_new_training_schema(self):
        from commander_gym.evidence import build_raw_evidence_envelope, validate_raw_evidence_envelope
        from tests.test_evidence import RawEvidenceTests
        fixture = RawEvidenceTests()
        with self.writer() as w:
            self.native(w)
            sink = RecorderSeatSink(w, 'a')
            event = SeatProvenance('chooseAction', OBS, {'channel': 'action', 'actionId': 0})
            sink.started(event)
            sink.finished(event)
            decision_id = inspect_journal(self.root)['rows'][-1]['payload']['decision_id']
            record = replace(fixture.make_record(), game_id='g', decision_id=decision_id)
            run = replace(fixture.make_run(), run_id='r', game_id='g', decision_ids=[decision_id])
            envelope = build_raw_evidence_envelope(run, [record], commander_gym_revision='synthetic-gym')
            w.raw_evidence(envelope)
            with self.assertRaisesRegex(JournalError, 'already attached'): w.raw_evidence(envelope)
            w.finish({'kind': 'native_terminal'}, expected_sources={'native': 1, 'seat:a': 1}, gaps=[])
        report = inspect_journal(self.root)
        self.assertTrue(report['recording_complete'])
        manifest = json.loads((self.root / 'manifest.json').read_bytes())
        artifact = next(a for a in manifest['artifacts'] if a['role'] == 'canonical_raw_evidence')
        validate_raw_evidence_envelope(json.loads((self.root / artifact['path']).read_bytes()))
        self.assertTrue(artifact['artifact_id'].startswith('sha256:'))

    def test_native_receipt_uses_merged_contract_and_preserves_supervisor(self):
        with self.writer() as w:
            receipt = self.root / 'native-terminal.json'
            receipt.write_text(json.dumps({'schemaVersion': 1,
                'source': 'native_ai_websocket_game_over', 'winnerId': 'a'}))
            receipt.chmod(0o600)
            w.finish_from_native_receipt(receipt, expected_sources={}, gaps=['native_events_unavailable'],
                                         supervisor_termination='operator_stop')
        outcome = inspect_journal(self.root)['rows'][-1]['payload']['outcome']
        self.assertEqual(outcome['kind'], 'native_terminal')
        self.assertEqual(outcome['supervisor_termination'], 'operator_stop')

if __name__ == '__main__': unittest.main()
