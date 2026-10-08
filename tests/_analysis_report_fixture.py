"""Project-owned diagnostic report: explicit unknowns, no invented pilot choices."""
from commander_gym.game_journal import PIN_KEYS


def skill_report(manifest, version='1.0.0'):
    ref = dict(path=manifest['artifacts'][0]['path'], line=1)
    return dict(
        analysis_version=version, run_id=manifest['run_id'],
        artifact_hash=dict(algorithm='sha256', value=manifest['artifact_hash'],
                           scope=[a['path'] for a in manifest['artifacts']]),
        idempotency_key=[manifest['run_id'], manifest['artifact_hash'], version],
        classification='finalized_aborted', native_completed=False, terminal_reason='operator_stop',
        outcome=dict(recorded_result=None, qualified=False, evidence=[ref]),
        pins={k: None for k in PIN_KEYS},
        integrity=dict(checks=['verified finalized source'], gaps=['partial capture'], stable_after_review=True),
        coverage=dict(total_decisions=None, usable_seat_observations=None, known_legal_sets=None,
                      reviewed_decisions=0, selection_method='technical diagnostics only', exclusions=['no choices']),
        telemetry=dict(by_seat_model=[], calls=None, retries=None, invalid_actions=None, fallbacks=None,
                       timeouts=None, tokens=None, latency=None, wall_time=None,
                       cost=dict(amount=None, currency=None, basis='unknown', pricing_source=None),
                       missing_fields=['all per-call telemetry']),
        findings=[dict(finding_id='f1', category='infrastructure_failure', severity='high', confidence='medium',
            confidence_rationale='Synthetic terminal receipt supports the stop classification',
            seat=None, turn=None, phase=None, decision_ids=[], event_ids=[], row_ids=[], relevant_cards_actions=[],
            evidence_refs=[ref], observation_available_then=None, selected_action=None, legal_alternatives=None,
            ex_ante_comparison=None, impact='A native outcome cannot be inferred',
            alternative_explanations=['Operator intentionally stopped'], counterevidence=['No native terminal receipt'],
            missing_information=['No pilot-visible choice or alternative is recorded'],
            outcome_used_for_decision_quality=False, recommended_next_step='Inspect stop source offline', needs_permission=False)],
        learning_candidates=[dict(hypothesis='Stop classification can be made explicit', supporting_finding_ids=['f1'],
            competing_explanations=['Intentional stop'], required_evidence=['Offline receipt fixture'],
            bounded_validation_idea='Use a synthetic finalized stop receipt', status='unvalidated')],
        privacy_retention_gaps=[], summary='Synthetic aborted-run diagnostic; no strategic judgment')
