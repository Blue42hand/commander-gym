"""Recorded native Force-of-Negation cost failure and bounded payment contracts."""
import gzip,json,unittest
from copy import deepcopy
from pathlib import Path
from commander_gym.heuristic_facts import CardFacts
from commander_gym.engine_deck_policy import EngineDeckPolicy
from commander_gym.policy_action_bounds import bounded_observation
from commander_gym.records import native_hand_exile_offer,validate_action_params,RecordValidationError
from commander_gym.openai_responses_pilot import _validate_native_action_params,OpenAIResponsesPilotError
from commander_gym.bounded_policy_fallback import enumerate_choices,constrain_native_format,BoundedPolicyFallback
from test_lmstudio import config,FakeTransport,completion

class CycleFiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).parent/'fixtures'
        cls.row=json.load(gzip.open(root/'cycle-05-regressions.json.gz','rt'))['1910']
        cls.facts=CardFacts(json.load(gzip.open(root/'cycle-04-public-facts.json.gz','rt')))
    def state(self):return deepcopy(self.row['observation'])
    def offer(self,o):return next(a for a in o['legalActions'] if a['actionId']==1)
    def test_missing_duplicate_stale_or_wrong_count_exile_never_leaves_harness(self):
        a=self.offer(self.state());base={'targets':['e331']}
        for extra in [{},{'exiledCards':[]},{'exiledCards':['e124']},{'exiledCards':['old-object']},
                      {'exiledCards':['e157','e157']},{'exiledCards':['e157','e165']},
                      {'exiledCards':['e157'],'exile':['e165']}]:
            for validate,error in [(validate_action_params,RecordValidationError),(_validate_native_action_params,OpenAIResponsesPilotError)]:
                with self.subTest(extra=extra,validator=validate.__name__),self.assertRaises(error):validate({**base,**extra},a)
        for key in ['exile','exiledCards']:
            validate_action_params({**base,key:['e157']},a)
            _validate_native_action_params({**base,key:['e157']},a)
    def test_frozen_counter_choices_include_every_offered_payment(self):
        o=self.state();before=deepcopy(o);choices=enumerate_choices(o);self.assertIsNotNone(choices)
        a=self.offer(o);w=[c for c in choices if c['choice']['semanticId']==a['semanticId']]
        self.assertEqual({tuple(c['choice']['params']['exiledCards']) for c in w},{('e157',),('e165',),('e135',)})
        for c in w:validate_action_params(c['choice']['params'],a)
        self.assertEqual(o,before)
    def test_native_schema_requires_bounded_canonical_exile_field(self):
        o=self.state();a=self.offer(o);fmt=constrain_native_format(o)
        def find(node):
            if isinstance(node,dict):
                if node.get('properties',{}).get('semanticId',{}).get('const')==a['semanticId']:return node['properties']['params']
                for v in node.values():
                    r=find(v)
                    if r:return r
            elif isinstance(node,list):
                for v in node:
                    r=find(v)
                    if r:return r
        spec=find(fmt);self.assertIn('exiledCards',spec['required']);self.assertNotIn('exile',spec['properties'])
        self.assertEqual(spec['properties']['exiledCards']['items']['enum'],['e157','e165','e135'])
        self.assertEqual(spec['properties']['exiledCards']['minItems'],1)
        self.assertEqual(spec['properties']['exiledCards']['maxItems'],1)
    def test_reserve_two_card_counter_for_threats_beyond_routine_rocks(self):
        o=self.state();before=deepcopy(o);b,r=bounded_observation(o,self.facts)
        self.assertFalse(any(a['actionId']==1 for a in b['legalActions']))
        c=EngineDeckPolicy(self.facts,'talrand').try_choose(o).choice;self.assertIsNotNone(c)
        self.assertEqual(next(a['kind'] for a in o['legalActions'] if a['actionId']==c.action_id),'PassPriority')
        self.assertEqual(o,before)
        for name in ['Sol Ring','Unknown engine','Time Warp']:
            o=self.state();o['state']['cards']['e331']['name']=name
            b,_=bounded_observation(o,self.facts);self.assertTrue(any(a['actionId']==1 for a in b['legalActions']),name)
    def test_deck_counter_override_survives_model_bounding(self):
        o=self.state();p=EngineDeckPolicy(self.facts,'talrand');p.counter_target_exceptions=frozenset({'Force of Negation'})
        r=p.try_choose(o);self.assertIsNone(r.choice)
        t=FakeTransport(completion(json.dumps({'option':0})));t.models['models'][0]['loaded_instances'][0]['config']['context_length']=131072
        c=BoundedPolicyFallback(config(max_prompt_bytes=122880,context_window=131072),t,facts=self.facts).choose_with_policy_context(o,[{'metadata':r.metadata}])
        self.assertEqual(c.metadata['boundedFallback']['mode'],'frozen-choices')
        self.assertIn('exiledCards',t.calls[-1][1]['messages'][1]['content'])
    def test_unknown_or_weighted_cost_retains_native_fallback(self):
        for change in ['kind','compound','weighted']:
            o=self.state();a=self.offer(o)
            if change=='kind':a['additionalCostInfo']['costType']='UnreviewedCost'
            if change=='compound':a['additionalCostInfo']['alsoRequired']=[{'costType':'PayLife'}]
            if change=='weighted':a['additionalCostInfo']['exileMinTotalWeight']=5
            self.assertIsNone(native_hand_exile_offer(a));self.assertIsNone(enumerate_choices(o))
