"""Complete payment plans remain bounded when another action needs native grammar."""
import json,unittest
from copy import deepcopy
from unittest.mock import patch
from commander_gym.bounded_policy_fallback import BoundedPolicyFallback,constrain_native_format,validate_selection_bounds
from commander_gym.convoke_plans import convoke_plans
from commander_gym.policy_action_bounds import bounded_observation
from commander_gym.pilot import PilotContractError
from commander_gym.lmstudio_pilot import _constrain_untargeted_actions
import test_heuristic_convoke as original
from test_lmstudio import config,FakeTransport,completion

class ConvokeNativeSchemaBoundsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        original.ConvokePaymentTests.setUpClass()
        cls.rows=original.ConvokePaymentTests.rows;cls.facts=original.ConvokePaymentTests.facts
    def state(self):return original.ConvokePaymentTests.state(self)
    def bounded(self):
        o,a=self.state();o,_=bounded_observation(o,self.facts);a=next(a for a in o['legalActions'] if a['actionId']==1)
        return o,a,convoke_plans(o,a)[0]
    def test_native_grammar_only_exposes_complete_payment_for_each_x(self):
        o,a,plan=self.bounded();fmt=constrain_native_format(o);_constrain_untargeted_actions(fmt['schema'],o)
        branch=next(b for b in fmt['schema']['properties']['choice']['anyOf'] if b['properties']['semanticId']['const']==a['semanticId'])
        params=branch['properties']['params'];self.assertIn('xValue',params['required'])
        self.assertNotIn('convokedCreatures',params['properties'])
        self.assertNotIn('convokedCreatures',params['required'])
        self.assertEqual(params['properties']['targets']['maxItems'],0)
    def test_incomplete_payment_is_rejected_before_native_delivery(self):
        o,a,plan=self.bounded()
        for params in [{'xValue':1},{'xValue':1,'convokedCreatures':{}},{'xValue':2,'convokedCreatures':plan['convokedCreatures']}]:
            with self.assertRaisesRegex(PilotContractError,'complete-payment-plan'):
                validate_selection_bounds({'channel':'action','choice':{'semanticId':a['semanticId'],'params':params}},o)
        validate_selection_bounds({'channel':'action','choice':{'semanticId':a['semanticId'],'params':plan}},o)
    def test_unknown_payment_keeps_native_domain(self):
        o,a,plan=self.bounded();a['manaCostString']='{X}{G/P}{G}{G}'
        fmt=constrain_native_format(o);branch=next(b for b in fmt['schema']['properties']['choice']['anyOf'] if b['properties']['semanticId']['const']==a['semanticId'])
        self.assertIn('convokedCreatures',branch['properties']['params']['properties'])
    def test_native_schema_retry_replaces_empty_payment_with_complete_payment(self):
        o,a,plan=self.bounded();bad={'channel':'action','choice':{'semanticId':a['semanticId'],'params':{'xValue':1,'convokedCreatures':{}}}}
        good=deepcopy(bad);good['choice']['params']={'xValue':1}
        t=FakeTransport(completion(json.dumps(bad)),completion(json.dumps(good)))
        t.models['models'][0]['loaded_instances'][0]['config']['context_length']=131072
        with patch('commander_gym.bounded_policy_fallback.enumerate_choices',return_value=None):
            c=BoundedPolicyFallback(config(max_prompt_bytes=122880,context_window=131072),t,facts=self.facts).choose_with_policy_context(o,[])
        self.assertEqual(c.params,plan);self.assertEqual(c.metadata['boundedFallback']['mode'],'native-schema')
        self.assertEqual(len(c.metadata['modelIo']['attempts']),2)
        self.assertEqual(c.metadata['boundedFallback']['convokePaymentCompletion']['source'],'harness-payment-plan')
        self.assertEqual(json.loads(c.metadata['modelIo']['attempts'][1]['response']['outputText']),good)
        self.assertEqual(c.metadata['modelIo']['attempts'][0]['response']['validationError'],'output_schema_mismatch')

    def test_partial_plans_do_not_shrink_the_native_X_domain(self):
        o,a,plan=self.bounded();a['maxAffordableX']=20
        self.assertIsNone(convoke_plans(o,a))
        partial=convoke_plans(o,a,require_complete=False)
        self.assertTrue(any(p['xValue']==1 for p in partial))
        fmt=constrain_native_format(o)
        branch=next(b for b in fmt['schema']['properties']['choice']['anyOf'] if b['properties']['semanticId']['const']==a['semanticId'])
        self.assertIn('convokedCreatures',branch['properties']['params']['properties'])
        self.assertEqual(branch['properties']['params']['properties']['xValue']['maximum'],20)
    def test_single_color_batches_are_not_split_into_other_colors(self):
        o,a,plan=self.bounded()
        for source in a['availableManaSources']:
            if source['entityId']=='e18':source['manaAmount']=4
        self.assertIsNotNone(convoke_plans(o,a))
        actor=next(p for p in o['state']['players'] if p['playerId']==o['agentToAct'])
        actor['manaPool']['restrictedMana']=[{'amount':1}]
        self.assertIsNone(convoke_plans(o,a))
