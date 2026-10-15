import unittest
from unittest.mock import patch
from commander_gym.native_game_capture import NativeGameCapture
from commander_gym.qa_callback_drain import verify_recorder_reopen
from commander_gym.qa_callback_drain import _observe_callback_drain
from commander_gym.game_journal import JournalError
import test_canonical_capture as canonical_fixtures


class QACallbackDrainTests(unittest.TestCase):
    fixture = canonical_fixtures.CanonicalCaptureTests.fixture
    def status(self):
        return dict(ok=True,bootId='test-boot',releaseId='test-release',qaCallbackGateEnabled=True,
                    qaCallbackPaused=True,qaCallbackAdmissionDrained=True,qaCallbackActiveHandlers=0,
                    qaCallbackWaitingHandlers=1,qaCallbackAdmissionFailures=0,qaCallbackGeneration=1)

    def test_actual_durable_joins_required_and_counters_read_twice(self):
        capture,directory=self.fixture();capture.scan()
        reads=[]
        def read():reads.append(1);return self.status()
        receipt=_observe_callback_drain(directory,read)
        self.assertEqual(receipt['callbacks'],4)
        self.assertFalse(receipt['canonicalCompletion'])
        self.assertEqual(len(reads),2)

    def test_reopen_retains_old_prefix_and_allows_only_durable_resume_row(self):
        capture,directory=self.fixture()
        source=directory/'native-000000.ndjson'
        lines=source.read_bytes().splitlines(keepends=True)
        # Fresh synthetic fixture only: keep its genuine native prefix open before first scan.
        source.write_bytes(b''.join(lines[:-1]))
        capture.scan()
        journal=capture._journals[directory.name]
        journal.append('runtime_context', {'recorder_bridge': {'callbackId':'fixture-callback', 'op':'closed'}},
                       source='recorder-bridge', source_sequence=journal.sources.get('recorder-bridge',0))
        receipt=_observe_callback_drain(directory,self.status)
        capture.close()
        resumed=NativeGameCapture(capture.root,capture.declared_pins)
        self.addCleanup(resumed.close);resumed.scan()
        with patch('commander_gym.qa_callback_drain.private_status',side_effect=lambda _:self.status()):
            current=verify_recorder_reopen(directory,'/fixture-only',receipt)
            self.assertEqual(current['nativeHead'],receipt['nativeHead'])
            self.assertGreater(current['journalRows'],receipt['journalRows'])
            with self.assertRaises(JournalError):
                verify_recorder_reopen(directory,'/fixture-only',{**receipt,'journalHead':'wrong'})

    def test_reopen_rejects_changed_recorder_declaration(self):
        capture,directory=self.fixture()
        source=directory/'native-000000.ndjson'
        source.write_bytes(b''.join(source.read_bytes().splitlines(keepends=True)[:-1]))
        capture.scan()
        receipt=_observe_callback_drain(directory,self.status)
        capture.close()
        resumed=NativeGameCapture(capture.root,{**capture.declared_pins,'config':{'changedForTest':True}})
        self.addCleanup(resumed.close);resumed.scan()
        with patch('commander_gym.qa_callback_drain.private_status',side_effect=lambda _:self.status()):
            with self.assertRaises(JournalError):
                verify_recorder_reopen(directory,'/fixture-only',receipt)

    def test_active_disabled_unpaused_changed_generation_or_failure_rejected(self):
        capture,directory=self.fixture();capture.scan()
        for key,value in [('qaCallbackPaused',False),('qaCallbackActiveHandlers',1),
                          ('qaCallbackGateEnabled',False),('qaCallbackAdmissionFailures',1),
                          ('qaCallbackGeneration',True)]:
            with self.subTest(key=key),self.assertRaises(JournalError):
                _observe_callback_drain(directory,lambda:{**self.status(),key:value})
        statuses=iter([self.status(),{**self.status(),'qaCallbackGeneration':2}])
        with self.assertRaises(JournalError):_observe_callback_drain(directory,lambda:next(statuses))

    def test_expanded_journal_and_native_source_have_independent_bounds(self):
        from commander_gym.game_journal import inspect_journal, _json
        capture,directory=self.fixture();capture.scan()
        journal_bytes=sum(len(_json(row)) for row in inspect_journal(directory)['rows'])
        native_bytes=(directory/'native-000000.ndjson').stat().st_size
        with patch('commander_gym.qa_callback_drain.MAX_JOURNAL_BYTES',journal_bytes):
            self.assertEqual(_observe_callback_drain(directory,self.status)['callbacks'],4)
        with patch('commander_gym.qa_callback_drain.MAX_JOURNAL_BYTES',journal_bytes-1):
            with self.assertRaises(JournalError):_observe_callback_drain(directory,self.status)
        with patch('commander_gym.qa_callback_drain.MAX_BYTES',native_bytes-1):
            with self.assertRaises(JournalError):_observe_callback_drain(directory,self.status)

    def test_failed_stale_cancelled_missing_and_mismatched_never_drain(self):
        for variant in ('failed','stale','cancelled','missing_result','unmatched','bad_resume','duplicate_resume_checkpoint','callback_before_resume'):
            capture,directory=self.fixture(variant);capture.scan()
            with self.subTest(variant=variant),self.assertRaises(JournalError):
                _observe_callback_drain(directory,self.status)


if __name__ == '__main__':unittest.main()
