import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from commander_gym.game_journal import (
    PIN_KEYS, PrivateGameJournal, JournalError,
    discover_finalized_manifests, verify_finalized_manifest,
)
from commander_gym.game_analysis import claim_analysis, finish_analysis

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
    def test_manifest_tamper_and_artifact_tamper_fail_discovery(self):
        manifest = json.loads((self.game / 'manifest.json').read_text())
        manifest['outcome'] = {'kind': 'native_terminal'}
        (self.game / 'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaisesRegex(JournalError, 'does not match'):
            verify_finalized_manifest(self.game)
        self.assertEqual(discover_finalized_manifests(self.runs), [])
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
