"""Explicit offline report adapter profile; not a replacement recording schema.

The analysis skill's semantic report becomes a recorder-compatible annotation via
an explicit lossless hash mapping. This validates structure/provenance, not the
truth of judgments or seat-safety of arbitrary prose. No artifact is executed.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import PurePosixPath
import re
from urllib.parse import urlparse

from .game_journal import PIN_KEYS

REPORT_PROFILE = 'commander-gym.analysis-report.v1'
HASH_BASIS = 'canonical_manifest_artifact_descriptors_v1'
CATEGORIES = {'model_judgment', 'observation_failure', 'action_or_schema_failure',
              'native_rules_candidate', 'primer_weakness_candidate',
              'recording_or_integrity_failure', 'infrastructure_failure', 'insufficient_evidence'}


class AnalysisReportError(ValueError):
    pass


def require(value, message):
    if not value:
        raise AnalysisReportError(message)


def text(value, label):
    require(isinstance(value, str) and value.strip(), label + ' requires text')


def strings(value, label):
    require(isinstance(value, list), label + ' requires an explicit array')
    for item in value:
        text(item, label)


def evidence_refs(value, *, manifest=None, nonempty=True):
    require(isinstance(value, list) and (bool(value) or not nonempty), 'evidence_refs requires an array')
    for ref in value:
        require(isinstance(ref, dict), 'evidence reference must be an object')
        require(('path' in ref) != ('link' in ref), 'reference requires exactly one source path/link')
        if 'path' in ref:
            path = ref['path']
            text(path, 'source path')
            parsed = PurePosixPath(path)
            require(not parsed.is_absolute() and '..' not in parsed.parts and '\\' not in path,
                    'source path must be a portable relative locator')
            if manifest is not None:
                require(path in {a['path'] for a in manifest['artifacts']}, 'reference path absent from verified manifest')
        else:
            text(ref['link'], 'source link')
            link = urlparse(ref['link'])
            require(link.scheme == 'https' and link.netloc and not link.username and not link.password,
                    'source link must be an HTTPS locator without credentials')
        precise = False
        if 'pointer' in ref:
            pointer = ref['pointer']
            require(isinstance(pointer, str) and pointer.startswith('/') and
                    not re.search(r'~(?![01])', pointer), 'invalid precise JSON pointer')
            precise = True
        if 'line' in ref:
            require(type(ref['line']) is int and ref['line'] > 0, 'line must be positive')
            precise = True
        if 'line_span' in ref:
            span = ref['line_span']
            require(isinstance(span, list) and len(span) == 2 and
                    all(type(n) is int for n in span) and 0 < span[0] <= span[1], 'invalid line span')
            precise = True
        for key in ('row_id', 'event_id'):
            if key in ref:
                text(ref[key], key)
                precise = True
        require(precise, 'source reference requires a pointer, line span or stable row/event ID')


def _fact(value, manifest, label):
    if value is None:
        return
    require(isinstance(value, dict), label + ' requires an object or explicit null')
    text(value.get('summary'), label + ' summary')
    evidence_refs(value.get('evidence_refs'), manifest=manifest)


def validate_report(report, manifest, version):
    text(version, 'analysis version')
    require(isinstance(report, dict) and report.get('report_profile') == REPORT_PROFILE,
            'unsupported or missing explicit analysis report profile')
    require(report.get('run_id') == manifest['run_id'] and report.get('artifact_hash') == manifest['artifact_hash']
            and report.get('analysis_version') == version, 'report identity mismatch')
    expected_scope = [a['path'] for a in manifest['artifacts']]
    provenance = report.get('artifact_hash_provenance')
    require(isinstance(provenance, dict) and provenance.get('algorithm') == 'sha256' and
            provenance.get('value') == manifest['artifact_hash'] and provenance.get('scope') == expected_scope
            and provenance.get('basis') == HASH_BASIS, 'hash algorithm/value/scope/basis mismatch')
    require(report.get('idempotency_key') == [manifest['run_id'], manifest['artifact_hash'], version],
            'report idempotency mismatch')
    classification = report.get('classification')
    require(classification in {'native_completed', 'finalized_failed', 'finalized_aborted', 'integrity_blocked'},
            'report requires a finalized classification')
    require(type(report.get('native_completed')) is bool and
            report['native_completed'] == (classification == 'native_completed'), 'native classification mismatch')
    if report['native_completed']:
        require(manifest['outcome'].get('kind') == 'native_terminal', 'report cannot invent native completion')
    require('terminal_reason' in report and (report['terminal_reason'] is None or
            isinstance(report['terminal_reason'], str)), 'terminal reason must be explicit')
    outcome = report.get('outcome')
    require(isinstance(outcome, dict) and 'recorded_result' in outcome and
            type(outcome.get('qualified')) is bool and (not outcome['qualified'] or report['native_completed']),
            'outcome qualification requires native completion')
    evidence_refs(outcome.get('evidence'), manifest=manifest, nonempty=False)
    require(isinstance(report.get('pins'), dict) and all(key in report['pins'] for key in PIN_KEYS),
            'all recorder pin sections must be explicit, using null for unavailable pins')
    integrity = report.get('integrity')
    require(isinstance(integrity, dict) and integrity.get('stable_after_review') is True,
            'stable integrity review required')
    strings(integrity.get('checks'), 'integrity checks')
    strings(integrity.get('gaps'), 'integrity gaps')
    coverage = report.get('coverage')
    require(isinstance(coverage, dict), 'coverage required')
    for key in ('total_decisions', 'usable_seat_observations', 'known_legal_sets', 'reviewed_decisions'):
        require(key in coverage and (coverage[key] is None or type(coverage[key]) is int and coverage[key] >= 0),
                'coverage values must be explicit nonnegative counts or null')
        if coverage.get('total_decisions') is not None and coverage[key] is not None:
            require(coverage[key] <= coverage['total_decisions'], 'coverage exceeds total decisions')
    text(coverage.get('selection_method'), 'selection method')
    strings(coverage.get('exclusions'), 'coverage exclusions')
    telemetry = report.get('telemetry')
    require(isinstance(telemetry, dict) and isinstance(telemetry.get('by_seat_model'), list)
            and isinstance(telemetry.get('missing_fields'), list), 'explicit telemetry/missing fields required')
    for key in ('calls', 'retries', 'invalid_actions', 'fallbacks', 'timeouts', 'tokens', 'latency', 'wall_time', 'cost'):
        require(key in telemetry, 'telemetry must explicitly preserve unavailable values: ' + key)
    cost = telemetry['cost']
    require(isinstance(cost, dict) and cost.get('basis') in {'recorded', 'estimated', 'unknown'} and
            all(key in cost for key in ('amount', 'currency', 'pricing_source')), 'explicit cost basis required')
    strings(report.get('privacy_retention_gaps'), 'privacy gaps')
    text(report.get('summary'), 'report summary')
    findings = report.get('findings')
    require(isinstance(findings, list), 'findings required')
    indexed = {}
    for finding in findings:
        require(isinstance(finding, dict), 'finding must be an object')
        fid = finding.get('finding_id')
        text(fid, 'finding_id')
        require(fid not in indexed, 'duplicate finding_id')
        indexed[fid] = finding
        require(finding.get('category') in CATEGORIES, 'invalid finding category')
        require(finding.get('severity') in {'critical', 'high', 'medium', 'low', 'informational'}, 'invalid severity')
        require(finding.get('confidence') in {'high', 'medium', 'low'}, 'invalid confidence')
        for key in ('confidence_rationale', 'impact', 'recommended_next_step'):
            text(finding.get(key), key)
        for key in ('alternative_explanations', 'decision_ids', 'event_ids', 'row_ids',
                    'relevant_cards_actions', 'counterevidence', 'missing_information'):
            strings(finding.get(key), key)
        for key in ('seat', 'turn', 'phase'):
            require(key in finding and (finding[key] is None or type(finding[key]) in {str, int}),
                    key + ' must be explicit')
        require(type(finding.get('needs_permission')) is bool and
                finding.get('outcome_used_for_decision_quality') is False, 'permission/outcome separation required')
        evidence_refs(finding.get('evidence_refs'), manifest=manifest)
        for key in ('observation_available_then', 'selected_action'):
            require(key in finding, key + ' required or explicit null')
            _fact(finding[key], manifest, key)
        require('legal_alternatives' in finding and (finding['legal_alternatives'] is None or
                isinstance(finding['legal_alternatives'], list)), 'legal alternatives required or explicit null')
        for alternative in finding['legal_alternatives'] or []:
            require(isinstance(alternative, dict), 'alternative must be an object')
            text(alternative.get('action'), 'alternative action')
            text(alternative.get('feasibility'), 'alternative timing/target/resource feasibility')
            evidence_refs(alternative.get('evidence_refs'), manifest=manifest)
        require('ex_ante_comparison' in finding and (finding['ex_ante_comparison'] is None or
                isinstance(finding['ex_ante_comparison'], str) and finding['ex_ante_comparison'].strip()),
                'ex ante comparison required or explicit null')
        if any(finding[key] is None for key in ('observation_available_then', 'selected_action',
                                               'legal_alternatives', 'ex_ante_comparison')):
            require(finding['missing_information'], 'unavailable decision evidence requires missing-information reasons')
    candidates = report.get('learning_candidates')
    require(isinstance(candidates, list), 'learning candidates required')
    for candidate in candidates:
        require(isinstance(candidate, dict) and candidate.get('status') == 'unvalidated',
                'learning candidates must remain unvalidated')
        for key in ('hypothesis', 'bounded_validation_idea'):
            text(candidate.get(key), key)
        for key in ('supporting_finding_ids', 'competing_explanations', 'required_evidence'):
            strings(candidate.get(key), key)
        require(candidate['supporting_finding_ids'] and set(candidate['supporting_finding_ids']) <= set(indexed),
                'learning candidate has unresolved finding references')
    return indexed


def recorder_profile_from_skill(report, manifest):
    """Pure, explicit hash-object mapping; never save or mutate source reports.

    Finding fields must already use this agreed profile. There is no heuristic
    alias inference. A caller must inspect/match actual producer output first.
    """
    require(isinstance(report, dict) and isinstance(report.get('artifact_hash'), dict),
            'skill report hash object required')
    result = deepcopy(report)
    provenance = result['artifact_hash']
    require(provenance.get('algorithm') == 'sha256' and provenance.get('value') == manifest['artifact_hash']
            and provenance.get('scope') == [a['path'] for a in manifest['artifacts']],
            'skill hash must match verified manifest scope')
    result['artifact_hash'] = provenance['value']
    result['artifact_hash_provenance'] = {**provenance, 'basis': HASH_BASIS}
    result['report_profile'] = REPORT_PROFILE
    validate_report(result, manifest, result.get('analysis_version'))
    return result


def finding_qualification(finding, *, report):
    """Structural strategic-evidence gate, not an adjudication/training label."""
    return dict(confidence=finding['confidence'], strategic_evidence_complete=bool(
        report['classification'] != 'integrity_blocked' and finding['observation_available_then'] is not None and finding['selected_action'] is not None and
        finding['legal_alternatives'] and finding['ex_ante_comparison'] and finding['confidence'] != 'low'))
