"""Synthetic improvement lifecycle tests; no private traces or model calls."""
import tempfile
import unittest
from pathlib import Path

from commander_gym.improvement import ImprovementLedger, ImprovementError

HASH = 'a' * 64


def proposal(category='model_judgment'):
    return dict(category=category, family_key='synthetic-resource-sequencing',
                finding_ids=['f1'], confidence='medium', severity='high',
                priority_reason='Repeated resource sequencing risk',
                evidence_quality='seat observation and native legal alternatives present',
                alternative_explanations=['Different long-term plan'],
                counterevidence=['A delayed line may preserve interaction'],
                missing_evidence=['Independent adjudication'])


def source(run='run1', group='game1'):
    return dict(run_id=run, artifact_hash=HASH, analysis_version='1.0.0',
                report_sha256=HASH, correlation_group=group, discovery_artifact_ids=['sha256:' + HASH],
                finding_ids=['f1'], evidence_refs=[{'path': 'synthetic.json', 'pointer': '/findings/0'}],
                finding_qualification={'f1': {'confidence': 'medium', 'strategic_evidence_complete': True}})


def hypothesis():
    return dict(baseline_commit='d'*40, statement='If explicit resource accounting is added, sequencing errors decrease',
                acceptance_criteria=[dict(metric='sequencing_errors', direction='decrease',
                                          minimum_delta=1)],
                scope=['resource-accounting'], experiment_bound='offline fixtures only',
                baseline_fixtures=[dict(artifact_id='sha256:' + HASH, group='base', visibility='seat_safe')],
                held_out_fixtures=[dict(artifact_id='sha256:' + 'b'*64, group='held', visibility='seat_safe')],
                regression_plan='Run frozen held-out cases', rollback_plan='Revert linked commit')


class ImprovementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = ImprovementLedger(Path(self.tmp.name) / 'private')
        self.cid = self.ledger.ingest(source(), proposal())

    def move(self, target, details):
        candidate = self.ledger.get(self.cid)
        return self.ledger.transition(self.cid, candidate['revision'], target, details, actor='reviewer')

    def prepare(self):
        self.move('hypothesis', hypothesis())
        self.move('reproduced', dict(repro_artifact='sha256:' + HASH,
                                   command='python -m unittest tests.test_improvement',
                                   offline=True, baseline_failed=True, tests_before_fix=True))
        self.move('implementation_validated', dict(pr='https://example.test/pr/1', commit='c'*40,
                  author='author', reviewer='reviewer', review='approved', ci='passed',
                  ci_artifact='sha256:' + HASH, changed_scope=['resource-accounting']))

    def test_repeated_ingestion_and_correlated_reports_do_not_inflate_evidence(self):
        self.assertEqual(self.cid, self.ledger.ingest(source(), proposal()))
        self.ledger.ingest(source('run2'), proposal())
        self.assertEqual(1, self.ledger.get(self.cid)['independent_groups'])
        self.assertEqual(2, len(self.ledger.get(self.cid)['sources']))

    def test_stale_revision_and_skip_rejected(self):
        with self.assertRaises(ImprovementError):
            self.ledger.transition(self.cid, 0, 'hypothesis', hypothesis(), actor='a')
        with self.assertRaises(ImprovementError):
            self.move('adopted', {})

    def test_fixture_leakage_and_privileged_input_rejected(self):
        for field, value in [('group', 'base'), ('visibility', 'omniscient'),
                             ('artifact_id', 'sha256:' + HASH)]:
            data = hypothesis()
            data['held_out_fixtures'][0][field] = value
            with self.assertRaises(ImprovementError):
                self.move('hypothesis', data)

    def test_independent_review_and_scope(self):
        self.move('hypothesis', hypothesis())
        self.move('reproduced', dict(repro_artifact='sha256:' + HASH, command='offline',
                                   offline=True, baseline_failed=True, tests_before_fix=True))
        for reviewer, scope in [('author', ['resource-accounting']), ('reviewer', ['redesign'])]:
            with self.assertRaises(ImprovementError):
                self.move('implementation_validated', dict(pr='x', commit='c'*40, author='author',
                          reviewer=reviewer, review='approved', ci='passed',
                          ci_artifact='sha256:' + HASH, changed_scope=scope))

    def test_measured_verification_and_strategic_approval(self):
        self.prepare()
        measure = dict(baseline_commit='d'*40, baseline_fixtures=hypothesis()['baseline_fixtures'],
                       held_out_fixtures=hypothesis()['held_out_fixtures'], commit='c'*40, verification_artifact='sha256:' + HASH,
                       metrics=[dict(metric='sequencing_errors', baseline=3, after=1, denominator=10)],
                       held_out_passed=True, regressions=[], shadow_artifact='sha256:' + HASH)
        self.move('measured', measure)
        with self.assertRaises(ImprovementError):
            self.move('adopted', dict(approval_ref='user-approved', approved_by='noah'))
        self.ledger.ingest(source('run2', 'game2'), proposal())
        self.move('adopted', dict(approval_ref='user-approved', approved_by='noah'))
        self.assertEqual('adopted', self.ledger.get(self.cid)['state'])
        self.assertEqual('terminal', self.ledger.claim(self.cid, 'hourly-worker')['status'])

    def test_win_rate_alone_and_failed_measurement_rejected(self):
        self.prepare()
        for metric, after in [('win_rate', 5), ('sequencing_errors', 4)]:
            with self.assertRaises(ImprovementError):
                self.move('measured', dict(baseline_commit='d'*40, baseline_fixtures=hypothesis()['baseline_fixtures'],
                    held_out_fixtures=hypothesis()['held_out_fixtures'], commit='c'*40, verification_artifact='sha256:' + HASH,
                    metrics=[dict(metric=metric, baseline=3, after=after, denominator=10)],
                    held_out_passed=True, regressions=[], shadow_artifact='sha256:' + HASH))

    def test_claim_resume_and_obsolete_worker(self):
        claim = self.ledger.claim(self.cid, 'worker', now=10, lease_seconds=10)
        self.assertEqual('busy', self.ledger.claim(self.cid, 'other', now=11)['status'])
        resumed = self.ledger.claim(self.cid, 'other', now=21)
        self.assertNotEqual(claim['token'], resumed['token'])
        with self.assertRaises(ImprovementError):
            self.ledger.release(self.cid, claim['token'], 'worker', now=22)
        self.ledger.release(self.cid, resumed['token'], 'other', now=22)

    def test_history_survives_reopen(self):
        self.move('hypothesis', hypothesis())
        other = ImprovementLedger(Path(self.tmp.name) / 'private')
        self.assertEqual(2, other.get(self.cid)['revision'])
        self.assertEqual(2, len(other.history(self.cid)))
        basis = other.history(self.cid)[-1]['evidence_basis']
        self.ledger.ingest(source('run2', 'game2'), proposal())
        self.assertEqual(basis, other.history(self.cid)[-1]['evidence_basis'])
        self.assertEqual(1, other.history(self.cid)[-1]['independent_groups_at_transition'])

    def test_report_ingestion_verifies_hashes_and_leaves_source_unchanged(self):
        from commander_gym.game_journal import PIN_KEYS, PrivateGameJournal
        from commander_gym.game_analysis import claim_analysis, finish_analysis
        game = Path(self.tmp.name) / 'game'
        with PrivateGameJournal(game, 'synthetic-run', {k: None for k in PIN_KEYS}, game_id='synthetic') as j:
            j.finish({'kind': 'operator_stop'}, expected_sources={}, gaps=['partial'])
        claim = claim_analysis(game, '1.0.0')
        from commander_gym.game_journal import verify_finalized_manifest
        from commander_gym.analysis_report import recorder_profile_from_skill
        from tests._analysis_report_fixture import skill_report
        manifest = verify_finalized_manifest(game)
        report = recorder_profile_from_skill(skill_report(manifest), manifest)
        finish_analysis(game, '1.0.0', claim['claim_id'], report)
        before = {str(p.relative_to(game)): p.read_bytes() for p in game.rglob('*') if p.is_file()}
        cid = self.ledger.ingest_report(game, '1.0.0', proposal('infrastructure_failure'), correlation_group='synthetic')
        self.assertEqual(cid, self.ledger.ingest_report(game, '1.0.0', proposal('infrastructure_failure'), correlation_group='synthetic'))
        self.assertEqual(before, {str(p.relative_to(game)): p.read_bytes() for p in game.rglob('*') if p.is_file()})
        with (game / 'analysis/1.0.0/report.json').open('ab') as f:
            f.write(b' ')
        with self.assertRaises(ImprovementError):
            self.ledger.ingest_report(game, '1.0.0', proposal('infrastructure_failure'), correlation_group='synthetic')

    def test_active_claim_fences_transitions(self):
        claim = self.ledger.claim(self.cid, 'worker')
        with self.assertRaises(ImprovementError):
            self.move('hypothesis', hypothesis())
        self.ledger.transition(self.cid, 1, 'hypothesis', hypothesis(), actor='worker', claim_token=claim['token'])

    def test_verification_must_use_frozen_fixture_set(self):
        self.prepare()
        with self.assertRaisesRegex(ImprovementError, 'fixture mismatch'):
            self.move('measured', dict(baseline_commit='d'*40, commit='c'*40, verification_artifact='sha256:' + HASH,
                held_out_passed=True, regressions=[], metrics=[]))

    def test_discovery_group_cannot_be_held_out(self):
        data = hypothesis()
        data['held_out_fixtures'][0]['group'] = 'game1'
        with self.assertRaisesRegex(ImprovementError, 'discovery evidence'):
            self.move('hypothesis', data)

    def test_later_discovery_evidence_invalidates_next_qualification(self):
        self.move('hypothesis', hypothesis())
        self.ledger.ingest(source('new-run', 'held'), proposal())
        with self.assertRaisesRegex(ImprovementError, 'discovery evidence'):
            self.move('reproduced', {})
        self.assertEqual('hypothesis', self.ledger.get(self.cid)['state'])

    def test_incomplete_strategic_evidence_stays_unvalidated(self):
        incomplete = source('partial-run', 'partial-group')
        incomplete['finding_qualification']['f1']['strategic_evidence_complete'] = False
        self.ledger.ingest(incomplete, proposal())
        self.move('hypothesis', hypothesis())
        with self.assertRaisesRegex(ImprovementError, 'insufficient'):
            self.move('reproduced', {})

    def test_ledger_intake_rejects_imprecise_evidence_refs(self):
        for refs in ([{}], ['anything'], [{'path': 'synthetic.json'}], [{'pointer': '/row'}]):
            data = source('new')
            data['evidence_refs'] = refs
            with self.assertRaises(ImprovementError):
                self.ledger.ingest(data, proposal())

    def test_discovery_artifact_cannot_be_held_out_under_another_group(self):
        data = hypothesis()
        discovery = source('new-source', 'another-group')
        discovery['discovery_artifact_ids'] = [data['held_out_fixtures'][0]['artifact_id']]
        self.ledger.ingest(discovery, proposal())
        with self.assertRaisesRegex(ImprovementError, 'discovery evidence'):
            self.move('hypothesis', data)
