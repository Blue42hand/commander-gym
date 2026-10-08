import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from commander_gym.game_journal import JournalError
from commander_gym.recording_health import RecordingHealthReporter
from tests.test_native_game_capture import NativeGameCaptureTests as _Fixture


class RecordingHealthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.capture = Mock(root=self.root, declared_pins={'gym': 'b' * 40})
        self.capture.operational_metrics.return_value = {'healthy': True, 'pendingRecordWrites': 0, 'durableBytes': 123}
        self.reporter = RecordingHealthReporter(self.capture, self.root / 'lifecycle.sock')
        self.status = dict(bootId='synthetic-boot', releaseId='c' * 64, engineSha='a' * 40,
                           activeGames=0, pendingActivities=0, inFlightAdmissions=0,
                           drainId='synthetic-drain', drainAcknowledged=True)
        self.proof = dict(schemaVersion=1, bootId='synthetic-boot', releaseId='c' * 64,
                          engineSha='a' * 40, gymSha='b' * 40, utc='2026-01-01T00:00:00+00:00',
                          recoveryComplete=True, recordingHealthy=True, producerCoverageComplete=True,
                          registeredSources=0, connectedSources=0, pendingRecordWrites=0)
        self.now = dt.datetime.fromisoformat(self.proof['utc']).timestamp() + 1
        self.write()

    def tearDown(self): self.temp.cleanup()

    def write(self):
        path = self.root / '.native-health.json'
        path.write_text(json.dumps(self.proof)); path.chmod(0o600)

    def test_zero_gym_seats_requires_actual_fresh_native_proof(self):
        report = self.reporter.build_report(self.status, now=self.now)
        self.assertTrue(report['drainComplete'])
        self.assertEqual(report['durableBytes'], 123)
        self.assertEqual(set(report), {'protocol', 'op', 'bootId', 'releaseId', 'gymSha',
            'recordingSchemaVersion', 'recoveryComplete', 'recordingHealthy', 'producerCoverageComplete',
            'pendingRecordWrites', 'durableBytes', 'drainId', 'drainComplete'})
        with self.assertRaises(JournalError): self.reporter.build_report(self.status, now=self.now + 10)
        (self.root / '.native-health.json').unlink()
        with self.assertRaises(OSError): self.reporter.build_report(self.status, now=self.now)

    def test_wrong_epoch_pin_permissions_or_counter_refuses_health(self):
        for key, value in [('bootId', 'wrong'), ('engineSha', 'wrong'), ('gymSha', 'wrong'),
                           ('pendingRecordWrites', False), ('recoveryComplete', 1)]:
            original = self.proof[key]; self.proof[key] = value; self.write()
            with self.assertRaises(JournalError): self.reporter.build_report(self.status, now=self.now)
            self.proof[key] = original
        self.write(); (self.root / '.native-health.json').chmod(0o644)
        with self.assertRaises(JournalError): self.reporter.build_report(self.status, now=self.now)

    def test_callbacks_admissions_lobbies_and_writer_errors_block_drain(self):
        for key in ('activeGames', 'pendingActivities', 'inFlightAdmissions'):
            self.assertFalse(self.reporter.build_report({**self.status, key: 1}, now=self.now)['drainComplete'])
        self.capture.operational_metrics.return_value['pendingRecordWrites'] = 1
        self.assertFalse(self.reporter.build_report(self.status, now=self.now)['drainComplete'])
        self.capture.operational_metrics.return_value['pendingRecordWrites'] = 0
        self.proof['connectedSources'] = 0; self.proof['registeredSources'] = 1; self.write()
        self.assertFalse(self.reporter.build_report(self.status, now=self.now)['producerCoverageComplete'])
        self.proof['recordingHealthy'] = False; self.write()
        self.assertFalse(self.reporter.build_report(self.status, now=self.now)['recordingHealthy'])

    def test_reporter_never_attempts_updater_operations_or_fabricates_health(self):
        self.reporter.exchange = Mock(side_effect=JournalError('synthetic unavailable'))
        self.reporter.tick()
        self.assertEqual(self.reporter.last_error, 'JournalError')
        self.reporter.exchange.assert_called_once_with({'protocol': 1, 'op': 'status'})

    def test_disposition_snapshot_publishes_only_after_successful_recording_exchange(self):
        self.capture.operational_metrics.return_value.update(acknowledgedIncomplete=1,
            acknowledgedIncompleteRegistrySha256='d'*64)
        def exchange(request):
            self.assertIsNone(self.reporter.last_successful_disposition)
            return self.status if request['op'] == 'status' else {'ok': True}
        self.reporter.exchange = Mock(side_effect=exchange)
        with patch('commander_gym.recording_health.time.time', return_value=self.now):
            self.reporter.tick()
        snapshot = self.reporter.last_successful_disposition
        self.assertEqual(snapshot['acknowledgedIncomplete'], 1)
        self.assertEqual(snapshot['registrySha256'], 'd'*64)
        self.assertEqual(snapshot['observedUnix'], self.now)
        self.assertEqual(snapshot['report'], self.reporter.last_successful_report)
        wire = self.reporter.exchange.call_args.args[0]
        self.assertNotIn('acknowledgedIncomplete', wire)
        self.assertNotIn('registrySha256', wire)
        snapshot['report']['pendingRecordWrites'] = 999
        self.assertEqual(self.reporter.last_successful_report['pendingRecordWrites'], 0)
        self.reporter.exchange.side_effect = JournalError('synthetic failure')
        self.reporter.tick()
        self.assertIsNone(self.reporter.last_successful_disposition)
        self.assertIsNone(self.reporter.last_successful_report)

    def test_stop_during_successful_exchange_cannot_publish_disposition(self):
        def exchange(request):
            if request['op'] == 'recording': self.reporter.request_stop()
            return self.status if request['op'] == 'status' else {'ok': True}
        self.reporter.exchange = Mock(side_effect=exchange)
        with patch('commander_gym.recording_health.time.time', return_value=self.now): self.reporter.tick()
        self.assertIsNone(self.reporter.last_successful_disposition)

    def test_acknowledged_count_requires_exact_registry_digest(self):
        self.capture.operational_metrics.return_value['acknowledgedIncomplete'] = 1
        with self.assertRaises(JournalError): self.reporter.build_report(self.status, now=self.now)
        self.capture.operational_metrics.return_value['acknowledgedIncompleteRegistrySha256'] = 'wrong'
        with self.assertRaises(JournalError): self.reporter.build_report(self.status, now=self.now)


