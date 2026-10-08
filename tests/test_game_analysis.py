import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from commander_gym.game_journal import (
    PIN_KEYS, PrivateGameJournal, JournalError,
    discover_finalized_manifests, verify_finalized_manifest,
)
from commander_gym.game_analysis import (claim_analysis, finish_analysis, renew_analysis,
    fail_analysis, prepare_notification, record_notification)

class GameAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.runs = Path(self.tmp.name)
        self.game = self.runs / 'synthetic'
        pins = {key: None for key in PIN_KEYS}
        with PrivateGameJournal(self.game, 'run', pins, game_id='game') as journal:
            journal.finish({'kind': 'operator_stop', 'supervisor_termination': 'operator_stop'},
                           expected_sources={}, gaps=['native_gameover_unavailable'])
    def tearDown(self): self.tmp.cleanup()
    def report(self, claim):
        return {key: claim[key] for key in ('run_id', 'artifact_hash', 'analysis_version')}
    def test_finalized_abort_discovered_as_incomplete_not_native_terminal(self):
        discovered = discover_finalized_manifests(self.runs)
        self.assertEqual(len(discovered), 1)
        self.assertFalse(discovered[0]['recording_complete'])
        self.assertEqual(discovered[0]['outcome']['kind'], 'operator_stop')
    def test_claim_report_and_completed_deduplication(self):
        claim = claim_analysis(self.game, '1.0.0')
        self.assertEqual(claim['claim_status'], 'claimed')
        self.assertEqual(claim_analysis(self.game, '1.0.0')['claim_status'], 'busy')
        receipt = finish_analysis(self.game, '1.0.0', claim['claim_id'], self.report(claim))
        self.assertEqual(receipt['status'], 'completed')
        self.assertEqual(claim_analysis(self.game, '1.0.0')['claim_status'], 'already_completed')
        self.assertEqual(claim_analysis(self.game, '1.0.1')['claim_status'], 'claimed')
    def test_stale_claim_resume_and_wrong_worker_rejected(self):
        with patch('commander_gym.game_analysis.time.time', return_value=100):
            old = claim_analysis(self.game, '1.0.0', lease_seconds=10)
        with patch('commander_gym.game_analysis.time.time', return_value=111):
            new = claim_analysis(self.game, '1.0.0')
        self.assertTrue(new['resuming'])
        with self.assertRaisesRegex(JournalError, 'identity mismatch'):
            finish_analysis(self.game, '1.0.0', old['claim_id'], self.report(old))
        finish_analysis(self.game, '1.0.0', new['claim_id'], self.report(new))
    def test_missing_report_never_stays_completed(self):
        claim = claim_analysis(self.game, '1.0.0')
        finish_analysis(self.game, '1.0.0', claim['claim_id'], self.report(claim))
        (self.game / 'analysis/1.0.0/report.json').unlink()
        with self.assertRaisesRegex(JournalError, 'missing or changed'):
            claim_analysis(self.game, '1.0.0')
    def test_one_corrupt_run_does_not_stop_other_discovery(self):
        bad = self.runs / 'bad'
        with PrivateGameJournal(bad, 'bad', {key: None for key in PIN_KEYS}) as w:
            w.finish({'kind': 'operator_stop'}, expected_sources={}, gaps=['partial'])
        (bad / '000000.jsonl').write_text('null\n')
        discovered = discover_finalized_manifests(self.runs)
        self.assertEqual([r['run_id'] for r in discovered], ['run'])
    def test_manifest_tamper_and_artifact_tamper_fail_discovery(self):
        manifest = json.loads((self.game / 'manifest.json').read_text())
        manifest['outcome'] = {'kind': 'native_terminal'}
        (self.game / 'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaisesRegex(JournalError, 'does not match'):
            verify_finalized_manifest(self.game)
        self.assertEqual(discover_finalized_manifests(self.runs), [])
    def test_worker_renewal_failure_and_immediate_resume(self):
        claim = claim_analysis(self.game, '1.0.0', worker_id='worker-a')
        with self.assertRaises(JournalError):
            renew_analysis(self.game, '1.0.0', claim['claim_id'], 'worker-b')
        renewed = renew_analysis(self.game, '1.0.0', claim['claim_id'], 'worker-a', lease_seconds=7200)
        self.assertGreater(renewed['lease_expires_at'], claim['lease_expires_at'])
        failed = fail_analysis(self.game, '1.0.0', claim['claim_id'], 'worker-a', 'source_unavailable')
        self.assertEqual(failed['status'], 'failed')
        resumed = claim_analysis(self.game, '1.0.0', worker_id='worker-b')
        self.assertEqual(resumed['claim_status'], 'claimed')
        self.assertTrue(resumed['resuming'])
        self.assertEqual(resumed['worker_id'], 'worker-b')
    def test_notification_uncertain_delivery_never_auto_retries(self):
        claim = claim_analysis(self.game, '1.0.0')
        finish_analysis(self.game, '1.0.0', claim['claim_id'], self.report(claim))
        first = prepare_notification(self.game, '1.0.0', 'worker-a')
        self.assertTrue(first['send_allowed'])
        second = prepare_notification(self.game, '1.0.0', 'worker-b')
        self.assertFalse(second['send_allowed'])
        self.assertEqual(second['status'], 'uncertain')
        record_notification(self.game, '1.0.0', first['delivery_id'], outcome='delivered',
                            delivery_receipt={'message_id': 'synthetic-confirmation'})
        self.assertFalse(prepare_notification(self.game, '1.0.0', 'worker-a')['send_allowed'])
    def test_definite_not_sent_can_retry_and_retains_observed_receipt(self):
        claim = claim_analysis(self.game, '1.0.0')
        finish_analysis(self.game, '1.0.0', claim['claim_id'], self.report(claim))
        first = prepare_notification(self.game, '1.0.0', 'worker-a')
        row = record_notification(self.game, '1.0.0', first['delivery_id'], outcome='not_sent',
                                  delivery_receipt={'result': 'synthetic-no-send'})
        self.assertEqual(row['receipt']['result'], 'synthetic-no-send')
        retry = prepare_notification(self.game, '1.0.0', 'worker-a')
        self.assertTrue(retry['send_allowed'])
        self.assertNotIn('receipt', retry)
        self.assertEqual(retry['attempts'][0]['receipt']['result'], 'synthetic-no-send')
    def test_notification_and_failure_metadata_reject_credentials_before_mutation(self):
        secret = 'sk-proj-' + 'syntheticcredential123456789'
        claim = claim_analysis(self.game, '1.0.0', worker_id='worker-a')
        with self.assertRaisesRegex(JournalError, 'forbidden'):
            fail_analysis(self.game, '1.0.0', claim['claim_id'], 'worker-a', secret)
        finish_analysis(self.game, '1.0.0', claim['claim_id'], self.report(claim))
        ledger = self.game / 'analysis/1.0.0/review.json'
        before = ledger.read_bytes()
        with self.assertRaisesRegex(JournalError, 'forbidden'):
            prepare_notification(self.game, '1.0.0', secret)
        self.assertEqual(ledger.read_bytes(), before)
    def test_private_directory_creation_syncs_parent(self):
        from commander_gym.game_journal import _private_dir
        fresh = self.runs / 'fresh'
        with patch('commander_gym.game_journal._sync_dir') as sync:
            _private_dir(fresh, create=True)
            sync.assert_called_once_with(self.runs)
    def test_manifest_shape_damage_does_not_stop_other_runs(self):
        bad = self.runs / 'bad'
        with PrivateGameJournal(bad, 'bad', {key: None for key in PIN_KEYS}) as w:
            w.finish({'kind': 'operator_stop'}, expected_sources={}, gaps=['partial'])
        for malformed in ('null', '[]', '{"artifacts":[null]}'):
            (bad / 'manifest.json').write_text(malformed + '\n')
            self.assertEqual([r['run_id'] for r in discover_finalized_manifests(self.runs)], ['run'])
    def test_finalized_manifest_is_immutable_and_revision_needs_new_identity(self):
        from commander_gym.game_journal import publish_finalized_manifest
        original = (self.game / 'manifest.json').read_bytes()
        changed = json.loads(original)
        changed['artifact_hash'] = 'changed'
        (self.game / 'manifest.json').write_text(json.dumps(changed))
        with self.assertRaisesRegex(JournalError, 'immutable'):
            publish_finalized_manifest(self.game)
    def test_report_secret_and_path_escape_rejected(self):
        with self.assertRaises(JournalError): claim_analysis(self.game, '../elsewhere')
        claim = claim_analysis(self.game, '1.0.0')
        with self.assertRaisesRegex(JournalError, 'forbidden'):
            finish_analysis(self.game, '1.0.0', claim['claim_id'], {**self.report(claim), 'headers': {}})
        self.assertFalse((self.game / 'analysis/1.0.0/report.json').exists())
    def test_crash_after_report_before_completion_resumes_without_rewriting_report(self):
        claim = claim_analysis(self.game, '1.0.0')
        from commander_gym.game_analysis import _atomic_private as actual
        def fail_ledger(path, data):
            if path.name == 'review.json': raise OSError('synthetic crash')
            actual(path, data)
        with patch('commander_gym.game_analysis._atomic_private', side_effect=fail_ledger):
            with self.assertRaises(OSError):
                finish_analysis(self.game, '1.0.0', claim['claim_id'], self.report(claim))
        receipt = finish_analysis(self.game, '1.0.0', claim['claim_id'], self.report(claim))
        self.assertEqual(receipt['status'], 'completed')

if __name__ == '__main__': unittest.main()
