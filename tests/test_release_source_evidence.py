import base64
import copy
import hashlib
import unittest
from unittest.mock import patch

from commander_gym.manual_runtime_profile import ManualRuntimeError
from commander_gym.release_cadence import REPOSITORIES, REQUIRED_CHECKS
from commander_gym.release_source_evidence import collect, GYM_WORKFLOW, PIN, GitHub


class SourceEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.policy = {'schemaVersion': 1, 'repositories': {}}
        self.responses = {}
        self.shas = {'engine': 'e' * 40, 'gym': 'a' * 40, 'private': 'b' * 40}
        self.run_keys = []
        self.job_keys = []
        counter = 10
        for index, (role, repo) in enumerate(REPOSITORIES.items(), 1):
            sha = self.shas[role]
            self.responses[f'repos/{repo}'] = {'id': index, 'full_name': repo, 'default_branch': 'main'}
            self.responses[f'repos/{repo}/commits/main'] = {'sha': sha}
            self.responses[f'repos/{repo}/compare/{sha}...{sha}'] = {
                'status': 'identical', 'merge_base_commit': {'sha': sha}}
            spec = {'repository': repo, 'id': index, 'workflows': {}}
            for path in sorted({p for p, _ in REQUIRED_CHECKS[role]}):
                counter += 1
                raw = b'trusted workflow\n'
                if role == 'gym':
                    raw += (b'          repository: Blue42hand/argentum-engine\n'
                            b'          # Pinned native candidate for the human-versus-Luna milestone.\n'
                            b'          ref: ' + self.shas['engine'].encode() + b'\n')
                normalized = PIN.sub(rb'\1<ENGINE_SHA>\3', raw) if role == 'gym' else raw
                spec['workflows'][path] = {'id': counter, 'sha256': hashlib.sha256(normalized).hexdigest()}
                self.responses[f'repos/{repo}/contents/{path}?ref={sha}'] = {
                    'type': 'file', 'path': path, 'encoding': 'base64', 'size': len(raw),
                    'content': base64.b64encode(raw).decode()}
                self.responses[f'repos/{repo}/actions/workflows/{counter}'] = {
                    'id': counter, 'path': path, 'state': 'active'}
                run = {'id': counter * 100, 'run_attempt': 1, 'workflow_id': counter, 'path': path,
                       'repository': {'id': index, 'full_name': repo}, 'head_repository': {'id': index},
                       'head_sha': sha, 'head_branch': 'main', 'event': 'push', 'status': 'completed',
                       'conclusion': 'success', 'updated_at': '2026-10-15T12:00:00Z',
                       'run_started_at': '2026-10-15T11:00:00Z'}
                run_key = f'repos/{repo}/actions/workflows/{counter}/runs?head_sha={sha}&event=push&branch=main&per_page=100'
                self.responses[run_key] = {'total_count': 1, 'workflow_runs': [run]}
                self.run_keys.append(run_key)
                self.responses[f'repos/{repo}/actions/runs/{run["id"]}'] = run
                jobs = [{'name': name, 'run_id': run['id'], 'run_attempt': 1, 'head_sha': sha,
                         'head_branch': 'main', 'status': 'completed', 'conclusion': 'success'}
                        for p, name in sorted(REQUIRED_CHECKS[role]) if p == path]
                job_key = f'repos/{repo}/actions/runs/{run["id"]}/attempts/1/jobs?per_page=100'
                self.responses[job_key] = {'total_count': len(jobs), 'jobs': jobs}
                self.job_keys.append(job_key)
                if role == 'gym':
                    self.gym_run = run
                    self.artifact_key = f'repos/{repo}/actions/runs/{run["id"]}/artifacts?per_page=100'
                    self.responses[self.artifact_key] = {'total_count': 1, 'artifacts': [{
                        'id': 999, 'name': 'canonical-v2-matched-release', 'expired': False,
                        'expires_at': '2026-10-22T12:00:00Z', 'created_at': '2026-10-15T11:59:00Z',
                        'size_in_bytes': 1234, 'digest': 'sha256:' + 'c' * 64,
                        'workflow_run': {'id': run['id'], 'repository_id': index,
                                         'head_repository_id': index, 'head_sha': sha, 'head_branch': 'main'}}]}
            self.policy['repositories'][role] = spec
        self.calls = []

    def get(self, endpoint):
        self.calls.append(endpoint)
        return copy.deepcopy(self.responses[endpoint])

    def collect(self):
        return collect(self, self.policy, now=1792065600)

    def test_success_is_source_evidence_only(self):
        result = self.collect()
        self.assertEqual({k: v['sha'] for k, v in result['sources'].items()}, self.shas)
        self.assertFalse(result['promotionEnabled'])
        self.assertIn('exact-owner-approval', result['remainingGates'])
        self.assertEqual(result['matchedArtifact']['id'], 999)
        self.assertTrue(all(c.startswith('repos/') for c in self.calls))

    def test_selects_matched_engine_not_independent_latest(self):
        repo = REPOSITORIES['engine']; head = 'f' * 40; sha = self.shas['engine']
        self.responses[f'repos/{repo}/commits/main']['sha'] = head
        self.responses[f'repos/{repo}/compare/{sha}...{head}'] = {
            'status': 'ahead', 'merge_base_commit': {'sha': sha}}
        result = self.collect()
        self.assertEqual(result['sources']['engine']['sha'], sha)
        self.assertEqual(result['observedMain']['engine'], head)

    def test_changed_or_missing_pin_workflow_rejected(self):
        key = f'repos/{REPOSITORIES["gym"]}/contents/{GYM_WORKFLOW}?ref={self.shas["gym"]}'
        for raw in (b'no pin', base64.b64decode(self.responses[key]['content']) + b'unreviewed command\n'):
            with self.subTest(raw=raw), patch.dict(self.responses, {key: {
                'type': 'file', 'path': GYM_WORKFLOW, 'encoding': 'base64',
                'content': base64.b64encode(raw).decode(), 'size': len(raw)}}):
                with self.assertRaises(ManualRuntimeError): self.collect()

    def test_repository_and_workflow_identity_mismatch(self):
        for key, field in ((f'repos/{REPOSITORIES["engine"]}', 'id'),
                           (f'repos/{REPOSITORIES["engine"]}/actions/workflows/11', 'id')):
            original = self.responses[key][field]; self.responses[key][field] = 888
            with self.assertRaises(ManualRuntimeError): self.collect()
            self.responses[key][field] = original

    def test_latest_failed_run_not_hidden_by_old_success(self):
        row = self.responses[self.run_keys[0]]
        newer = copy.deepcopy(row['workflow_runs'][0]); newer.update(id=999999, conclusion='failure')
        row['workflow_runs'].append(newer); row['total_count'] = 2
        with self.assertRaisesRegex(ManualRuntimeError, 'source_run_identity_or_result'): self.collect()

    def test_pr_wrong_sha_or_wrong_attempt_jobs_rejected(self):
        run = self.responses[self.run_keys[0]]['workflow_runs'][0]
        for field, value in [('event', 'pull_request'), ('head_sha', 'd' * 40), ('conclusion', 'skipped')]:
            original = run[field]; run[field] = value
            with self.assertRaises(ManualRuntimeError): self.collect()
            run[field] = original
        job = self.responses[self.job_keys[0]]['jobs'][0]; job['run_attempt'] = 2
        with self.assertRaisesRegex(ManualRuntimeError, 'source_job_identity_or_result'): self.collect()

    def test_missing_duplicate_skipped_or_truncated_jobs_rejected(self):
        key = self.job_keys[0]; saved = copy.deepcopy(self.responses[key])
        for change in ('missing', 'duplicate', 'skipped', 'truncated'):
            self.responses[key] = copy.deepcopy(saved); row = self.responses[key]
            if change == 'missing': row['jobs'].pop(); row['total_count'] -= 1
            if change == 'duplicate': row['jobs'].append(row['jobs'][0]); row['total_count'] += 1
            if change == 'skipped': row['jobs'][0]['conclusion'] = 'skipped'
            if change == 'truncated': row['total_count'] += 1
            with self.subTest(change=change), self.assertRaises(ManualRuntimeError): self.collect()

    def test_run_rerun_race_rejected(self):
        repo = REPOSITORIES['gym']; key = f'repos/{repo}/actions/runs/{self.gym_run["id"]}'
        self.responses[key] = {**self.gym_run, 'run_attempt': 2}
        with self.assertRaisesRegex(ManualRuntimeError, 'source_run_changed'): self.collect()

    def test_artifact_stale_expired_wrong_source_or_missing_digest_rejected(self):
        row = self.responses[self.artifact_key]['artifacts'][0]
        for field, value in [('expired', True), ('expires_at', '2026-10-01T12:00:00Z'),
                             ('created_at', '2026-10-14T12:00:00Z'), ('digest', None)]:
            original = row[field]; row[field] = value
            with self.subTest(field=field), self.assertRaises((ManualRuntimeError, TypeError)): self.collect()
            row[field] = original
        row['workflow_run']['head_sha'] = 'd' * 40
        with self.assertRaises(ManualRuntimeError): self.collect()

    def test_non_main_ancestor_and_incomplete_runs_rejected(self):
        sha = self.shas['engine']; key = f'repos/{REPOSITORIES["engine"]}/compare/{sha}...{sha}'
        self.responses[key]['status'] = 'diverged'
        with self.assertRaisesRegex(ManualRuntimeError, 'source_not_main_ancestor'): self.collect()
        self.responses[key]['status'] = 'identical'
        self.responses[self.run_keys[0]]['total_count'] = 1000
        with self.assertRaisesRegex(ManualRuntimeError, 'source_incomplete_response'): self.collect()

    def test_transport_only_reads_fixed_github_host(self):
        with patch('subprocess.run') as run:
            run.return_value.stdout = b'{}'
            self.assertEqual(GitHub().get('repos/Blue42hand/commander-gym'), {})
            args = run.call_args.args[0]
            self.assertEqual(args[:7], ['gh', 'api', '--hostname', 'github.com', '--method', 'GET',
                                       'repos/Blue42hand/commander-gym'])


if __name__ == '__main__':
    unittest.main()
