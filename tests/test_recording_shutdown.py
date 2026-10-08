"""Cooperative cancellation preserves durable evidence and restart recovery."""
import threading
import time
import unittest
from unittest.mock import Mock, patch

from commander_gym import game_journal, native_game_capture, record_codec
from commander_gym.game_journal import inspect_journal, PrivateGameJournal, verify_finalized_manifest
from commander_gym.native_game_capture import NativeGameCapture, read_native_source
from commander_gym.record_codec import ScanCancelled
from commander_gym.recording_health import RecordingHealthReporter
from tests import test_native_game_capture as fixtures


class RecordingShutdownTests(unittest.TestCase):
    setUp, tearDown, add = fixtures.NativeGameCaptureTests.setUp, fixtures.NativeGameCaptureTests.tearDown, fixtures.NativeGameCaptureTests.add

    def snapshot(self):
        return {p.name: p.read_bytes() for p in self.game.iterdir() if p.is_file()}

    def restart(self):
        self.capture.close()
        self.capture = NativeGameCapture(self.root, self.pins)
        self.capture.scan()
        report = inspect_journal(self.game)
        self.assertFalse(report['closed'])
        self.assertFalse((self.game / 'manifest.json').exists())
        native = [row for row in report['rows'] if row['source'] == 'native']
        self.assertEqual([row['source_sequence'] for row in native], list(range(self.sequence)))
        return report

    def test_stop_during_cold_native_validation_preserves_source_and_no_publication(self):
        before = self.snapshot()
        decode = native_game_capture.decode_record
        def interrupt(*args, **kwargs):
            value = decode(*args, **kwargs)
            self.capture.request_stop()
            return value
        with patch.object(native_game_capture, 'decode_record', side_effect=interrupt):
            self.capture.scan()
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.capture.errors)
        self.assertNotIn(self.game.name, self.capture._cursors)
        self.restart()

    def test_stop_during_cold_journal_validation_never_calls_corrupt_or_finalizes(self):
        self.capture.scan()
        self.capture.close()
        self.capture = NativeGameCapture(self.root, self.pins)
        before = self.snapshot()
        decode = game_journal.decode_record
        def interrupt(*args, **kwargs):
            value = decode(*args, **kwargs)
            self.capture.request_stop()
            return value
        with patch.object(game_journal, 'decode_record', side_effect=interrupt):
            self.capture.scan()
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.capture.errors)
        self.restart()

    def test_stop_after_durable_import_preserves_prefix_and_restart_imports_once(self):
        self.add('state_checkpoint', {'after': {'synthetic': 'new'}})
        append = PrivateGameJournal.append
        def interrupt(journal, kind, payload, **kwargs):
            result = append(journal, kind, payload, **kwargs)
            if kwargs.get('source') == 'native':
                self.capture.request_stop()
            return result
        with patch.object(PrivateGameJournal, 'append', autospec=True, side_effect=interrupt):
            self.capture.scan()
        prefix = (self.game / '000000.jsonl').read_bytes()
        self.assertEqual(inspect_journal(self.game)['sources']['native'], 1)
        self.assertFalse((self.game / 'manifest.json').exists())
        self.restart()
        self.assertTrue((self.game / '000000.jsonl').read_bytes().startswith(prefix))

    def test_cancel_after_real_terminal_durability_recovers_publication_on_restart(self):
        fixtures.NativeGameCaptureTests.terminal(self)
        append = PrivateGameJournal.append
        def interrupt(journal, kind, payload, **kwargs):
            result = append(journal, kind, payload, **kwargs)
            if kind == 'terminal':
                self.capture.request_stop()
            return result
        with patch.object(PrivateGameJournal, 'append', autospec=True, side_effect=interrupt):
            self.capture.scan()
        self.assertTrue(inspect_journal(self.game)['closed'])
        self.assertFalse((self.game / 'manifest.json').exists())
        prefix = (self.game / '000000.jsonl').read_bytes()
        self.capture.close()
        self.capture = NativeGameCapture(self.root, self.pins)
        self.capture.scan()
        manifest = verify_finalized_manifest(self.game)
        self.assertEqual(manifest['outcome']['kind'], 'native_terminal')
        self.assertEqual(manifest['outcome']['winner_id'], 'seat-a')
        self.assertEqual((self.game / '000000.jsonl').read_bytes(), prefix)

    def test_physical_reader_checks_cancel_between_large_legacy_chunks(self):
        import io
        stop = threading.Event()
        class InterruptingStream(io.BytesIO):
            def readline(inner, limit):
                self.assertLessEqual(limit, 1024 * 1024)
                block = super().readline(limit)
                stop.set()
                return block
        with self.assertRaises(ScanCancelled):
            list(record_codec.physical_lines(InterruptingStream(b'x' * (2 * 1024 * 1024) + b'\n'),
                                            cancel=stop.is_set))

    def test_lazy_reiterations_and_file_hashing_observe_cancellation(self):
        self.capture.scan()
        stop = threading.Event()
        native = read_native_source(self.game, cancel=stop.is_set)
        journal = inspect_journal(self.game, cancel=stop.is_set)['rows']
        stop.set()
        for operation in (lambda: list(native), lambda: list(journal),
                          lambda: record_codec.file_identity(self.source, cancel=stop.is_set),
                          lambda: inspect_journal(self.game, cancel=stop.is_set)):
            with self.assertRaises(ScanCancelled): operation()

    def test_close_never_scans_even_when_new_native_rows_exist(self):
        self.capture.scan()
        self.add('state_checkpoint', {'after': {'synthetic': 'pending'}})
        before = self.snapshot()
        with patch.object(self.capture, 'scan', side_effect=AssertionError('close must not scan')):
            self.capture.close()
        self.assertEqual(self.snapshot(), before)
        self.restart()

    def test_reporter_close_interrupts_synchronous_cold_tick_and_skips_heartbeat(self):
        reporter = RecordingHealthReporter(self.capture, self.root / 'unused.sock')
        reporter.exchange = Mock(side_effect=AssertionError('stopped tick must not report'))
        entered = threading.Event()
        decode = native_game_capture.decode_record
        def slow_decode(*args, **kwargs):
            entered.set()
            deadline = time.monotonic() + 2
            while not self.capture.stop_requested() and time.monotonic() < deadline:
                time.sleep(.001)
            self.assertTrue(self.capture.stop_requested())
            return decode(*args, **kwargs)
        with patch.object(native_game_capture, 'decode_record', side_effect=slow_decode):
            worker = threading.Thread(target=reporter.tick)
            worker.start()
            self.assertTrue(entered.wait(2))
            start = time.monotonic()
            reporter.close()
            self.capture.close()
            worker.join(1)
            self.assertLess(time.monotonic() - start, 1)
            self.assertFalse(worker.is_alive())
        reporter.exchange.assert_not_called()
        self.restart()

    def test_reporter_does_not_acknowledge_health_after_stop_during_status_exchange(self):
        reporter = RecordingHealthReporter(self.capture, self.root / 'unused.sock')
        def interrupt(request):
            reporter.request_stop()
            return {}
        reporter.exchange = Mock(side_effect=interrupt)
        reporter.build_report = Mock(side_effect=AssertionError('stopped reporter must not build health'))
        reporter.tick()
        reporter.exchange.assert_called_once_with({'protocol': 1, 'op': 'status'})
        reporter.build_report.assert_not_called()

    def test_reporter_close_reports_noncooperating_worker(self):
        reporter = RecordingHealthReporter(self.capture, self.root / 'unused.sock')
        reporter._thread = Mock()
        reporter._thread.is_alive.return_value = True
        with self.assertRaises(TimeoutError): reporter.close()
        reporter._thread.join.assert_called_once_with(timeout=5)

    def test_close_reports_deadline_for_noncooperating_scan_lock(self):
        acquired, release = threading.Event(), threading.Event()
        def hold():
            with self.capture._lock:
                acquired.set()
                release.wait(8)
        worker = threading.Thread(target=hold)
        worker.start()
        self.assertTrue(acquired.wait(1))
        start = time.monotonic()
        try:
            with self.assertRaises(TimeoutError): self.capture.close()
            self.assertLess(time.monotonic() - start, 5.5)
        finally:
            release.set()
            worker.join(1)


if __name__ == '__main__':
    unittest.main()
