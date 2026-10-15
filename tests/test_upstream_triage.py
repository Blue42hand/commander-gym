import unittest
from unittest.mock import patch
from commander_gym.upstream_triage import main, summarize


class UpstreamTriageTests(unittest.TestCase):
    def comparison(self):
        return {'status': 'ahead', 'total_commits': 1,
                'commits': [{'sha': 'b' * 40, 'commit': {'message': 'Fix masked decision recording'}}],
                'files': [{'filename': 'rules-engine/legality.kt'}]}

    def test_changes_are_evidence_for_review_not_automatic_merge_authority(self):
        report = summarize('a' * 40, 'b' * 40, self.comparison(), owned_integration='owned-branch')
        self.assertEqual(report['state'], 'review-required')
        self.assertTrue(report['importanceIsHeuristic'])
        self.assertIn('security-or-private-data', report['commits'][0]['reviewCategories'])
        self.assertIn('native-rules-or-protocol', report['pathReviewCategories'])
        self.assertEqual(report['weeklySync']['state'], 'held-existing-owner')

    def test_incomplete_commits_or_file_limit_never_advances_cursor(self):
        comparison = self.comparison(); comparison['total_commits'] = 251
        self.assertFalse(summarize('a'*40, 'b'*40, comparison)['cursorAdvanceAllowed'])
        comparison = self.comparison(); comparison['files'] *= 300
        self.assertFalse(summarize('a'*40, 'b'*40, comparison)['cursorAdvanceAllowed'])

    def test_force_push_or_diverged_history_requires_review(self):
        comparison = self.comparison(); comparison['status'] = 'diverged'
        report = summarize('a'*40, 'b'*40, comparison)
        self.assertEqual(report['state'], 'held-nonlinear-history')
        self.assertFalse(report['cursorAdvanceAllowed'])

    def test_unknown_changes_still_require_review(self):
        comparison = self.comparison()
        comparison['commits'][0]['commit']['message'] = 'Update things'
        comparison['files'] = [{'filename': 'misc.txt'}]
        self.assertEqual(summarize('a'*40, 'b'*40, comparison)['state'], 'review-required')

    def test_identical_head_is_quiet_and_keeps_owner(self):
        report = summarize('a'*40, 'a'*40,
                           {'status': 'identical', 'total_commits': 0, 'commits': [], 'files': []},
                           owned_integration='owned')
        self.assertEqual(report['state'], 'unchanged')
        self.assertEqual(report['weeklySync']['state'], 'held-existing-owner')

    def test_invalid_ref_is_rejected_before_any_network_read(self):
        with patch('commander_gym.upstream_triage.github') as github, patch('builtins.print'):
            self.assertEqual(main(['--since', 'main; unexpected']), 1)
        github.assert_not_called()


if __name__ == '__main__':
    unittest.main()
