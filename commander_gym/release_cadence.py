"""Read-only nightly preflight over the existing private release bundle.

This module cannot approve, stage or promote a runtime. Evidence is supplied by
the existing qualification owner and pinned separately, like release.json itself.
No schedule is installed and no credentials or provider APIs are accessed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time

from .manual_runtime_profile import (
    COMMIT, ManualRuntimeError, decode, digest, require, validate_approval,
    validate_profile,
)
from .runtime_release import inspect_bundle

REPOSITORIES = {
    'engine': 'Blue42hand/argentum-engine',
    'gym': 'Blue42hand/commander-gym',
    'private': 'Blue42hand/commander-gym-private',
}
REQUIRED_CHECKS = {
    'engine': {('.github/workflows/ci.yml', name) for name in (
        'backend', 'coverage', 'frontend', 'test (engine)', 'test (scenarios-old)',
        'test (scenarios-2023-24)', 'test (scenarios-2025-26)', 'test (server)',
        'test (content)', 'test (tools)')} | {('.github/workflows/web-client-tests.yml', 'test')},
    'gym': {('.github/workflows/python-tests.yml', name) for name in (
        'python', 'jvm-adapter', 'manual-runtime-sdk', 'release-install')},
    'private': {('.github/workflows/deck-collection.yml', 'integrity')},
}


def feature_policy(profile):
    """Freeze operational settings and installed pilot identity, not source heads."""
    validate_profile(profile)
    keys = ('profile', 'purpose', 'recordingSchemaVersion', 'settings', 'ingress',
            'identities', 'paths', 'credentialPolicy', 'recordingAuthority')
    return {
        **{key: profile[key] for key in keys},
        'liveCatalog': profile.get('liveCatalog'),
        # validate_profile checks generated config against these exact settings.
        # Its bytes contain content-addressed artifact roots, which necessarily
        # change on an ordinary source update without widening policy.
        'pilotFingerprint': profile['catalog']['pilotFingerprint'],
        'componentDigest': profile['catalog']['componentDigest'],
        'bindingNames': sorted(profile['catalog']['bindings']),
    }


def pinned_json(path, expected_digest):
    """Digest must come from review, never from an untrusted adjacent checksum."""
    path = Path(path)
    require(not path.is_symlink() and path.is_file(), 'cadence_input_file')
    require(path.stat().st_size <= 32 * 1024 * 1024, 'cadence_input_size')
    raw = path.read_bytes()
    require(len(raw) <= 32 * 1024 * 1024, 'cadence_input_size')
    require(hashlib.sha256(raw).hexdigest() == expected_digest, 'cadence_input_digest')
    return decode(raw)


def validate_sources(profile, evidence, release_sha):
    """Require final-main CI, exact tuple review, and a sealed private catalog proof.

    These are qualification attestations, not self-authenticating GitHub payloads.
    The CLI requires their externally reviewed digest. Future automated collection
    must verify origin, run attempts and check identities before issuing them.
    """
    require(type(evidence) is dict and set(evidence) == {
        'schemaVersion', 'releaseSha256', 'sources', 'catalogValidation'}, 'cadence_evidence_fields')
    require(type(evidence['schemaVersion']) is int and evidence['schemaVersion'] == 1
            and evidence['releaseSha256'] == release_sha, 'cadence_evidence_identity')
    sources = evidence['sources']
    require(type(sources) is dict and set(sources) == set(REPOSITORIES), 'cadence_sources')
    for role, repo in REPOSITORIES.items():
        source = sources[role]
        require(type(source) is dict and set(source) == {
            'repository', 'sha', 'review', 'checks'}, 'cadence_source_fields')
        sha = source['sha']
        require(source['repository'] == repo and type(sha) is str
                and COMMIT.fullmatch(sha), 'cadence_source_identity')
        if role != 'private':
            require(sha == profile[role + 'Sha'], 'cadence_source_identity')
        review = source['review']
        require(type(review) is dict and set(review) == {'sha', 'verdict', 'evidenceSha256'}
                and review['sha'] == sha and review['verdict'] == 'approve'
                and type(review['evidenceSha256']) is str
                and len(review['evidenceSha256']) == 64
                and all(c in '0123456789abcdef' for c in review['evidenceSha256']), 'cadence_review')
        checks = source['checks']
        require(type(checks) is list and checks, 'cadence_checks')
        seen = set()
        for check in checks:
            require(type(check) is dict and set(check) == {
                'workflow', 'name', 'event', 'branch', 'headSha', 'testedSha',
                'runId', 'runAttempt', 'conclusion'}, 'cadence_check_fields')
            require(type(check['workflow']) is str and type(check['name']) is str,
                    'cadence_check_identity')
            identity = check['workflow'], check['name']
            require(identity not in seen, 'cadence_duplicate_check')
            seen.add(identity)
            require(check['event'] == 'push' and check['branch'] == 'main'
                    and check['headSha'] == sha and check['testedSha'] == sha
                    and check['conclusion'] == 'success'
                    and type(check['runId']) is int and check['runId'] > 0
                    and type(check['runAttempt']) is int and check['runAttempt'] > 0,
                    'cadence_check_failed')
        require(REQUIRED_CHECKS[role] <= seen, 'cadence_missing_check')
    catalog = evidence['catalogValidation']
    require(type(catalog) is dict and set(catalog) == {
        'engineSha', 'gymSha', 'privateSha', 'catalogFilesSha256',
        'collectionIntegrity', 'nativeDeckValidation'}, 'cadence_catalog_fields')
    require(catalog['engineSha'] == profile['engineSha']
            and catalog['gymSha'] == profile['gymSha']
            and catalog['privateSha'] == sources['private']['sha']
            and catalog['catalogFilesSha256'] == digest(profile['artifacts']['catalog']['files'])
            and catalog['collectionIntegrity'] is True
            and catalog['nativeDeckValidation'] is True, 'cadence_catalog_identity')


def preflight(bundle, release_sha, baseline, evidence, *, current_runtime, sequence, now=None):
    """Return bounded metadata only; even a passing candidate remains disabled."""
    now = time.time() if now is None else now
    manifest = inspect_bundle(Path(bundle), release_sha, now=now)
    profile = manifest['profile']
    require(feature_policy(profile) == feature_policy(baseline), 'cadence_feature_change')
    validate_approval(profile, manifest['qualification'], manifest['approval'],
                      previous_id=current_runtime, sequence=sequence)
    validate_sources(profile, evidence, release_sha)
    return {'schemaVersion': 1, 'state': 'held-disabled', 'preflightPassed': True,
            'promotionEnabled': False, 'releaseSha256': release_sha,
            'runtimeId': digest(profile), 'previousRuntimeId': current_runtime,
            'sequence': sequence, 'sources': {role: row['sha'] for role, row in evidence['sources'].items()},
            'featurePolicySha256': digest(feature_policy(baseline))}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('bundle', 'sha256', 'baseline', 'baseline-sha256', 'evidence',
                 'evidence-sha256', 'current-runtime'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--sequence', type=int, required=True)
    args = parser.parse_args(argv)
    try:
        baseline = pinned_json(args.baseline, args.baseline_sha256)
        evidence = pinned_json(args.evidence, args.evidence_sha256)
        result = preflight(args.bundle, args.sha256, baseline, evidence,
                           current_runtime=args.current_runtime, sequence=args.sequence)
    except ManualRuntimeError as error:
        result = {'schemaVersion': 1, 'state': 'held-invalid', 'reason': str(error),
                  'promotionEnabled': False, 'preflightPassed': False}
    except (OSError, KeyError, TypeError, ValueError):
        # Malformed private inputs must not dump paths or payloads into alerts.
        result = {'schemaVersion': 1, 'state': 'held-invalid', 'reason': 'cadence_input_invalid',
                  'promotionEnabled': False, 'preflightPassed': False}
    print(json.dumps(result, sort_keys=True))
    return 0 if result['preflightPassed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
