"""Read-only GitHub evidence for one matched source candidate, never approval.

Run only trusted installed collector code in the operator's existing gh context.
Candidate workflows and repository files are inspected as bytes, never executed.
The policy digest comes from independent review, not an adjacent checksum file.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone

from .manual_runtime_profile import COMMIT, ManualRuntimeError, decode, require
from .release_cadence import REPOSITORIES, REQUIRED_CHECKS, pinned_json

PIN = re.compile(rb'(          repository: Blue42hand/argentum-engine\n'
                 rb'          # Pinned native candidate for the human-versus-Luna milestone\.\n'
                 rb'          ref: )([0-9a-f]{40})(\n)')
GYM_WORKFLOW = '.github/workflows/python-tests.yml'
ARTIFACT = 'canonical-v2-matched-release'


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def full_sha(value):
    require(type(value) is str and COMMIT.fullmatch(value), 'source_sha')
    return value


def positive(value):
    return type(value) is int and value > 0


class GitHub:
    def get(self, endpoint):
        # Endpoints are constructed only from fixed repositories and validated IDs.
        require(endpoint.startswith('repos/') and not any(c in endpoint for c in '\r\n'),
                'source_endpoint')
        try:
            result = subprocess.run(['gh', 'api', '--hostname', 'github.com', '--method',
                                     'GET', endpoint], stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    timeout=60, check=True)
        except (OSError, subprocess.SubprocessError):
            raise ManualRuntimeError('source_github_unavailable') from None
        require(len(result.stdout) <= 16 * 1024 * 1024, 'source_response_size')
        return decode(result.stdout)


def bounded_rows(response, key):
    rows = response[key]
    require(type(rows) is list and type(response['total_count']) is int
            and len(rows) == response['total_count'] and len(rows) <= 100,
            'source_incomplete_response')
    return rows


def workflow_bytes(api, repo, path, sha):
    result = api.get(f'repos/{repo}/contents/{path}?ref={sha}')
    require(result['type'] == 'file' and result['path'] == path
            and result['encoding'] == 'base64' and result['size'] <= 1024 * 1024,
            'source_workflow_file')
    raw = base64.b64decode(''.join(result['content'].split()), validate=True)
    require(len(raw) == result['size'], 'source_workflow_size')
    return raw


def validate_policy(policy):
    require(type(policy) is dict and set(policy) == {'schemaVersion', 'repositories'}
            and type(policy['schemaVersion']) is int and policy['schemaVersion'] == 1,
            'source_policy')
    require(set(policy['repositories']) == set(REPOSITORIES), 'source_policy')
    for role, repo in REPOSITORIES.items():
        row = policy['repositories'][role]
        require(set(row) == {'repository', 'id', 'workflows'} and row['repository'] == repo
                and positive(row['id']), 'source_policy_repository')
        paths = {path for path, _ in REQUIRED_CHECKS[role]}
        require(set(row['workflows']) == paths, 'source_policy_workflows')
        for path, workflow in row['workflows'].items():
            require(set(workflow) == {'id', 'sha256'} and positive(workflow['id'])
                    and re.fullmatch('[0-9a-f]{64}', workflow['sha256']), 'source_policy_workflow')


def collect_workflow(api, repo, repository_id, path, spec, sha, raw):
    normalized = PIN.sub(rb'\1<ENGINE_SHA>\3', raw) if path == GYM_WORKFLOW else raw
    require(sha256(normalized) == spec['sha256'], 'source_unreviewed_workflow')
    workflow = api.get(f'repos/{repo}/actions/workflows/{spec["id"]}')
    require(workflow['id'] == spec['id'] and workflow['path'] == path
            and workflow['state'] == 'active', 'source_workflow_identity')
    runs = bounded_rows(api.get(f'repos/{repo}/actions/workflows/{spec["id"]}/runs'
                               f'?head_sha={sha}&event=push&branch=main&per_page=100'), 'workflow_runs')
    require(runs and all(positive(r['id']) for r in runs), 'source_no_run')
    run = max(runs, key=lambda r: r['id'])  # Never hide a newer failed or pending run.
    run_id, attempt = run['id'], run['run_attempt']
    require(positive(attempt) and run['workflow_id'] == spec['id'] and run['path'] == path
            and run['repository']['id'] == repository_id
            and run['repository']['full_name'] == repo
            and run['head_repository']['id'] == repository_id
            and run['head_sha'] == sha and run['head_branch'] == 'main'
            and run['event'] == 'push' and run['status'] == 'completed'
            and run['conclusion'] == 'success', 'source_run_identity_or_result')
    jobs = bounded_rows(api.get(f'repos/{repo}/actions/runs/{run_id}/attempts/{attempt}'
                               '/jobs?per_page=100'), 'jobs')
    checks, names = [], set()
    for job in jobs:
        require(type(job['name']) is str and job['name'] not in names
                and job['run_id'] == run_id and job['run_attempt'] == attempt
                and job['head_sha'] == sha and job['head_branch'] == 'main'
                and job['status'] == 'completed' and job['conclusion'] == 'success',
                'source_job_identity_or_result')
        names.add(job['name'])
        checks.append({'workflow': path, 'name': job['name'], 'event': 'push', 'branch': 'main',
                       'headSha': sha, 'testedSha': sha, 'runId': run_id,
                       'runAttempt': attempt, 'conclusion': 'success'})
    fresh = api.get(f'repos/{repo}/actions/runs/{run_id}')
    require(all(fresh.get(k) == run.get(k) for k in (
        'id', 'run_attempt', 'head_sha', 'head_branch', 'event', 'workflow_id',
        'path', 'status', 'conclusion', 'updated_at')), 'source_run_changed')
    return checks, run


def collect(api, policy, *, now=None, gym_sha=None):
    validate_policy(policy)
    if gym_sha is not None:
        gym_sha = full_sha(gym_sha)
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    pins, heads, raw_workflows = {}, {}, {}
    for role, repo in REPOSITORIES.items():
        identity = api.get(f'repos/{repo}')
        require(identity['id'] == policy['repositories'][role]['id']
                and identity['full_name'] == repo and identity['default_branch'] == 'main',
                'source_repository_identity')
        heads[role] = full_sha(api.get(f'repos/{repo}/commits/main')['sha'])
    pins.update(heads)
    if gym_sha is not None:
        pins['gym'] = gym_sha
    gym_raw = workflow_bytes(api, REPOSITORIES['gym'], GYM_WORKFLOW, pins['gym'])
    matches = list(PIN.finditer(gym_raw))
    require(len(matches) == 1, 'source_engine_pin')
    pins['engine'] = full_sha(matches[0][2].decode())
    raw_workflows[('gym', GYM_WORKFLOW)] = gym_raw
    sources, gym_run = {}, None
    for role, repo in REPOSITORIES.items():
        sha, spec = pins[role], policy['repositories'][role]
        # A frozen Gym or matched engine pin may precede main, but cannot be off-main.
        ancestry = api.get(f'repos/{repo}/compare/{sha}...{heads[role]}')
        require(ancestry['status'] in ('ahead', 'identical')
                and ancestry['merge_base_commit']['sha'] == sha, 'source_not_main_ancestor')
        checks = []
        for path, workflow in spec['workflows'].items():
            raw = raw_workflows.get((role, path))
            if raw is None:
                raw = workflow_bytes(api, repo, path, sha)
            rows, run = collect_workflow(api, repo, spec['id'], path, workflow, sha, raw)
            checks.extend(rows)
            if role == 'gym':
                gym_run = run
        require(REQUIRED_CHECKS[role] <= {(r['workflow'], r['name']) for r in checks},
                'source_required_jobs')
        sources[role] = {'repository': repo, 'sha': sha, 'checks': checks}
    artifacts = bounded_rows(api.get(f'repos/{REPOSITORIES["gym"]}/actions/runs/'
                                    f'{gym_run["id"]}/artifacts?per_page=100'), 'artifacts')
    matches = [a for a in artifacts if a['name'] == ARTIFACT]
    require(len(matches) == 1, 'source_matched_artifact')
    artifact = matches[0]
    origin = artifact['workflow_run']
    require(positive(artifact['id']) and artifact['expired'] is False
            and timestamp(artifact['expires_at']) > now
            and 0 < artifact['size_in_bytes'] <= 1024**3
            and re.fullmatch('sha256:[0-9a-f]{64}', artifact['digest'])
            and origin['id'] == gym_run['id']
            and origin['repository_id'] == policy['repositories']['gym']['id']
            and origin['head_repository_id'] == origin['repository_id']
            and origin['head_sha'] == pins['gym'] and origin['head_branch'] == 'main'
            # Artifacts lack run_attempt. Refuse previous-attempt artifacts.
            and timestamp(artifact['created_at']) >= timestamp(gym_run['run_started_at']),
            'source_artifact_identity_or_expiry')
    final = api.get(f'repos/{REPOSITORIES["gym"]}/actions/runs/{gym_run["id"]}')
    require(all(final.get(k) == gym_run.get(k) for k in (
        'run_attempt', 'status', 'conclusion', 'updated_at')), 'source_run_changed')
    return {'schemaVersion': 1, 'state': 'source-evidence-collected', 'promotionEnabled': False,
            'observedUnix': now, 'observedMain': heads, 'sources': sources,
            'matchedArtifact': {'repository': REPOSITORIES['gym'], 'id': artifact['id'],
                                'runId': gym_run['id'], 'runAttempt': gym_run['run_attempt'],
                                'sha256': artifact['digest'].split(':')[1],
                                'bytes': artifact['size_in_bytes'], 'expiresAt': artifact['expires_at']},
            'remainingGates': ['artifact-byte-and-manifest-verification', 'independent-source-review',
                               'native-private-catalog-validation', 'fresh-runtime-qualification',
                               'exact-owner-approval']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--policy', required=True)
    parser.add_argument('--policy-sha256', required=True)
    parser.add_argument('--gym-sha', help='Frozen full Gym commit; must be a main ancestor with passing exact-commit CI')
    args = parser.parse_args(argv)
    try:
        policy = pinned_json(args.policy, args.policy_sha256)
        result = collect(GitHub(), policy, gym_sha=args.gym_sha)
        result['policySha256'] = args.policy_sha256
    except ManualRuntimeError as error:
        result = {'state': 'held-source-evidence', 'reason': str(error), 'promotionEnabled': False}
    except (KeyError, TypeError, ValueError, OSError):
        result = {'state': 'held-source-evidence', 'reason': 'source_input_invalid', 'promotionEnabled': False}
    print(json.dumps(result, sort_keys=True))
    return 0 if result['state'] == 'source-evidence-collected' else 1


if __name__ == '__main__':
    raise SystemExit(main())
