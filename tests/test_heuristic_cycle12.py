"""Keep ordinary reactive resources for a threat that needs an answer."""
import gzip,json,unittest
from pathlib import Path
from copy import deepcopy
from commander_gym.heuristic_facts import CardFacts
from commander_gym.policy_action_bounds import bounded_observation,disables_only_opposing_permanent

class ReactiveResourcesTwelveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).parent/'fixtures';cls.rows=json.load(gzip.open(root/'cycle-12-regressions.json.gz','rt'))
        cls.facts=CardFacts(json.load(gzip.open(root/'cycle-11-public-facts.json.gz','rt')))
    def state(self,key):return deepcopy(self.rows[key]['observation'])
    def sources(self,o):return {o['state']['cards'].get(a['action'].get('cardId'),{}).get('name') for a in o['legalActions']}
    def test_counter_does_not_free_an_opponents_creature_from_another_opponents_pacifism(self):
        o=self.state('2361');before=deepcopy(o);self.assertTrue(disables_only_opposing_permanent(o,'e246'))
        b,m=bounded_observation(o,self.facts);self.assertNotIn('Swan Song',self.sources(b));self.assertEqual(o,before)
        self.assertIn('preserve-counter-while-opponents-disable-each-other',{x['rule'] for x in m})
    def test_own_target_unknown_target_and_unknown_mechanics_keep_counter_choice(self):
        for variant in ['own','unknown-controller','not-battlefield','unknown-spell','multiple','malformed','own-spell']:
            o=self.state('2361');cs=o['state']['cards'];spell=cs['e246'];target=spell['targets'][0]['entityId']
            if variant=='own':cs[target]['controllerId']=o['agentToAct']
            elif variant=='unknown-controller':cs[target]['controllerId']=None
            elif variant=='not-battlefield':
                for z in o['state']['zones']:
                    if z['zoneId']['zoneType']=='Battlefield' and target in z['cardIds']:z['cardIds'].remove(target)
            elif variant=='unknown-spell':spell['name']='Unknown mixed aura'
            elif variant=='multiple':spell['targets'].append({'type':'Permanent','entityId':'e100'})
            elif variant=='malformed':spell['targets']=['unknown']
            else:spell['controllerId']=o['agentToAct']
            self.assertFalse(disables_only_opposing_permanent(o,'e246'),variant)
            if variant in {'own-spell','malformed'}:continue
            b,_=bounded_observation(o,self.facts);self.assertIn('Swan Song',self.sources(b),variant)
    def test_deck_can_explicitly_allow_a_political_counter(self):
        o=self.state('2361');b,_=bounded_observation(o,self.facts,counter_target_exceptions={'Swan Song'})
        self.assertIn('Swan Song',self.sources(b))
    def test_instant_protection_waits_for_stack_or_combat(self):
        o=self.state('8241');before=deepcopy(o);b,m=bounded_observation(o,self.facts)
        self.assertNotIn('Heroic Intervention',self.sources(b));self.assertEqual(o,before)
        self.assertIn('save-instant-protection-for-stack-or-combat',{x['rule'] for x in m})
        for variant in ['stack','combat','deck-exception','unknown-step']:
            o=self.state('8241');kwargs={}
            if variant=='stack':
                o['state']['cards']['live']={'name':'Unknown live threat'}
                next(z for z in o['state']['zones'] if z['zoneId']['zoneType']=='Stack')['cardIds']=['live']
            elif variant=='combat':o['state']['currentStep']='DECLARE_BLOCKERS';o['state']['combat']={'attackers':[{'creatureId':'e0'}]}
            elif variant=='unknown-step':o['state']['currentStep']='UNKNOWN_STEP'
            else:kwargs['reaction_timing_exceptions']={'Heroic Intervention'}
            b,_=bounded_observation(o,self.facts,**kwargs);self.assertIn('Heroic Intervention',self.sources(b),variant)

class FreeLibraryInspectionTwelveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).parent/'fixtures';cls.rows=json.load(gzip.open(root/'cycle-12-regressions.json.gz','rt'))
        cls.facts=CardFacts(json.load(gzip.open(root/'cycle-12-public-facts.json.gz','rt')))
    def state(self):return deepcopy(self.rows['game18-1397']['observation'])
    def test_actual_declined_free_library_inspection_is_accepted_locally(self):
        from commander_gym.default_policies import DefaultPolicySubsystem
        from commander_gym.engine_deck_policy import EngineDeckPolicy
        for seq in ['1397','2573']:
            for policy in [DefaultPolicySubsystem(self.facts),EngineDeckPolicy(self.facts,'sythis')]:
                o=deepcopy(self.rows['game18-'+seq]['observation']);before=deepcopy(o);r=policy.try_choose(o)
                self.assertIsNotNone(r.choice);self.assertTrue(r.choice.response['choice'])
                self.assertEqual(r.choice.metadata['heuristic']['reason'],'free-optional-library-reorder');self.assertEqual(o,before)
    def test_unknown_paid_different_identity_or_opponent_choice_remains_with_model(self):
        from commander_gym.default_policies import DefaultPolicySubsystem
        for variant in ['unknown-contract','unknown-ability','paid','phase','opponent','different-source']:
            o=self.state();doc=self.facts.document();p=o['pendingDecision'];ctx=p['context']
            if variant=='unknown-contract':doc['cards']["Mirri's Guile"].pop('freeOptionalLibraryReorders')
            elif variant=='unknown-ability':ctx['abilityIdentity']['abilityId']='unknown'
            elif variant=='paid':p['yesDetail']='Pay {1}'
            elif variant=='phase':ctx['phase']='ACTIVATION'
            elif variant=='opponent':o['state']['cards'][ctx['sourceId']]['controllerId']='policy-talrand'
            else:o['state']['cards'][ctx['sourceId']]['name']='Unknown other card'
            self.assertIsNone(DefaultPolicySubsystem(CardFacts(doc)).try_choose(o).choice,variant)
    def test_acceptance_does_not_choose_order_or_disclose_unrevealed_library(self):
        from commander_gym.default_policies import DefaultPolicySubsystem
        from commander_gym.engine_deck_policy import EngineDeckPolicy
        o=deepcopy(self.rows['game18-2580']['observation']);before=deepcopy(o)
        for policy in [DefaultPolicySubsystem(self.facts),EngineDeckPolicy(self.facts,'sythis')]:
            self.assertIsNone(policy.try_choose(o).choice)
        self.assertEqual(o,before)
    def test_free_reorder_contract_requires_bounded_native_ability_counts(self):
        for invalid in [[],{'bad':0},{'bad':True},{'bad':11}]:
            doc=self.facts.document();doc['cards']["Mirri's Guile"]['freeOptionalLibraryReorders']=invalid
            with self.assertRaises(ValueError):CardFacts(doc)

class CompleteAnnouncedXTwelveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).parent/'fixtures';cls.row=json.load(gzip.open(root/'cycle-12-regressions.json.gz','rt'))['game18-failed-syncopate']
        cls.facts=CardFacts(json.load(gzip.open(root/'cycle-12-public-facts.json.gz','rt')))
    def state(self):return deepcopy(self.row['observation'])
    def test_actual_syncopate_offer_has_complete_choices_for_every_native_affordable_x(self):
        from commander_gym.bounded_policy_fallback import enumerate_choices
        o=self.state();before=deepcopy(o);a=next(a for a in o['legalActions'] if a.get('hasXCost'))
        choices=enumerate_choices(o);self.assertIsNotNone(choices)
        casts=[c for c in choices if c['choice']['semanticId']==a['semanticId']]
        self.assertEqual([c['choice']['params']['xValue'] for c in casts],list(range(a['minX'],a['maxAffordableX']+1)))
        self.assertTrue(all(c['choice']['params']['targets']==['e300'] for c in casts));self.assertEqual(o,before)
    def test_model_ranks_native_complete_x_cast_without_returning_parameters(self):
        from commander_gym.bounded_policy_fallback import enumerate_choices,BoundedPolicyFallback
        from test_lmstudio import FakeTransport,completion,config
        o=self.state();bounded,_=bounded_observation(o,self.facts);choices=enumerate_choices(bounded);index=next(i for i,c in enumerate(choices) if c['choice']['params'].get('xValue')==1)
        t=FakeTransport(completion(json.dumps({'option':index})));t.models['models'][0]['loaded_instances'][0]['config']['context_length']=131072
        c=BoundedPolicyFallback(config(max_prompt_bytes=122880,context_window=131072),t,facts=self.facts).choose_with_policy_context(o,[{'metadata':{'policyLayers':{'status':'deferred'}}}])
        self.assertEqual(c.params,{'xValue':1,'targets':['e300']});self.assertEqual(c.metadata['boundedFallback']['mode'],'frozen-choices')
    def test_unknown_large_or_material_x_domains_keep_native_schema(self):
        from commander_gym.bounded_policy_fallback import enumerate_choices
        for variant in ['unknown-max','large','delve','extra-cost','target-depends-on-x','missing-field']:
            o=self.state();a=next(a for a in o['legalActions'] if a.get('hasXCost'))
            if variant=='unknown-max':a['maxAffordableX']=None
            elif variant=='large':a['maxAffordableX']=200
            elif variant=='delve':a['hasDelve']=True
            elif variant=='extra-cost':a['additionalCostInfo']={'costType':'Unknown'}
            elif variant=='target-depends-on-x':a['xConstrainsTargetManaValue']=True
            else:a['parameterSpec']['allowedFields'].pop('xValue')
            self.assertIsNone(enumerate_choices(o),variant)

    def test_conditional_counter_defaults_require_positive_x_and_allow_deck_override(self):
        from commander_gym.bounded_policy_fallback import enumerate_choices
        o=self.state();before=deepcopy(o)
        b,restrictions=bounded_observation(o,self.facts)
        self.assertEqual([w['choice']['params']['xValue'] for w in enumerate_choices(b) if 'xValue' in w['choice']['params']],[1])
        self.assertIn('conditional-counter-needs-positive-X',{x['rule'] for x in restrictions});self.assertEqual(o,before)
        b,_=bounded_observation(o,self.facts,counter_target_exceptions={'Syncopate'})
        self.assertEqual([w['choice']['params']['xValue'] for w in enumerate_choices(b) if 'xValue' in w['choice']['params']],[0,1])
        next(a for a in o['legalActions'] if a.get('hasXCost'))['maxAffordableX']=0
        b,_=bounded_observation(o,self.facts)
        self.assertFalse(any(a.get('hasXCost') for a in b['legalActions']))
