"""Recorded early An Offer You Can't Refuse on an unthreatening Brainstorm."""
import gzip,json,unittest
from copy import deepcopy
from pathlib import Path
from commander_gym.heuristic_facts import CardFacts
from commander_gym.engine_deck_policy import EngineDeckPolicy
from commander_gym.policy_action_bounds import bounded_observation

class CycleNineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).parent/'fixtures';cls.row=json.load(gzip.open(root/'cycle-09-regressions.json.gz','rt'))['1101']
        cls.facts=CardFacts(json.load(gzip.open(root/'cycle-07-public-facts.json.gz','rt')))
    def state(self):return deepcopy(self.row['observation'])
    def test_preserve_costly_counter_instead_of_refunding_opponent_mana_for_a_filter(self):
        o=self.state();before=deepcopy(o);b,r=bounded_observation(o,self.facts)
        for a in b['legalActions']:
            if o['state']['cards'].get(a['action'].get('cardId'),{}).get('name')=="An Offer You Can't Refuse":self.assertNotIn('e105',a.get('validTargets') or [])
        c=EngineDeckPolicy(self.facts,'alela').try_choose(o).choice;self.assertIsNotNone(c)
        self.assertEqual(next(a['kind'] for a in o['legalActions'] if a['actionId']==c.action_id),'PassPriority');self.assertEqual(o,before)
    def test_engines_tutors_and_unknown_spells_keep_model_choices(self):
        for name in ['Rhystic Study','Demonic Tutor','Mystical Tutor','Life\'s Legacy','Unknown spell']:
            o=self.state();o['state']['cards']['e105']['name']=name;b,_=bounded_observation(o,self.facts)
            self.assertTrue(any('e105' in (a.get('validTargets') or []) for a in b['legalActions']),name)
    def test_deck_override_can_approve_combo_specific_counter_usage(self):
        o=self.state();b,_=bounded_observation(o,self.facts,counter_target_exceptions={"An Offer You Can't Refuse"})
        self.assertTrue(any('e105' in (a.get('validTargets') or []) for a in b['legalActions']))

class GeneralCompetencyNineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).parent/'fixtures';cls.rows=json.load(gzip.open(root/'cycle-09-regressions.json.gz','rt'))
        cls.facts=CardFacts(json.load(gzip.open(root/'cycle-09-public-facts.json.gz','rt')))
    def state(self,n):return deepcopy(self.rows[str(n)]['observation'])
    def test_develop_commander_colours_before_discretionary_removal(self):
        from commander_gym.default_policies import DefaultPolicySubsystem
        for policy in [DefaultPolicySubsystem(self.facts),EngineDeckPolicy(self.facts,'alela')]:
            o=self.state(4034);before=deepcopy(o);c=policy.try_choose(o).choice
            self.assertIsNotNone(c)
            a=next(a for a in o['legalActions'] if a['actionId']==c.action_id)
            self.assertEqual(o['state']['cards'][a['action']['cardId']]['name'],'Patchwork Banner')
            self.assertEqual(c.metadata['heuristic']['reason'],'default-develop-commander-mana-capacity');self.assertEqual(o,before)
    def test_mana_setup_does_not_override_native_payment_or_visible_survival(self):
        from commander_gym.default_policies import DefaultPolicySubsystem
        policy=DefaultPolicySubsystem(self.facts)
        for variant in ['payment','stack','life','native-disallowed','unknown-facts']:
            o=self.state(4034)
            if variant=='payment':o['nativePaymentError']={'message':'Cannot pay'}
            elif variant=='stack':
                next(z for z in o['state']['zones'] if z['zoneId']['zoneType']=='Stack')['cardIds']=['e200']
            elif variant=='life':next(p for p in o['state']['players'] if p['playerId']==o['agentToAct'])['life']=1
            elif variant=='native-disallowed':
                o['legalActions']=[a for a in o['legalActions'] if o['state']['cards'].get(a['action'].get('cardId'),{}).get('name')!='Patchwork Banner']
            else:
                document=self.facts.document();document['cards']['Patchwork Banner'].pop('developmentMana');policy=DefaultPolicySubsystem(CardFacts(document))
            self.assertIsNone(policy._commander_mana_setup(o,o['legalActions']),variant)
    def test_removal_targets_live_killable_engine_instead_of_suppressed_commander(self):
        o=self.state(4069);before=deepcopy(o);b,r=bounded_observation(o,self.facts)
        self.assertNotIn('e200',b['pendingDecision']['legalTargets']['0'])
        self.assertTrue(set(b['pendingDecision']['legalTargets']['0'])<= {'e248','e268','e191'});self.assertIn(b['pendingDecision']['legalTargets']['0'][0],{'e248','e268'})
        self.assertTrue(any(x['rule']=='fixed-stat-removal-live-threat-shortlist' for x in r));self.assertEqual(o,before)
    def test_unknown_effects_and_unkillable_targets_retain_model_domain(self):
        o=self.state(4069);o['pendingDecision']['context']['effectHint']='Different or mixed effect';b,_=bounded_observation(o,self.facts)
        self.assertEqual(b['pendingDecision'],o['pendingDecision'])
        o=self.state(4069)
        for t in o['pendingDecision']['legalTargets']['0']:o['state']['cards'][t]['toughness']=10
        b,_=bounded_observation(o,self.facts);self.assertEqual(b['pendingDecision'],o['pendingDecision'])
    def test_noop_self_untap_is_excluded_but_tapped_or_uncertain_source_is_preserved(self):
        for tapped,excluded in [(False,True),(True,False),(None,False)]:
            o=self.state(9550);o['state']['cards']['e81']['isTapped']=tapped;before=deepcopy(o);b,r=bounded_observation(o,self.facts)
            actual=any(a['action'].get('abilityId')=='Staff of Domination:1' for a in b['legalActions'])
            self.assertEqual(actual,not excluded);self.assertEqual(o,before)
    def test_deck_override_preserves_reviewed_self_target_plan(self):
        o=self.state(4069);b,_=bounded_observation(o,self.facts,allow_self_targets={'Nowhere to Run'})
        self.assertEqual(b['pendingDecision'],o['pendingDecision'])

    def test_model_sees_public_imprint_link_and_complete_linked_rules(self):
        from commander_gym.bounded_policy_fallback import decision_context
        o=self.state(9417);before=deepcopy(o)
        view=decision_context(o,frozen=True,policy_context={},facts=self.facts)
        self.assertEqual(view['cards']['e139']['linkedExile'],['e160'])
        self.assertEqual(view['cards']['e160']['oracleText'],o['state']['cards']['e160']['oracleText'])
        self.assertNotIn('e160',view.get('summarizedInactiveCards',[]));self.assertEqual(o,before)
        # An opaque/nonvisible linked entity never becomes a prompt reference.
        o['state']['cards']['e139']['linkedExile'].append('masked-secret')
        o['state']['cards']['masked-secret']={'name':'Hidden identity','oracleText':'secret'}
        view=decision_context(o,frozen=True,policy_context={},facts=self.facts)
        self.assertEqual(view['cards']['e139']['linkedExile'],['e160'])
        self.assertNotIn('masked-secret',str(view));self.assertNotIn('Hidden identity',str(view))
        o['state']['cards']['e160']['zone']='EXILE'
        view=decision_context(o,frozen=True,policy_context={},facts=self.facts)
        self.assertEqual(view['cards']['e160']['oracleText'],o['state']['cards']['e160']['oracleText'])

    def test_copy_targets_a_different_live_threat_and_cannot_keep_original_target(self):
        from commander_gym.bounded_policy_fallback import enumerate_choices
        o=self.state(5924);before=deepcopy(o);b,r=bounded_observation(o,self.facts)
        self.assertNotIn('e200',b['pendingDecision']['legalTargets']['0'])
        self.assertEqual(b['pendingDecision']['targetRequirements'][0]['minTargets'],1)
        choices=enumerate_choices(b);self.assertIsNotNone(choices)
        self.assertTrue(all(c['response'].get('selectedTargets',{}).get('0') for c in choices))
        self.assertTrue(any(x['rule']=='spread-neutralization-copies-across-live-threats' for x in r));self.assertEqual(o,before)
    def test_counter_threatening_original_neutralization_restores_copy_uncertainty(self):
        o=self.state(5924);s=o['state'];s['cards']['response']={'name':'Negate','controllerId':'policy-talrand','targets':[{'type':'Spell','spellEntityId':'e319'}]}
        next(z for z in s['zones'] if z['zoneId']['zoneType']=='Stack')['cardIds'].append('response')
        b,r=bounded_observation(o,self.facts)
        self.assertEqual(b['pendingDecision'],o['pendingDecision'])
        self.assertFalse(any(x['rule']=='spread-neutralization-copies-across-live-threats' for x in r))

    def test_counter_imprint_waits_for_live_spell_without_model_call(self):
        o=self.state(9417);before=deepcopy(o);b,r=bounded_observation(o,self.facts)
        self.assertFalse(any(a['action'].get('abilityId')=='Isochron Scepter:2' for a in b['legalActions']))
        c=EngineDeckPolicy(self.facts,'talrand').try_choose(o).choice;self.assertIsNotNone(c)
        self.assertEqual(next(a['kind'] for a in o['legalActions'] if a['actionId']==c.action_id),'PassPriority');self.assertEqual(o,before)
    def test_live_stack_and_unreviewed_imprints_keep_copy_activation(self):
        for variant in ['live-stack','draw-imprint','unknown-imprint']:
            o=self.state(9417)
            if variant=='live-stack':next(z for z in o['state']['zones'] if z['zoneId']['zoneType']=='Stack')['cardIds']=['e200']
            else:o['state']['cards']['e160']['name']='Brainstorm' if variant=='draw-imprint' else 'Unknown card'
            b,_=bounded_observation(o,self.facts)
            self.assertTrue(any(a['action'].get('abilityId')=='Isochron Scepter:2' for a in b['legalActions']),variant)
    def test_approved_activation_payoff_can_override_noop_utility_bounds(self):
        for sequence,name,ability in [(9417,'Isochron Scepter','Isochron Scepter:2'),(9550,'Staff of Domination','Staff of Domination:1')]:
            o=self.state(sequence);b,_=bounded_observation(o,self.facts,allow_self_targets={name})
            self.assertTrue(any(a['action'].get('abilityId')==ability for a in b['legalActions']))

    def test_actual_failed_regrowth_menu_becomes_twenty_complete_choices(self):
        from commander_gym.bounded_policy_fallback import enumerate_choices,BoundedPolicyFallback
        from test_lmstudio import config,FakeTransport,completion,discovery
        o=deepcopy(self.rows['provider-failure']['observation']);before=deepcopy(o);b,_=bounded_observation(o,self.facts)
        choices=enumerate_choices(b);self.assertIsNotNone(choices);self.assertEqual(len(choices),20)
        offers={a['semanticId']:a for a in o['legalActions']}
        regrowth=next(a for a in o['legalActions'] if o['state']['cards'].get(a['action'].get('cardId'),{}).get('name')=='Regrowth')
        index=next(i for i,c in enumerate(choices) if c['choice']['semanticId']==regrowth['semanticId'] and c['choice']['params'].get('targets')==['e94'])
        t=FakeTransport(completion(json.dumps({'option':index})));t.models=discovery();t.models['models'][0]['loaded_instances'][0]['config']['context_length']=131072
        c=BoundedPolicyFallback(config(context_window=131072,max_prompt_bytes=122880),t,facts=self.facts).choose(o)
        self.assertEqual(c.action_id,regrowth['actionId']);self.assertEqual(c.params,{'targets':['e94']})
        self.assertEqual(t.calls[1][1]['response_format']['json_schema']['name'],'bounded_policy_choice');self.assertEqual(o,before)
