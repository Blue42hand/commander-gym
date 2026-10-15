"""Nightly preflight rejects unqualified tuples and never invokes deployment."""
import copy
import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch

import test_runtime_release as fixtures
from commander_gym.manual_runtime_profile import ManualRuntimeError, canonical, digest
from commander_gym.release_cadence import (
    REPOSITORIES, REQUIRED_CHECKS, pinned_json, preflight,
)


class ReleaseCadenceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RuntimeReleaseTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.baseline = copy.deepcopy(self.fixture.manifest['profile'])
        self.evidence = {
            'schemaVersion': 1, 'releaseSha256': self.fixture.review,
            'sources': {}, 'catalogValidation': {
                'engineSha': self.baseline['engineSha'], 'gymSha': self.baseline['gymSha'],
                'privateSha': 'c' * 40,
                'catalogFilesSha256': digest(self.baseline['artifacts']['catalog']['files']),
                'collectionIntegrity': True, 'nativeDeckValidation': True,
            },
        }
        for role, repository in REPOSITORIES.items():
            sha = self.baseline.get(role + 'Sha', 'c' * 40)
            self.evidence['sources'][role] = {
                'repository': repository, 'sha': sha,
                'review': {'sha': sha, 'verdict': 'approve', 'evidenceSha256': 'd' * 64},
                'checks': [{'workflow': workflow, 'name': name, 'event': 'push',
                            'branch': 'main', 'headSha': sha, 'testedSha': sha,
                            'runId': 1, 'runAttempt': 1, 'conclusion': 'success'}
                           for workflow, name in sorted(REQUIRED_CHECKS[role])],
            }

    def run_preflight(self, **kwargs):
        return preflight(self.fixture.bundle, self.fixture.review, self.baseline,
                         self.evidence, current_runtime='0' * 64, sequence=2,
                         now=kwargs.get('now', 1000))

    def test_complete_bundle_is_report_only_with_no_install_or_provider_calls(self):
        with patch('commander_gym.runtime_release.stage_bundle') as stage, \
             patch('commander_gym.runtime_release.promote') as promote, \
             patch('subprocess.run') as process:
            result = self.run_preflight()
        self.assertEqual(result['state'], 'held-disabled')
        self.assertTrue(result['preflightPassed'])
        self.assertFalse(result['promotionEnabled'])
        self.assertEqual(result['sources']['private'], 'c' * 40)
        stage.assert_not_called(); promote.assert_not_called(); process.assert_not_called()
        self.assertNotIn(str(self.fixture.root), str(result))

    def test_every_source_requires_exact_main_push_not_pr_merge_or_dispatch(self):
        for role in REPOSITORIES:
            check = self.evidence['sources'][role]['checks'][0]
            for key, value in [('event', 'pull_request'), ('event', 'workflow_dispatch'),
                               ('branch', 'candidate'), ('testedSha', 'f' * 40),
                               ('headSha', 'f' * 40), ('conclusion', 'skipped'),
                               ('conclusion', 'failure'), ('runAttempt', 0), ('runId', True)]:
                with self.subTest(role=role, key=key, value=value):
                    old = check[key]; check[key] = value
                    with self.assertRaisesRegex(ManualRuntimeError, 'cadence_check_failed'):
                        self.run_preflight()
                    check[key] = old

    def test_missing_and_duplicate_required_checks_fail_closed(self):
        for role in REPOSITORIES:
            checks = self.evidence['sources'][role]['checks']
            removed = checks.pop()
            with self.assertRaises(ManualRuntimeError): self.run_preflight()
            checks.append(removed)
            checks.append(copy.deepcopy(removed))
            with self.assertRaisesRegex(ManualRuntimeError, 'cadence_duplicate_check'):
                self.run_preflight()
            checks.pop()

    def test_wrong_workflow_cannot_spoof_required_job_name(self):
        self.evidence['sources']['private']['checks'][0]['workflow'] = '.github/workflows/other.yml'
        with self.assertRaisesRegex(ManualRuntimeError, 'cadence_missing_check'):
            self.run_preflight()

    def test_rejected_or_wrong_commit_review_blocks_each_source(self):
        for role in REPOSITORIES:
            review = self.evidence['sources'][role]['review']
            for key, value in [('sha', 'f' * 40), ('verdict', 'request-changes')]:
                old = review[key]; review[key] = value
                with self.assertRaisesRegex(ManualRuntimeError, 'cadence_review'):
                    self.run_preflight()
                review[key] = old

    def test_private_catalog_proof_is_bound_to_all_three_sources_and_bytes(self):
        proof = self.evidence['catalogValidation']
        for key in proof:
            old = proof[key]
            proof[key] = False if type(old) is bool else 'f' * len(old)
            with self.subTest(key=key):
                with self.assertRaisesRegex(ManualRuntimeError, 'cadence_catalog_identity'):
                    self.run_preflight()
            proof[key] = old

    def test_new_pilot_or_binding_requires_separate_baseline_approval(self):
        # The candidate is unchanged; a different baseline cannot authorize it.
        self.baseline['catalog']['pilotFingerprint'] = 'f' * 64
        with self.assertRaisesRegex(ManualRuntimeError, 'cadence_feature_change'):
            self.run_preflight()

    def test_stale_qualification_and_changed_bundle_rejected(self):
        with self.assertRaisesRegex(ManualRuntimeError, 'qualification_stale'):
            self.run_preflight(now=100000)
        name = next(iter(self.fixture.manifest['files']))
        (self.fixture.bundle / name).write_bytes(b'tampered')
        with self.assertRaisesRegex(ManualRuntimeError, 'bundle_file_digest'):
            self.run_preflight()

    def test_previous_runtime_or_sequence_mismatch_does_not_reapprove(self):
        for previous, sequence in [('f' * 64, 2), ('0' * 64, 3)]:
            with self.assertRaisesRegex(ManualRuntimeError, 'approval_tuple_mismatch'):
                preflight(self.fixture.bundle, self.fixture.review, self.baseline,
                          self.evidence, current_runtime=previous, sequence=sequence, now=1000)

    def test_evidence_cannot_be_reused_for_another_release(self):
        self.evidence['releaseSha256'] = 'f' * 64
        with self.assertRaisesRegex(ManualRuntimeError, 'cadence_evidence_identity'):
            self.run_preflight()

    def test_external_evidence_digest_and_duplicate_keys_are_enforced(self):
        path = self.fixture.root / 'evidence.json'
        raw = canonical(self.evidence); path.write_bytes(raw)
        self.assertEqual(pinned_json(path, hashlib.sha256(raw).hexdigest()), self.evidence)
        with self.assertRaisesRegex(ManualRuntimeError, 'cadence_input_digest'):
            pinned_json(path, 'f' * 64)
        raw = b'{"schemaVersion":1,"schemaVersion":1}'; path.write_bytes(raw)
        with self.assertRaisesRegex(ManualRuntimeError, 'duplicate_json_key'):
            pinned_json(path, hashlib.sha256(raw).hexdigest())


if __name__ == '__main__':
    unittest.main()
