"""Private upstream readiness judgments joined through existing AnnotationStore.

This validates evidence shape and qualification boundaries, not the truth of a
reviewer's judgments. It never publishes cards or reads native/private traces.
"""
from __future__ import annotations
import re
from .card_opportunities import CardOpportunityError, _pointer

TYPE = 'card_upstream_readiness.v1'
HUMAN_SCOPES = {'read_every_line', 'manual_playthrough', 'correctness', 'ux_both_seats'}
BASE_GATES = {'oracle_fidelity', 'composition_review', 'test_plan', 'tests', 'review', 'ci'}
STAGES = {'deck_presence', 'observed_available', 'played', 'resolved', 'ability'}


def _revision(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{40}', value):
        raise CardOpportunityError('readiness requires exact implementation/dependency commits')
    return value


def _assess_readiness(envelope, annotations, *, implementation_revision,
                     dependency_revision, requirements_revision, required_abilities,
                     required_gates):
    """Assess one card using an explicit current annotation set; no latest guessing.

    Revision pins conservatively invalidate all old evidence after any engine or
    relevant dependency change. Callers must include new failures/regressions and
    select superseding revisions explicitly, as with usefulness annotations.
    """
    pins = dict(implementation_revision=_revision(implementation_revision),
                dependency_revision=_revision(dependency_revision),
                requirements_revision=_revision(requirements_revision))
    for values in (required_abilities, required_gates):
        if not isinstance(values, list) or len(values) != len(set(values)) or any(
                not isinstance(v, str) or not v.strip() for v in values):
            raise CardOpportunityError('required abilities/gates must be explicit unique names')
    required_gates = sorted(BASE_GATES | set(required_gates))
    decisions = {d['provenance']['decision_id']: (i, d) for i, d in enumerate(envelope['decisions'])}
    cards = {}
    for annotation in annotations:
        if annotation.annotation_type != TYPE:
            raise CardOpportunityError('readiness annotation type mismatch')
        p = annotation.payload
        card_id = p.get('card_definition_id')
        if not isinstance(card_id, str) or not card_id:
            raise CardOpportunityError('readiness requires exact card identity')
        for key in pins:
            _revision(p.get(key))
        entry = cards.setdefault(card_id, dict(card_definition_id=card_id,
            facts={stage: [] for stage in sorted(STAGES)}, human_scopes=[], human_validated_abilities=[], gates={},
            stale_annotations=[], blockers=[], annotation_ids=[]))
        if any(p.get(k) != v for k, v in pins.items()):
            entry['stale_annotations'].append(annotation.annotation_id)
            continue
        entry['annotation_ids'].append(annotation.annotation_id)
        if annotation.target_kind != 'decision' or annotation.target_id not in decisions:
            raise CardOpportunityError('readiness must target an exact decision')
        i, decision = decisions[annotation.target_id]
        # Identity must be visible in the acting seat's canonical observation.
        from .card_opportunities import frame_from_decision
        frame = frame_from_decision(decision, evidence_ref={})
        if card_id not in {c['identity'] for c in frame['cards']}:
            raise CardOpportunityError('readiness card is not visible to acting seat')
        if envelope['producer'].get('revision') != pins['dependency_revision']:
            raise CardOpportunityError('source Gym dependency revision does not match')
        if envelope['qualification']['diagnostic_only']:
            entry['blockers'].append('diagnostic_only_source')
        if envelope['run']['engine'].get('revision') != pins['implementation_revision']:
            raise CardOpportunityError('source engine revision does not match implementation')
        facts = p.get('facts', [])
        if not isinstance(facts, list):
            raise CardOpportunityError('readiness facts must be an array')
        for fact in facts:
            stage = fact.get('stage')
            pointers = fact.get('evidence_pointers')
            if stage not in STAGES or not isinstance(pointers, list) or not pointers:
                raise CardOpportunityError('readiness fact requires stage and exact evidence')
            if type(fact.get('human_game')) is not bool:
                raise CardOpportunityError('human-game exposure must be explicit')
            if fact['human_game'] and (not isinstance(fact.get('human_game_reference'), str) or
                    not fact['human_game_reference'].strip()):
                raise CardOpportunityError('human-game classification requires a review reference')
            # Deck presence is independently reviewed, never inferred from a hand.
            allowed = f'/decisions/{i}/provenance/' if stage == 'deck_presence' else (
                f'/decisions/{i}/input/' if stage == 'observed_available' else
                f'/decisions/{i}/provenance/outcome/')
            for pointer in pointers:
                if not isinstance(pointer, str) or not pointer.startswith(allowed):
                    raise CardOpportunityError('readiness evidence violates stage boundary')
                _pointer(envelope, pointer)
            if stage == 'ability' and not isinstance(fact.get('ability'), str):
                raise CardOpportunityError('ability evidence requires an explicit scope')
            if stage == 'played':
                selected = frame['selected']
                identities = {c['identity'] for c in frame['cards']
                              if c['instance_id'] == selected['play_instance_id']}
                if selected['status'] != 'known' or card_id not in identities:
                    raise CardOpportunityError('played claim must join the selected visible play action')
            if stage in {'played', 'resolved', 'ability'}:
                # Canonical outcomes preserve result observations, not a card-scoped
                # native acceptance/resolution event contract. Keep claims inspectable
                # without manufacturing execution proof from a changed state digest.
                if 'native_execution_join_unavailable' not in entry['blockers']:
                    entry['blockers'].append('native_execution_join_unavailable')
            entry['facts'][stage].append(dict(fact))
        confirmations = p.get('human_confirmation', [])
        if not isinstance(confirmations, list) or any(s not in HUMAN_SCOPES for s in confirmations):
            raise CardOpportunityError('invalid human confirmation scope')
        validated_abilities = p.get('human_validated_abilities', [])
        if not isinstance(validated_abilities, list) or any(
                not isinstance(a, str) or not a.strip() for a in validated_abilities):
            raise CardOpportunityError('human ability validation requires named scopes')
        if confirmations or validated_abilities:
            if (annotation.annotator.get('source') != 'human' or
                    not isinstance(annotation.annotator.get('identity'), str) or
                    not annotation.annotator['identity'].strip() or
                    not isinstance(p.get('confirmation_reference'), str) or
                    not p['confirmation_reference'].strip()):
                raise CardOpportunityError('human confirmation requires identified explicit attestation')
            entry['human_scopes'].extend(confirmations)
            entry['human_validated_abilities'].extend(validated_abilities)
        gates = p.get('gates', [])
        if not isinstance(gates, list):
            raise CardOpportunityError('readiness gates must be an array')
        for gate in gates:
            if (not isinstance(gate.get('name'), str) or type(gate.get('passed')) is not bool or
                    not isinstance(gate.get('reference'), str) or not gate['reference'].strip()):
                raise CardOpportunityError('gate requires named result and review/test/CI reference')
            entry['gates'].setdefault(gate['name'], []).append(gate['passed'])
        if type(p.get('regression')) is not bool:
            raise CardOpportunityError('regression status must be explicit')
        if p.get('uses_only_existing_primitives') is not True:
            entry['blockers'].append('batch_scope_requires_existing_primitives')
        if p['regression']:
            entry['blockers'].append('relevant_regression')
    for entry in cards.values():
        blockers = entry['blockers']
        for scope in sorted(HUMAN_SCOPES - set(entry['human_scopes'])):
            blockers.append('human_confirmation:' + scope)
        for stage in ('played', 'resolved'):
            if not any(f['human_game'] for f in entry['facts'][stage]):
                blockers.append('human_game_evidence:' + stage)
        abilities = {f.get('ability') for f in entry['facts']['ability'] if f['human_game']}
        blockers.extend('ability_evidence:' + a for a in required_abilities if a not in abilities)
        blockers.extend('human_ability_confirmation:' + a for a in required_abilities
                        if a not in entry['human_validated_abilities'])
        blockers.extend('gate:' + g for g in required_gates
                        if not entry['gates'].get(g) or not all(entry['gates'][g]))
        if not entry['annotation_ids']:
            blockers.append('current_revision_evidence')
        entry['eligible_for_batch_proposal'] = not blockers
    return dict(pins=pins, required_abilities=required_abilities,
                required_gates=required_gates, cards=list(cards.values()),
                privacy='private; never publish this report or source annotations')


def analyze_readiness_artifact(layout, source_artifact_id, *, annotation_artifact_ids,
                               implementation_revision, dependency_revision,
                               requirements_revision, required_abilities, required_gates):
    """Canonical-only integration. Native manifests cannot substitute for raw blobs."""
    import json
    from .annotations import AnnotationStore
    from .evidence import validate_raw_evidence_envelope
    from .storage import LocalArtifactStore
    envelope = json.loads(LocalArtifactStore(layout).read_bytes(source_artifact_id))
    validate_raw_evidence_envelope(envelope)
    annotations = [AnnotationStore(layout).read_artifact(a)
                   for a in sorted(set(annotation_artifact_ids))]
    for annotation in annotations:
        if annotation.source_evidence_artifact_id != source_artifact_id:
            raise CardOpportunityError('readiness annotation exact source mismatch')
    report = _assess_readiness(envelope, annotations,
        implementation_revision=implementation_revision, dependency_revision=dependency_revision,
        requirements_revision=requirements_revision, required_abilities=required_abilities,
        required_gates=required_gates)
    report['source'] = dict(artifact_id=source_artifact_id, run_id=envelope['run']['run_id'])
    report['annotation_artifact_ids'] = sorted(set(annotation_artifact_ids))
    return report
