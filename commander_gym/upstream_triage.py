"""Read-only upstream change triage. No merge, push, model call or notification."""
from __future__ import annotations

import argparse
import json
import subprocess

from .manual_runtime_profile import COMMIT, ManualRuntimeError, require

UPSTREAM = 'wingedsheep/argentum-engine'
CATEGORIES = {
    'security-or-private-data': ('security', 'vulnerab', 'credential', 'mask', 'auth', 'secret'),
    'recording-or-recovery': ('record', 'persist', 'recover', 'rollback', 'corrupt', 'data loss'),
    'native-rules-or-protocol': ('rules-engine/', 'mtg-sdk/', 'protocol', 'legal', 'decision'),
    'dependencies-or-build': ('gradle', 'package-lock', '.github/workflows/', 'docker'),
    'performance': ('latency', 'performance', 'cache', 'deadlock', 'concurren', 'timeout'),
}


def summarize(base, head, comparison, *, owned_integration=None):
    for sha in (base, head):
        require(type(sha) is str and COMMIT.fullmatch(sha), 'triage_commit')
    require(type(comparison) is dict, 'triage_comparison')
    commits = comparison.get('commits')
    files = comparison.get('files')
    total = comparison.get('total_commits')
    require(type(commits) is list and type(files) is list and type(total) is int
            and total >= 0, 'triage_comparison')
    complete = len(commits) == total and len(files) < 300
    linear = comparison.get('status') in ('ahead', 'identical')
    findings = []
    paths = []
    for file in files:
        require(type(file) is dict and type(file.get('filename')) is str, 'triage_file')
        paths.append(file['filename'])
    for commit in commits:
        sha = commit.get('sha')
        require(type(sha) is str and COMMIT.fullmatch(sha), 'triage_commit')
        message = commit.get('commit', {}).get('message')
        require(type(message) is str, 'triage_message')
        title = message.splitlines()[0][:200] if message else ''
        categories = [name for name, words in CATEGORIES.items()
                      if any(word in message.lower() for word in words)]
        findings.append({'sha': sha, 'title': title, 'reviewCategories': categories,
                         'url': f'https://github.com/{UPSTREAM}/commit/{sha}'})
    path_categories = [name for name, words in CATEGORIES.items()
                       if any(word in path.lower() for word in words for path in paths)]
    state = ('held-incomplete-comparison' if not complete else
             'held-nonlinear-history' if not linear else
             'unchanged' if base == head else 'review-required')
    return {
        'schemaVersion': 1, 'repository': UPSTREAM, 'baseSha': base, 'headSha': head,
        'state': state, 'comparisonComplete': complete, 'totalCommits': total,
        'compareUrl': f'https://github.com/{UPSTREAM}/compare/{base}...{head}',
        'pathReviewCategories': path_categories, 'commits': findings,
        'importanceIsHeuristic': True,
        'cursorAdvanceAllowed': complete and linear,
        'weeklySync': {'state': 'held-existing-owner' if owned_integration else 'requires-tested-sync-pr',
                       'existingIntegration': owned_integration},
    }


def github(endpoint):
    # Fixed public repository; never execute content returned by GitHub.
    result = subprocess.run(['gh', 'api', f'repos/{UPSTREAM}/{endpoint}'],
                            check=True, capture_output=True, text=True, timeout=60)
    return json.loads(result.stdout)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--since', required=True, help='Last successfully reviewed upstream commit')
    parser.add_argument('--until', help='Exact upstream commit; defaults to a single main snapshot')
    parser.add_argument('--owned-integration', default='integration/upstream-2026-10-15',
                        help='Existing owner branch; retain until that owner completes the sync')
    args = parser.parse_args(argv)
    try:
        require(COMMIT.fullmatch(args.since), 'triage_commit')
        head = args.until or github('commits/main')['sha']
        require(type(head) is str and COMMIT.fullmatch(head), 'triage_commit')
        comparison = github(f'compare/{args.since}...{head}')
        # GitHub truncates unpaginated commits at250 and files at300. A partial
        # response produces an explicit hold; it never advances the review cursor.
        report = summarize(args.since, head, comparison, owned_integration=args.owned_integration)
    except (ManualRuntimeError, subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError):
        print(json.dumps({'schemaVersion': 1, 'state': 'held-read-failed',
                          'cursorAdvanceAllowed': False}))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0 if report['cursorAdvanceAllowed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
