"""Offline, private evidence-to-improvement ledger (schema 1).

Records attestations and provenance; it neither executes artifacts nor certifies
that an evaluator's assertions are true. No games, APIs, issue publication,
training export, policy edits or deployment are performed here.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time
import uuid

from .game_journal import _json, _private_dir, _safe, verify_finalized_manifest
from .storage import parse_artifact_id

CATEGORIES = {'model_judgment', 'observation_failure', 'action_or_schema_failure',
              'native_rules_candidate', 'primer_weakness_candidate',
              'recording_or_integrity_failure', 'infrastructure_failure', 'insufficient_evidence'}
STRATEGIC = {'model_judgment', 'primer_weakness_candidate'}
STATES = {'insufficient_evidence': {'hypothesis', 'rejected'},
          'hypothesis': {'reproduced', 'rejected'},
          'reproduced': {'implementation_validated', 'rejected'},
          'implementation_validated': {'measured', 'rejected'},
          'measured': {'adopted', 'rejected'}, 'adopted': {'rolled_back'},
          'rejected': set(), 'rolled_back': set()}


class ImprovementError(ValueError):
    pass


def require(value, message):
    if not value:
        raise ImprovementError(message)


def strings(value, label):
    require(isinstance(value, list) and value and
            all(isinstance(x, str) and x.strip() for x in value), label + ' requires nonempty strings')


def digest(value):
    return hashlib.sha256(_json(value)).hexdigest()


def artifact(value):
    try:
        parse_artifact_id(value)
    except ValueError as exc:
        raise ImprovementError('invalid artifact reference') from exc


def validate_hypothesis(d):
    for field in ('statement', 'experiment_bound', 'regression_plan', 'rollback_plan'):
        require(isinstance(d.get(field), str) and d[field].strip(), field + ' required')
    require(isinstance(d.get('baseline_commit'), str) and len(d['baseline_commit']) == 40
            and all(c in '0123456789abcdef' for c in d['baseline_commit']), 'exact baseline commit required')
    strings(d.get('scope'), 'scope')
    criteria = d.get('acceptance_criteria')
    require(isinstance(criteria, list) and criteria, 'falsifiable criteria required')
    for c in criteria:
        require(isinstance(c, dict) and isinstance(c.get('metric'), str) and
                c['metric'] not in {'winner', 'win_rate', 'placement'} and
                c.get('direction') in {'increase', 'decrease'} and
                type(c.get('minimum_delta')) in {float, int} and
                math.isfinite(c['minimum_delta']) and c['minimum_delta'] > 0,
                'criteria need a decision/reliability metric, direction and positive threshold')
    require(len({c['metric'] for c in criteria}) == len(criteria), 'duplicate criterion')
    sets = []
    for key in ('baseline_fixtures', 'held_out_fixtures'):
        fixtures = d.get(key)
        require(isinstance(fixtures, list) and fixtures, key + ' required')
        ids, groups = set(), set()
        for f in fixtures:
            artifact(f.get('artifact_id'))
            require(f.get('visibility') == 'seat_safe' and isinstance(f.get('group'), str)
                    and f['group'].strip(), 'fixtures require seat-safe inputs and leakage groups')
            ids.add(f['artifact_id'])
            groups.add(f['group'])
        sets.append((ids, groups))
    require(not sets[0][0] & sets[1][0] and not sets[0][1] & sets[1][1],
            'baseline and held-out fixtures must be disjoint by digest and derivation group')


class ImprovementLedger:
    """SQLite transactions give atomic ingestion, revision checks and leased work.

    History is append-only through this API. Keep the directory on private local
    storage, outside run manifests; backups are the operator's responsibility.
    Correlation keys are explicit human/adjudicator supplied fingerprints, not
    automatic semantic equivalence or statistical independence certification.
    """
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        _private_dir(self.directory, create=True)
        self.path = self.directory / 'improvements.sqlite3'
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        require(not self.path.stat().st_mode & 0o077, 'ledger must be private')
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS events (
              candidate TEXT NOT NULL, revision INTEGER NOT NULL, payload TEXT NOT NULL,
              PRIMARY KEY(candidate, revision));
            CREATE TABLE IF NOT EXISTS sources (
              candidate TEXT NOT NULL, source_key TEXT NOT NULL, payload TEXT NOT NULL,
              PRIMARY KEY(candidate, source_key));
            CREATE TABLE IF NOT EXISTS claims (
              candidate TEXT PRIMARY KEY, token TEXT NOT NULL, worker TEXT NOT NULL,
              expires REAL NOT NULL);
            ''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _get(self, db, cid):
        row = db.execute('SELECT payload FROM events WHERE candidate=? ORDER BY revision DESC LIMIT 1',
                         (cid,)).fetchone()
        require(row is not None, 'unknown candidate')
        result = json.loads(row[0])
        result['sources'] = [json.loads(r[0]) for r in db.execute(
            'SELECT payload FROM sources WHERE candidate=? ORDER BY source_key', (cid,))]
        result['independent_groups'] = len({s['correlation_group'] for s in result['sources']})
        return result

    def get(self, cid):
        with self.db() as db:
            return self._get(db, cid)

    def history(self, cid):
        with self.db() as db:
            return [json.loads(r[0]) for r in db.execute(
                'SELECT payload FROM events WHERE candidate=? ORDER BY revision', (cid,))]

    def ingest(self, source, proposal):
        """Trusted adapter API. Production callers use ingest_report for integrity checks."""
        _safe(source)
        _safe(proposal)
        for field in ('run_id', 'analysis_version', 'correlation_group'):
            require(isinstance(source.get(field), str) and source[field].strip(), field + ' required')
        for field in ('artifact_hash', 'report_sha256'):
            artifact('sha256:' + str(source.get(field)))
        strings(source.get('finding_ids'), 'source finding_ids')
        require(isinstance(source.get('evidence_refs'), list) and source['evidence_refs'], 'evidence references required')
        require(proposal.get('category') in CATEGORIES, 'unknown category')
        require(isinstance(proposal.get('family_key'), str) and proposal['family_key'].strip(), 'explicit dedupe family required')
        strings(proposal.get('finding_ids'), 'finding_ids')
        require(set(proposal['finding_ids']) <= set(source['finding_ids']), 'findings absent from report')
        require(proposal.get('confidence') in {'low', 'medium', 'high'}, 'invalid confidence')
        require(proposal.get('severity') in {'informational', 'low', 'medium', 'high', 'critical'}, 'invalid severity')
        for field in ('priority_reason', 'evidence_quality'):
            require(isinstance(proposal.get(field), str) and proposal[field].strip(), field + ' required')
        for field in ('alternative_explanations', 'counterevidence', 'missing_evidence'):
            require(isinstance(proposal.get(field), list), field + ' must explicitly be an array')
        cid = 'improvement-' + digest([1, proposal['category'], proposal['family_key']])
        key = digest([source[k] for k in ('run_id', 'artifact_hash', 'analysis_version', 'report_sha256')]
                     + [sorted(proposal['finding_ids'])])
        payload = {**source, 'finding_ids': sorted(proposal['finding_ids']), 'assessment': proposal}
        with self.db() as db:
            previous = db.execute('SELECT payload FROM sources WHERE candidate=? AND source_key=?', (cid, key)).fetchone()
            if previous:
                require(json.loads(previous[0]) == payload, 'same evidence key has changed assessment; use a new analysis version')
                return cid
            db.execute('INSERT INTO sources VALUES (?,?,?)', (cid, key, _json(payload).decode()))
            if not db.execute('SELECT 1 FROM events WHERE candidate=?', (cid,)).fetchone():
                self._append(db, dict(candidate_id=cid, schema_version=1, revision=1,
                    state='insufficient_evidence', category=proposal['category'], family_key=proposal['family_key'],
                    actor='ingestion', timestamp=time.time(), details={}, gates={}))
        return cid

    def ingest_report(self, run_directory: Path, version: str, proposal, *, correlation_group: str):
        """Read verified completed recorder reports without changing their ledger."""
        manifest = verify_finalized_manifest(run_directory)
        # Validate version without creating any run/analysis directories.
        import re
        require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', version), 'invalid version')
        base = run_directory / 'analysis' / version
        for path in (run_directory / 'analysis', base):
            _private_dir(path)
        review_path, report_path = base / 'review.json', base / 'report.json'
        require(not review_path.is_symlink() and not report_path.is_symlink(), 'symlinked report')
        receipt = json.loads(review_path.read_bytes())
        data = report_path.read_bytes()
        report = json.loads(data)
        report_hash = hashlib.sha256(data).hexdigest()
        require(receipt.get('status') == 'completed' and receipt.get('report_sha256') == report_hash,
                'analysis is incomplete or report changed')
        for field, value in dict(run_id=manifest['run_id'], artifact_hash=manifest['artifact_hash'], analysis_version=version).items():
            require(receipt.get(field) == value and report.get(field) == value, 'report identity mismatch')
        findings = report.get('findings', [])
        require(isinstance(findings, list), 'invalid findings')
        indexed = {f.get('finding_id'): f for f in findings if isinstance(f, dict)}
        require(None not in indexed and len(indexed) == len(findings), 'findings require unique finding_id')
        refs = []
        for fid in proposal['finding_ids']:
            require(fid in indexed, 'finding missing from completed report')
            require(indexed[fid].get('category') == proposal['category'], 'finding category mismatch')
            evidence = indexed[fid].get('evidence_refs')
            require(isinstance(evidence, list) and evidence, 'finding lacks precise evidence_refs')
            refs.append(dict(finding_id=fid, report_pointer='/findings/' + str(findings.index(indexed[fid])),
                             evidence_refs=evidence))
        verify_finalized_manifest(run_directory)
        require(report_path.read_bytes() == data and json.loads(review_path.read_bytes()) == receipt,
                'report changed during ingestion')
        return self.ingest(dict(run_id=manifest['run_id'], artifact_hash=manifest['artifact_hash'],
            analysis_version=version, report_sha256=report_hash, correlation_group=correlation_group,
            finding_ids=list(indexed), evidence_refs=refs), proposal)

    def _append(self, db, record):
        db.execute('INSERT INTO events VALUES (?,?,?)',
                   (record['candidate_id'], record['revision'], _json(record).decode()))

    def transition(self, cid, revision, target, details, *, actor, claim_token=None):
        _safe(details)
        require(isinstance(actor, str) and actor.strip(), 'actor required')
        with self.db() as db:
            c = self._get(db, cid)
            claim = db.execute('SELECT token, worker, expires FROM claims WHERE candidate=?', (cid,)).fetchone()
            if claim:
                require(claim[0] == claim_token and claim[1] == actor and claim[2] > time.time(),
                        'transition requires current owned live lease')
            require(c['revision'] == revision, 'stale candidate revision')
            require(target in STATES[c['state']], 'invalid state transition')
            gates = c['gates']
            if target == 'hypothesis':
                validate_hypothesis(details)
            elif target == 'reproduced':
                artifact(details.get('repro_artifact'))
                require(details.get('offline') is True and details.get('baseline_failed') is True
                        and isinstance(details.get('command'), str) and details['command'].strip(),
                        'deterministic offline failing baseline repro required')
                require(details.get('tests_before_fix') is True or details.get('test_exception_reason'),
                        'tests before fixes or explicit infeasibility reason required')
            elif target == 'implementation_validated':
                for f in ('pr', 'author', 'reviewer'):
                    require(isinstance(details.get(f), str) and details[f].strip(), f + ' required')
                require(details['author'] != details['reviewer'] and details.get('review') == 'approved'
                        and details.get('ci') == 'passed', 'independent approved review and CI required')
                require(isinstance(details.get('commit'), str) and len(details['commit']) == 40
                        and all(ch in '0123456789abcdef' for ch in details['commit']), 'exact commit required')
                artifact(details.get('ci_artifact'))
                strings(details.get('changed_scope'), 'changed_scope')
                require(set(details['changed_scope']) <= set(gates['hypothesis']['scope']), 'change exceeds scope')
            elif target == 'measured':
                require(details.get('commit') == gates['implementation_validated']['commit'], 'measurement commit mismatch')
                require(details.get('baseline_commit') == gates['hypothesis']['baseline_commit'], 'baseline commit mismatch')
                artifact(details.get('verification_artifact'))
                require(details.get('held_out_passed') is True and details.get('regressions') == [], 'regression qualification failed')
                for key in ('baseline_fixtures', 'held_out_fixtures'):
                    require(details.get(key) == gates['hypothesis'][key], 'verification fixture mismatch')
                metrics = details.get('metrics')
                require(isinstance(metrics, list) and metrics, 'measured metrics required')
                indexed = {m.get('metric'): m for m in metrics}
                require(len(indexed) == len(metrics), 'duplicate measured metric')
                for criterion in gates['hypothesis']['acceptance_criteria']:
                    m = indexed.get(criterion['metric'], {})
                    for field in ('baseline', 'after', 'denominator'):
                        require(type(m.get(field)) in {int, float} and math.isfinite(m[field]), 'finite measured values required')
                    require(m['denominator'] > 0, 'measurement denominator required')
                    delta = m['after'] - m['baseline']
                    if criterion['direction'] == 'decrease':
                        delta = -delta
                    require(delta >= criterion['minimum_delta'], 'acceptance threshold not met')
                if c['category'] in STRATEGIC:
                    artifact(details.get('shadow_artifact'))
            elif target == 'adopted':
                require(details.get('approval_ref') and details.get('approved_by'), 'explicit adoption approval required')
                if c['category'] in STRATEGIC:
                    require(c['independent_groups'] >= 2, 'strategic adoption needs multiple independent evidence groups')
                    require(all(s['assessment']['confidence'] != 'low' for s in c['sources']), 'strategic evidence confidence insufficient')
            else:
                require(details.get('reason') and details.get('evidence_ref'), 'rejection/rollback reason and evidence required')
            gates = {**gates, target: details}
            record = {k: v for k, v in c.items() if k not in {'sources', 'independent_groups'}}
            record.update(revision=revision + 1, state=target, details=details, gates=gates,
                          actor=actor, timestamp=time.time())
            self._append(db, record)
            return record

    def claim(self, cid, worker, *, now=None, lease_seconds=3600):
        require(isinstance(worker, str) and worker.strip(), 'worker required')
        require(type(lease_seconds) is int and 1 <= lease_seconds <= 86400, 'invalid lease')
        now = time.time() if now is None else now
        with self.db() as db:
            c = self._get(db, cid)
            if not STATES[c['state']]:
                return {'status': 'terminal', 'state': c['state']}
            previous = db.execute('SELECT expires FROM claims WHERE candidate=?', (cid,)).fetchone()
            if previous and previous[0] > now:
                return {'status': 'busy'}
            token = str(uuid.uuid4())
            db.execute('INSERT OR REPLACE INTO claims VALUES (?,?,?,?)', (cid, token, worker, now + lease_seconds))
            return dict(status='claimed', token=token, expires=now + lease_seconds, resuming=previous is not None,
                        revision=c['revision'])

    def release(self, cid, token, worker, *, now=None):
        now = time.time() if now is None else now
        with self.db() as db:
            require(db.execute('DELETE FROM claims WHERE candidate=? AND token=? AND worker=? AND expires>?',
                               (cid, token, worker, now)).rowcount == 1, 'owned live lease required')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger', type=Path, required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    ingest = sub.add_parser('ingest')
    ingest.add_argument('--run-directory', type=Path, required=True)
    ingest.add_argument('--version', required=True)
    ingest.add_argument('--correlation-group', required=True)
    ingest.add_argument('--proposal', type=Path, required=True)
    for command in ('show', 'history', 'claim', 'transition', 'release'):
        p = sub.add_parser(command)
        p.add_argument('candidate')
        if command in {'claim', 'release', 'transition'}:
            p.add_argument('--actor', required=True)
        if command == 'transition':
            p.add_argument('--revision', required=True, type=int)
            p.add_argument('--target', required=True)
            p.add_argument('--details', required=True, type=Path)
            p.add_argument('--claim-token')
        if command == 'release':
            p.add_argument('--token', required=True)
    args = parser.parse_args()
    ledger = ImprovementLedger(args.ledger)
    if args.command == 'ingest':
        result = ledger.ingest_report(args.run_directory, args.version, json.loads(args.proposal.read_bytes()),
                                      correlation_group=args.correlation_group)
    elif args.command == 'show':
        result = ledger.get(args.candidate)
    elif args.command == 'history':
        result = ledger.history(args.candidate)
    elif args.command == 'claim':
        result = ledger.claim(args.candidate, args.actor)
    elif args.command == 'release':
        ledger.release(args.candidate, args.token, args.actor)
        result = {'status': 'released'}
    else:
        result = ledger.transition(args.candidate, args.revision, args.target,
                                    json.loads(args.details.read_bytes()), actor=args.actor, claim_token=args.claim_token)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
