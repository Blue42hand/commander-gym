"""Native captured land drops must not stall behind a different unfamiliar land."""
import gzip,json,unittest
from pathlib import Path
from copy import deepcopy
from commander_gym.default_policies import DefaultPolicySubsystem
from commander_gym.engine_deck_policy import EngineDeckPolicy
from commander_gym.heuristic_facts import CardFacts

class LandDevelopmentElevenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).parent/'fixtures'
        cls.rows=json.load(gzip.open(root/'cycle-11-regressions.json.gz','rt'))
        cls.facts=CardFacts(json.load(gzip.open(root/'cycle-11-public-facts.json.gz','rt')))
    def state(self):return deepcopy(self.rows['4671']['observation'])
    def chosen(self,o,r):
        self.assertIsNotNone(r.choice)
        return next(a for a in o['legalActions'] if a['actionId']==r.choice.action_id)
    def test_captured_missed_land_drops_are_local_and_observation_unchanged(self):
        for seq,row in self.rows.items():
            if seq=='failed-delve':continue
            o=deepcopy(row['observation']);before=deepcopy(o)
            for p in [DefaultPolicySubsystem(self.facts),EngineDeckPolicy(self.facts,o['agentToAct'].removeprefix('policy-'))]:
                self.assertEqual(self.chosen(o,p.try_choose(o))['kind'],'PlayLand',seq)
            self.assertEqual(o,before)
    def test_known_land_progresses_even_with_another_unknown_timing(self):
        doc=self.facts.document();doc['cards']['Shattered Sanctum'].pop('conditionalTappedEntry')
        f=CardFacts(doc);o=self.state();a=self.chosen(o,EngineDeckPolicy(f,'alela').try_choose(o))
        self.assertIn(o['state']['cards'][a['action']['cardId']]['name'],['Island','Reliquary Tower'])
    def test_only_unknown_land_still_defers_to_model(self):
        for missing in ['timing','mana']:
            o=self.state();o['legalActions']=[a for a in o['legalActions'] if a['kind']!='PlayLand' or o['state']['cards'][a['action']['cardId']]['name']=='Shattered Sanctum']
            doc=self.facts.document()
            if missing=='timing':doc['cards']['Shattered Sanctum']['conditionalTappedEntry']=False
            else:doc['cards']['Shattered Sanctum']['manaColors']=[]
            self.assertIsNone(DefaultPolicySubsystem(CardFacts(doc)).try_choose(o).choice,missing)
    def test_only_ordinary_conditional_land_is_played_conservatively(self):
        o=self.state();o['legalActions']=[a for a in o['legalActions'] if a['kind']!='PlayLand' or o['state']['cards'][a['action']['cardId']]['name']=='Shattered Sanctum']
        p=DefaultPolicySubsystem(self.facts);a=self.chosen(o,p.try_choose(o))
        card=o['state']['cards'][a['action']['cardId']]
        self.assertEqual(card['name'],'Shattered Sanctum');self.assertTrue(p._enters_tapped(card,[],o))
        self.assertFalse(self.facts.get(card['name'])['entersTapped'])
    def test_land_menu_explicitly_explains_no_mana_cost(self):
        from commander_gym.bounded_policy_fallback import enumerate_choices,_candidate_description
        o=self.state();lands=[_candidate_description(c,o,self.facts) for c in enumerate_choices(o) if c['channel']=='action' and next(a for a in o['legalActions'] if a['semanticId']==c['choice']['semanticId'])['kind']=='PlayLand']
        self.assertTrue(lands);self.assertTrue(all(c['costBasis']=='land drop, no mana' for c in lands))

class DelvePaymentElevenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).parent/'fixtures'
        cls.row=json.load(gzip.open(root/'cycle-11-regressions.json.gz','rt'))['failed-delve']
        cls.facts=CardFacts(json.load(gzip.open(root/'cycle-11-public-facts.json.gz','rt')))
    def state(self):
        o=deepcopy(self.row['observation']);a=next(a for a in o['legalActions'] if a.get('hasDelve'))
        return o,a
    def wire(self,a,ids):return {'channel':'action','choice':{'semanticId':a['semanticId'],'params':{'delvedCards':ids}}}
    def test_native_payment_schema_requires_four_to_six_distinct_offered_cards(self):
        from commander_gym.bounded_policy_fallback import constrain_native_format,enumerate_choices
        o,a=self.state();before=deepcopy(o);self.assertIsNone(enumerate_choices(o))
        fmt=constrain_native_format(o)
        branch=next(b for b in fmt['schema']['properties']['choice']['anyOf'] if b['properties']['semanticId']['const']==a['semanticId'])
        params=branch['properties']['params'];rule=params['properties']['delvedCards']
        self.assertEqual((rule['minItems'],rule['maxItems']),(4,6))
        self.assertEqual(rule['items']['enum'],[c['entityId'] for c in a['validDelveCards']])
        self.assertIn('delvedCards',params['required']);self.assertEqual(o,before)
    def test_insufficient_duplicate_excess_and_unoffered_payment_rejected(self):
        from commander_gym.bounded_policy_fallback import validate_selection_bounds
        from commander_gym.pilot import PilotContractError
        o,a=self.state();ids=[c['entityId'] for c in a['validDelveCards']]
        for selected in [[],ids[:3],ids[:7],[ids[0]]*4,ids[:3]+['unknown']]:
            with self.assertRaisesRegex(PilotContractError,'delve-outside-native-bounds'):validate_selection_bounds(self.wire(a,selected),o)
        for n in [4,5,6]:validate_selection_bounds(self.wire(a,ids[:n]),o)
    def test_zero_minimum_allows_ordinary_mana_cast_and_unknown_mixed_cost_stays_uncertain(self):
        from commander_gym.bounded_policy_fallback import validate_selection_bounds,_delve_bounds
        o,a=self.state();a['minDelveNeeded']=0;validate_selection_bounds(self.wire(a,[]),o)
        a['hasConvoke']=True;self.assertIsNone(_delve_bounds(a))
        a['hasConvoke']=False;a.pop('minDelveNeeded');self.assertIsNone(_delve_bounds(a))
    def test_model_supplies_valid_bounded_payment_from_actual_failed_input(self):
        from commander_gym.bounded_policy_fallback import BoundedPolicyFallback
        from test_lmstudio import FakeTransport,completion,config
        o,a=self.state();ids=[c['entityId'] for c in a['validDelveCards']][:4]
        t=FakeTransport(completion(json.dumps(self.wire(a,ids))))
        t.models['models'][0]['loaded_instances'][0]['config']['context_length']=131072
        c=BoundedPolicyFallback(config(max_prompt_bytes=122880,context_window=131072),t,facts=self.facts).choose_with_policy_context(o,[{'metadata':{'policyLayers':{'status':'deferred'}}}])
        self.assertEqual(c.action_id,a['actionId']);self.assertEqual(c.params['delvedCards'],ids)
    def test_actual_empty_payment_and_duplicates_retry_before_native_execution(self):
        from commander_gym.bounded_policy_fallback import BoundedPolicyFallback
        from test_lmstudio import FakeTransport,completion,config
        for invalid in [[],['e156']*4]:
            o,a=self.state();ids=[c['entityId'] for c in a['validDelveCards']][:4]
            t=FakeTransport(completion(json.dumps(self.wire(a,invalid))),completion(json.dumps(self.wire(a,ids))))
            t.models['models'][0]['loaded_instances'][0]['config']['context_length']=131072
            c=BoundedPolicyFallback(config(max_prompt_bytes=122880,context_window=131072),t,facts=self.facts).choose_with_policy_context(o,[{'metadata':{'policyLayers':{'status':'deferred'}}}])
            self.assertEqual(c.params['delvedCards'],ids);self.assertEqual(c.metadata['retryCount'],1)
            self.assertEqual(sum(path=='/v1/chat/completions' for path,_,_ in t.calls),2)
