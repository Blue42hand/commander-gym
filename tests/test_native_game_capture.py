import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from commander_gym.game_journal import (
    JournalError, PIN_KEYS, ZERO, inspect_journal, seat_projection, verify_finalized_manifest,
)
from commander_gym.game_server_seat import SeatProvenance
from commander_gym.native_game_capture import NativeCursor, NativeGameCapture, read_native_source


class NativeGameCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        self.game = self.root / 'synthetic-game'
        self.game.mkdir(mode=0o700)
        self.source = self.game / 'native-000000.ndjson'
        self.source.touch(mode=0o600)
        self.sequence, self.previous = 0, ZERO
        self.pins = {key: None for key in PIN_KEYS}
        self.pins.update(gym='b' * 40, models={'provider': 'synthetic-v1'}, bindings={'catalog_digest': 'synthetic'})
        self.capture = NativeGameCapture(self.root, self.pins)
        self.add('state_checkpoint', {'after': {'hiddenOpponents': ['synthetic-secret-card']}})
        self.add('initialization', {'setup': {'seed': 42, 'players': [{'playerId': 'seat-a', 'deck': ['Forest']}]},
                                    'events': [], 'pinnedCards': ['synthetic-compiled-card']})

    def tearDown(self):
        self.capture.close()
        self.temp.cleanup()

    def add(self, kind, payload, seat=None, newline=True):
        self.sequence += 1
        body = dict(schemaVersion=1, gameId=self.game.name, sequence=self.sequence,
                    previousSha256=self.previous, engineRevision='a' * 40,
                    clockEpoch='synthetic-clock', utc='2026-01-01T00:00:00Z', monotonicNanos=self.sequence,
                    kind=kind, visibility='seat' if seat else 'admin', payload=payload)
        if seat: body['seatId'] = seat
        exact = json.dumps(body, separators=(',', ':'))
        self.previous = hashlib.sha256(exact.encode()).hexdigest()
        wrapper = json.dumps({'body': exact, 'sha256': self.previous})
        with self.source.open('a') as stream: stream.write(wrapper + ('\n' if newline else ''))

    def transition(self):
        self.add('native_transition', {'action': {'type': 'PassPriority', 'playerId': 'seat-a'},
            'beforeStateDigest': 'c' * 64, 'effectiveStateDigest': 'd' * 64,
            'result': {'state': {'counter': 2}, 'events': [{'type': 'SyntheticEvent'}], 'outcome': {'type': 'Done'}}})

    def terminal(self):
        self.add('terminal', {'winnerId': 'seat-a', 'nativeGameOver': True, 'administrativeStall': None})

    def test_human_only_game_finalizes_without_any_policy_callback_and_keeps_seat_boundary(self):
        self.add('seat_observation', {'state': {'viewingPlayerId': 'seat-a', 'ownHand': ['Forest']},
                                     'legalActions': [{'action': {'type': 'PassPriority'}}]}, seat='seat-a')
        self.transition()
        self.terminal()
        self.capture.scan()
        manifest = verify_finalized_manifest(self.game)
        self.assertTrue(manifest['ready_for_analysis'])
        self.assertFalse(manifest['recording_complete'])
        self.assertIn('canonical_game_server_training_adapter_unavailable', manifest['gaps'])
        self.assertEqual(manifest['outcome']['winner_id'], 'seat-a')
        self.assertIn('admin_native_source', [a['role'] for a in manifest['artifacts']])
        report = inspect_journal(self.game)
        self.assertEqual(report['sources']['native'], self.sequence)
        projected = seat_projection(report, 'seat-a')
        self.assertEqual(len(projected), 1)
        self.assertNotIn('synthetic-secret-card', json.dumps(projected))
        before = (self.game / 'manifest.json').read_bytes()
        self.capture.scan()
        self.assertEqual((self.game / 'manifest.json').read_bytes(), before)

    def test_ai_callback_keeps_actual_metadata_and_native_identity_without_admin_input(self):
        sink = self.capture.seat_sink(self.game.name, 'seat-a')
        observation = {'state': {'viewingPlayerId': 'seat-a'}, 'legalActions': [{'action': {'type': 'PassPriority'}}]}
        sink.started(SeatProvenance('chooseAction', observation, {}))
        sink.finished(SeatProvenance('chooseAction', observation,
            {'channel': 'action', 'actionId': 0, 'metadata': {'model': 'synthetic-v1', 'input_tokens': 17,
             'cost_usd': 0, 'retryCount': 1, 'rationale': 'actual synthetic provider rationale'}}))
        self.transition()
        self.terminal()
        self.capture.scan()
        callbacks = [r for r in inspect_journal(self.game)['rows'] if r['kind'] == 'seat_callback']
        self.assertEqual(callbacks[0]['payload']['choice']['metadata']['input_tokens'], 17)
        self.assertEqual(callbacks[0]['payload']['observation'], observation)
        self.assertNotIn('hiddenOpponents', json.dumps(callbacks))

    def test_live_partial_tail_is_pending_then_imported_once_and_incremental_reads_skip_prefix(self):
        cursor = NativeCursor()
        self.assertEqual(len(read_native_source(self.game, cursor)), 2)
        self.assertEqual(read_native_source(self.game, cursor), [])
        self.add('seat_bottom_offer', {'hand': ['Forest']}, seat='seat-a', newline=False)
        self.assertEqual(read_native_source(self.game, cursor), [])
        with self.source.open('a') as stream: stream.write('\n')
        self.assertEqual(len(read_native_source(self.game, cursor)), 1)
        self.capture.scan()
        self.capture.scan()
        self.assertEqual(inspect_journal(self.game)['sources']['native'], 3)
        self.assertFalse((self.game / 'manifest.json').exists())

    def test_human_concession_during_ai_choice_drains_usage_receipt_before_sealing(self):
        sink = self.capture.seat_sink(self.game.name, 'seat-a')
        observation = {'state': {'viewingPlayerId': 'seat-a'}}
        sink.started(SeatProvenance('chooseAction', observation, {}))
        self.terminal()
        self.capture.scan()
        self.assertFalse((self.game / 'manifest.json').exists())
        sink.finished(SeatProvenance('chooseAction', observation, {'metadata': {'output_tokens': 19, 'cost_usd': 0}}))
        self.capture.scan()
        self.assertTrue(verify_finalized_manifest(self.game)['ready_for_analysis'])
        self.assertNotIn('pending_decisions', verify_finalized_manifest(self.game)['gaps'])
        self.assertIn('output_tokens', json.dumps(seat_projection(inspect_journal(self.game), 'seat-a')))

    def test_unrecoverable_pending_callback_seals_only_as_explicit_partial_after_limit(self):
        sink = self.capture.seat_sink(self.game.name, 'seat-a')
        sink.started(SeatProvenance('chooseAction', {'state': {'viewingPlayerId': 'seat-a'}}, {}))
        self.terminal()
        with patch('commander_gym.native_game_capture.time.monotonic', return_value=10): self.capture.scan()
        with patch('commander_gym.native_game_capture.time.monotonic', return_value=611): self.capture.scan()
        manifest = verify_finalized_manifest(self.game)
        self.assertIn('pending_decisions', manifest['gaps'])
        self.assertIn('callback_completion_unavailable_after_terminal_timeout', manifest['gaps'])
        self.assertFalse(manifest['recording_complete'])

    def test_restart_imports_from_durable_prefix_and_preserves_terminal(self):
        self.capture.scan()
        self.capture.close()
        self.capture = NativeGameCapture(self.root, self.pins)
        self.transition()
        self.terminal()
        self.capture.scan()
        report = inspect_journal(self.game)
        self.assertTrue(report['closed'])
        self.assertEqual(report['sources']['native'], self.sequence)
        self.assertEqual(len([r for r in report['rows'] if r['source'] == 'native']), self.sequence)

    def test_crash_after_imported_native_terminal_can_finish_from_durable_source(self):
        self.transition()
        self.terminal()
        with patch('commander_gym.native_game_capture.PrivateGameJournal.finish', side_effect=OSError('synthetic crash')):
            self.capture.scan()
        self.assertFalse((self.game / 'manifest.json').exists())
        self.capture.close()
        self.capture = NativeGameCapture(self.root, self.pins)
        self.capture.scan()
        self.assertTrue(verify_finalized_manifest(self.game)['ready_for_analysis'])

    def test_duplicate_missing_corrupted_and_foreign_source_fail_closed_without_deletion(self):
        original = self.source.read_bytes()
        for altered in (original + original.splitlines(keepends=True)[-1],
                        original.replace(b'"sha256": "', b'"sha256": "f', 1),
                        original.splitlines(keepends=True)[-1]):
            self.source.write_bytes(altered)
            with self.assertRaises(JournalError): read_native_source(self.game)
        self.assertTrue(self.source.exists())
        self.source.write_bytes(original)
        with self.assertRaises(JournalError): self.capture.seat_sink('../foreign', 'seat-a')

    def test_damaged_gym_tail_never_silently_resumed_or_promoted(self):
        self.capture.scan()
        self.capture.close()
        with (self.game / '000000.jsonl').open('ab') as stream: stream.write(b'partial')
        self.capture = NativeGameCapture(self.root, self.pins)
        self.terminal()
        self.capture.scan()
        self.assertFalse((self.game / 'manifest.json').exists())
        self.assertIn(self.game.name, self.capture.errors)
        self.assertTrue((self.game / '000000.jsonl').read_bytes().endswith(b'partial'))

    def test_root_and_exact_pins_required_no_permission_changes(self):
        self.root.chmod(0o755)
        with self.assertRaises(JournalError): NativeGameCapture(self.root, self.pins)
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o755)
        self.root.chmod(0o700)
        with self.assertRaises(JournalError): NativeGameCapture(self.root, {**self.pins, 'gym': 'floating-main'})


if __name__ == '__main__': unittest.main()
