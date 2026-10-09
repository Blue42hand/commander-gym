from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from commander_gym.card_readiness import analyze_readiness_artifact, TYPE, HUMAN_SCOPES, BASE_GATES
from commander_gym.card_opportunities import CardOpportunityError
from commander_gym.annotations import AnnotationStore
from commander_gym.evidence import RawEvidenceStore
from commander_gym.storage import StorageLayout
from tests.test_annotations import AnnotationTests


class ReadinessTests(unittest.TestCase):
    def assess(self, *, facts=None, scopes=None, human=True, regression=False,
               stale=False, gates=None, source_mismatch=False, pass_choice=False,
               dependency_revision=None, termination=None):
        fixture = AnnotationTests()
        from commander_gym.records import ActionRecord
        record = replace(fixture.make_record(),
            legal_actions=[ActionRecord('cast',dict(kind='CastSpell',sourceEntityId='c',affordable=True))],
            chosen_action_id='cast', observation=dict(type='Game',
            schemaHash='argentum-schema-v9', perspectivePlayerId='a', stateDigest='s',
            zones=[dict(ownerId='a', zoneType='HAND', hidden=False,
                cards=[dict(entityId='c', cardDefinitionId='def')])]))
        if pass_choice:
            record = replace(record, legal_actions=fixture.make_record().legal_actions,
                             chosen_action_id='pass')
        run = replace(fixture.make_run(), engine=replace(fixture.make_run().engine, revision='a'*40))
        if termination is not None:
            run = replace(run, termination=termination)
        dep = dependency_revision or 'b'*40
        with tempfile.TemporaryDirectory() as tmp:
            layout = StorageLayout.create(Path(tmp)); layout.ensure_directories()
            source = RawEvidenceStore(layout).write(run, [record], commander_gym_revision='b'*40).artifact.artifact_id
            payload = dict(card_definition_id='def', implementation_revision='c'*40 if stale else 'a'*40,
                dependency_revision=dep, requirements_revision='d'*40, facts=facts or [],
                human_confirmation=list(HUMAN_SCOPES) if scopes is None else scopes,
                uses_only_existing_primitives=True, human_validated_abilities=['draw'], confirmation_reference='private explicit confirmation message', regression=regression,
                gates=[dict(name=g, passed=True, reference='exact test/review/CI result') for g in BASE_GATES] if gates is None else gates)
            annotation = replace(fixture.annotation(source), annotation_type=TYPE, payload=payload,
                annotator=dict(source='human' if human else 'automated-review', identity='reviewer'))
            aid = AnnotationStore(layout).write(annotation).artifact.artifact_id
            if source_mismatch:
                source = RawEvidenceStore(layout).write(replace(run, run_id='other-run'), [record], commander_gym_revision='b'*40).artifact.artifact_id
            from commander_gym.card_opportunities import analyze_artifact
            return analyze_artifact(layout, source, generator_revision='b'*40,
                readiness_annotation_artifact_ids=[aid, aid], readiness_requirements=dict(
                implementation_revision='a'*40, dependency_revision=dep,
                requirements_revision='d'*40, required_abilities=['draw'], required_gates=[]))['upstream_readiness']

    def facts(self):
        return [dict(stage=s, human_game=True, human_game_reference='private participant review',
            ability='draw', evidence_pointers=['/decisions/0/provenance/outcome/result_observation/stateDigest'])
            for s in ['played', 'resolved', 'ability']]

    def test_human_participation_without_play_and_resolution_is_insufficient(self):
        result = self.assess(scopes=[])['cards'][0]
        self.assertFalse(result['eligible_for_batch_proposal'])
        self.assertIn('human_confirmation:read_every_line', result['blockers'])
        self.assertIn('human_game_evidence:resolved', result['blockers'])

    def test_selection_does_not_prove_resolution(self):
        facts = self.facts(); facts[1]['evidence_pointers']=['/decisions/0/target/chosen_action_id']
        with self.assertRaises(CardOpportunityError): self.assess(facts=facts)

    def test_automated_reviewer_cannot_supply_human_confirmation(self):
        with self.assertRaises(CardOpportunityError): self.assess(facts=self.facts(), human=False)

    def test_exact_scoped_confirmations_and_gates_propose_only(self):
        result = self.assess(facts=self.facts())
        self.assertFalse(result['cards'][0]['eligible_for_batch_proposal'])
        self.assertIn('native_execution_join_unavailable', result['cards'][0]['blockers'])
        self.assertEqual(1, len(result['annotation_artifact_ids']))

    def test_revision_change_invalidates_prior_qualification(self):
        result = self.assess(facts=self.facts(), stale=True)['cards'][0]
        self.assertFalse(result['eligible_for_batch_proposal'])
        self.assertIn('current_revision_evidence', result['blockers'])

    def test_regression_blocks_even_complete_evidence(self):
        self.assertFalse(self.assess(facts=self.facts(), regression=True)['cards'][0]['eligible_for_batch_proposal'])

    def test_missing_ci_and_required_ability_remain_blockers(self):
        result = self.assess(facts=self.facts()[:2], gates=[])['cards'][0]
        self.assertIn('gate:ci', result['blockers'])
        self.assertIn('ability_evidence:draw', result['blockers'])

    def test_annotations_from_another_raw_source_are_rejected(self):
        with self.assertRaises(CardOpportunityError):
            self.assess(facts=self.facts(), source_mismatch=True)

    def test_outcome_digest_cannot_qualify_pass_as_card_play(self):
        with self.assertRaises(CardOpportunityError):
            self.assess(facts=self.facts(), pass_choice=True)

    def test_caller_and_annotation_cannot_repin_unchanged_dependency_source(self):
        with self.assertRaises(CardOpportunityError):
            self.assess(facts=self.facts(), dependency_revision='e'*40)

    def test_diagnostic_only_source_stays_blocked(self):
        from commander_gym.run_records import RunTermination
        result = self.assess(facts=self.facts(), termination=RunTermination(status='stopped',reason='operator_stop'))
        self.assertIn('diagnostic_only_source', result['cards'][0]['blockers'])
