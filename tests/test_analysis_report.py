"""Profile validation and recorder integration; entirely synthetic and offline."""
from copy import deepcopy
import os
from pathlib import Path
import tempfile
import unittest

from commander_gym.analysis_report import (
    AnalysisReportError, recorder_profile_from_skill, validate_report, evidence_refs,
)
from commander_gym.game_analysis import claim_analysis, finish_analysis
from commander_gym.game_journal import PIN_KEYS, PrivateGameJournal, verify_finalized_manifest
from commander_gym.improvement import ImprovementLedger, ImprovementError
from tests._analysis_report_fixture import skill_report
from tests.test_improvement import proposal


class AnalysisReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.game = Path(self.tmp.name) / 'game'
        with PrivateGameJournal(self.game, 'synthetic-profile', {k: None for k in PIN_KEYS}) as j:
            j.finish({'kind': 'operator_stop'}, expected_sources={}, gaps=['partial'])
        self.manifest = verify_finalized_manifest(self.game)
        self.skill = skill_report(self.manifest)
        self.report = recorder_profile_from_skill(self.skill, self.manifest)
        self.ledger = ImprovementLedger(Path(self.tmp.name) / 'private')

    def finish(self, report, version='1.0.0'):
        claim = claim_analysis(self.game, version)
        finish_analysis(self.game, version, claim['claim_id'], report)

    def test_lossless_hash_map_and_full_recorder_ingestion(self):
        original = deepcopy(self.skill)
        self.assertEqual(self.skill['artifact_hash']['scope'], self.report['artifact_hash_provenance']['scope'])
        self.assertEqual(self.manifest['artifact_hash'], self.report['artifact_hash'])
        self.assertEqual(original, self.skill)
        self.finish(self.report)
        cid = self.ledger.ingest_report(self.game, '1.0.0', proposal('infrastructure_failure'), correlation_group='synthetic')
        candidate = self.ledger.get(cid)
        self.assertEqual('insufficient_evidence', candidate['state'])
        self.assertEqual(self.report['artifact_hash_provenance'], candidate['sources'][0]['artifact_hash_provenance'])
        self.assertFalse(candidate['sources'][0]['finding_qualification']['f1']['strategic_evidence_complete'])
        self.assertEqual([a['artifact_id'] for a in self.manifest['artifacts']],
                         candidate['sources'][0]['discovery_artifact_ids'])

    def test_unknown_profile_and_hash_scope_are_rejected(self):
        for field, value in [('report_profile', 'unknown'), ('artifact_hash', {'value': 'fake'}),
                             ('idempotency_key', ['wrong'])]:
            report = deepcopy(self.report)
            report[field] = value
            with self.assertRaises(AnalysisReportError):
                validate_report(report, self.manifest, '1.0.0')
        for field, value in [('algorithm', 'md5'), ('scope', ['manifest.json']), ('value', 'b'*64), ('basis', 'raw_file')]:
            report = deepcopy(self.report)
            report['artifact_hash_provenance'][field] = value
            with self.assertRaises(AnalysisReportError):
                validate_report(report, self.manifest, '1.0.0')

    def test_findings_require_full_semantic_field_map(self):
        for key in ('finding_id', 'category', 'confidence_rationale', 'counterevidence',
                    'observation_available_then', 'selected_action', 'legal_alternatives', 'ex_ante_comparison',
                    'missing_information', 'outcome_used_for_decision_quality', 'seat', 'recommended_next_step'):
            report = deepcopy(self.report)
            del report['findings'][0][key]
            with self.subTest(key=key), self.assertRaises(AnalysisReportError):
                validate_report(report, self.manifest, '1.0.0')
        for key in ('coverage', 'integrity', 'telemetry', 'pins', 'learning_candidates'):
            report = deepcopy(self.report)
            del report[key]
            with self.subTest(key=key), self.assertRaises(AnalysisReportError):
                validate_report(report, self.manifest, '1.0.0')

    def test_evidence_reference_shape_precision_and_manifest_membership(self):
        bad = [None, [{}], ['anything'], [{'path': '000000.jsonl'}], [{'line': 1}],
               [{'path': '../secret', 'line': 1}], [{'path': 'missing.json', 'pointer': '/x'}],
               [{'path': '000000.jsonl', 'line': 0}], [{'path': '000000.jsonl', 'pointer': '/~broken'}],
               [{'path': '000000.jsonl', 'line_span': [3, 1]}]]
        for refs in bad:
            with self.subTest(refs=refs), self.assertRaises(AnalysisReportError):
                evidence_refs(refs, manifest=self.manifest)
        for precise in ({'pointer': '/row~0~1'}, {'line': 1}, {'line_span': [1, 2]}, {'row_id': 'synthetic-row'}):
            evidence_refs([{'path': self.manifest['artifacts'][0]['path'], **precise}], manifest=self.manifest)

    def test_completed_receipt_does_not_bypass_semantic_validation(self):
        report = deepcopy(self.report)
        report['findings'][0]['evidence_refs'] = [{}]
        self.finish(report)  # recorder checks byte identity, not semantic completeness
        with self.assertRaises(ImprovementError):
            self.ledger.ingest_report(self.game, '1.0.0', proposal('infrastructure_failure'), correlation_group='synthetic')

    def test_report_and_receipt_must_be_private_regular_files(self):
        self.finish(self.report)
        for name in ('report.json', 'review.json'):
            path = self.game / 'analysis/1.0.0' / name
            os.chmod(path, 0o644)
            with self.assertRaisesRegex(ImprovementError, 'private regular'):
                self.ledger.ingest_report(self.game, '1.0.0', proposal('infrastructure_failure'), correlation_group='synthetic')
            os.chmod(path, 0o600)

    def test_native_outcome_and_learning_candidate_cannot_be_invented(self):
        report = deepcopy(self.report)
        report.update(native_completed=True, classification='native_completed')
        with self.assertRaises(AnalysisReportError):
            validate_report(report, self.manifest, '1.0.0')
        report = deepcopy(self.report)
        report['learning_candidates'][0]['status'] = 'validated'
        with self.assertRaises(AnalysisReportError):
            validate_report(report, self.manifest, '1.0.0')
        report['learning_candidates'][0].update(status='unvalidated', supporting_finding_ids=['unknown'])
        with self.assertRaises(AnalysisReportError):
            validate_report(report, self.manifest, '1.0.0')