class OperationalMetricsTests(unittest.TestCase):
    setUp, tearDown, add, terminal = _Fixture.setUp, _Fixture.tearDown, _Fixture.add, _Fixture.terminal

    def test_orphan_unsealed_prefix_cannot_acknowledge_drain(self):
        self.capture.scan()
        metrics = self.capture.operational_metrics()
        self.assertTrue(metrics["healthy"])
        self.assertGreater(metrics["pendingRecordWrites"], 0)

    def test_terminal_requires_verified_manifest_before_zero_pending(self):
        self.terminal(); self.capture.scan()
        self.assertGreater(self.capture.operational_metrics()['pendingRecordWrites'], 0)
        self.capture.scan()
        result = self.capture.operational_metrics()
        self.assertTrue(result['healthy']); self.assertEqual(result['pendingRecordWrites'], 0)
        self.assertGreater(result['durableBytes'], 0)

    def test_partial_native_tail_stays_pending(self):
        self.capture.scan()
        self.add('seat_submission', {'action': 'synthetic'}, seat='seat-a', newline=False)
        self.capture.scan()
        self.assertGreater(self.capture.operational_metrics()['pendingRecordWrites'], 0)

    def test_stall_is_finalized_abort_not_native_rules_result(self):
        self.add('terminal', {'winnerId': None, 'nativeGameOver': True, 'administrativeStall': 'synthetic stall'})
        self.capture.scan()
        from commander_gym.game_journal import verify_finalized_manifest
        result = verify_finalized_manifest(self.game)
        self.assertEqual(result['outcome']['kind'], 'finalized_aborted')
        self.assertIn('administrative_stall_not_native_rules_outcome', result['gaps'])

# Keep the imported fixture out of unittest module discovery.
del _Fixture
