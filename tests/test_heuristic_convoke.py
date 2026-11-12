"""Convoke wire and payment-plan contracts from a native rejected game state.

The historical state predates the new native parameter field. The one added
field below is a synthetic projection of the separately tested native contract.
"""
import gzip,json,unittest
from pathlib import Path
from copy import deepcopy
from commander_gym.records import validate_action_params,RecordValidationError
from commander_gym.openai_responses_pilot import _validate_native_action_params,_native_action_format,OpenAIResponsesPilotError
from commander_gym.heuristic_facts import CardFacts
from commander_gym.bounded_policy_fallback import BoundedPolicyFallback,enumerate_choices
from commander_gym.policy_action_bounds import bounded_observation
from commander_gym.convoke_plans import convoke_plans
from test_lmstudio import config,FakeTransport,completion

class ConvokePaymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).parent/'fixtures'
        with gzip.open(root/'cycle-03-regressions.json.gz','rt') as f:cls.rows=json.load(f)
        cls.facts=CardFacts.load(root/'competence-public-facts.json')
    def state(self):
        o=deepcopy(self.rows['6232']['observation']);a=next(a for a in o['legalActions'] if a['actionId']==1)
        a['parameterSpec']['allowedFields']['convokedCreatures']='CONVOKE_PAYMENT_MAP'
        return o,a
    def test_recorded_failure_becomes_complete_x_one_payment(self):
        o,a=self.state();b,r=bounded_observation(o,self.facts);a=next(a for a in b['legalActions'] if a['actionId']==1)
        self.assertEqual(a['minX'],1)
        plans=convoke_plans(b,a);self.assertEqual(len(plans),1);p=plans[0]
        self.assertEqual(p['xValue'],1)
        # Mystic pays the final green via native AutoPay, never a second tap.
        self.assertEqual(set(p['convokedCreatures']),{'e0','e92','e423'})
        self.assertEqual(sum(v['color']=='GREEN' for v in p['convokedCreatures'].values()),2)
        self.assertEqual(sum(v['color'] is None for v in p['convokedCreatures'].values()),1)
        validate_action_params(p,a);_validate_native_action_params(p,a)
    def test_unoffered_creatures_colors_and_malformed_payments_are_rejected(self):
        o,a=self.state()
        for payments in [{'unknown':{'color':'GREEN'}},{'e92':{'color':'RED'}},{'e92':{'color':[]}},{'e92':{'sourceId':'bad'}},{'e92':'GREEN'}]:
            p={'xValue':1,'convokedCreatures':payments}
            with self.subTest(payment=payments):
                with self.assertRaises(RecordValidationError):validate_action_params(p,a)
                with self.assertRaises(OpenAIResponsesPilotError):_validate_native_action_params(p,a)
    def test_schema_requires_announced_x_and_bounds_convoke_colors(self):
        o,a=self.state();fmt=_native_action_format(o)
        branch=next(b for b in fmt['schema']['properties']['choice']['anyOf'] if b['properties']['semanticId']['const']==a['semanticId'])
        params=branch['properties']['params'];self.assertIn('xValue',params['required'])
        colors=params['properties']['convokedCreatures']['properties']
        self.assertEqual(set(colors),{'e18','e92','e0','e423'})
        self.assertEqual(colors['e92']['properties']['color']['enum'],['GREEN',None])
        self.assertFalse(params['properties']['convokedCreatures']['additionalProperties'])
    def test_model_ranks_complete_payments_without_assembling_fields(self):
        o,a=self.state();b,_=bounded_observation(o,self.facts);choices=enumerate_choices(b)
        i=next(i for i,w in enumerate(choices) if w['choice']['semanticId']==a['semanticId'])
        t=FakeTransport(completion(json.dumps({'option':i})))
        t.models['models'][0]['loaded_instances'][0]['config']['context_length']=131072
        c=BoundedPolicyFallback(config(max_prompt_bytes=122880,context_window=131072),t,facts=self.facts).choose_with_policy_context(o,[])
        self.assertEqual(c.metadata['boundedFallback']['mode'],'frozen-choices')
        self.assertEqual(c.params['xValue'],1);self.assertTrue(c.params['convokedCreatures'])
        validate_action_params(c.params,a)
    def test_unknown_costs_and_old_unexpressible_contract_defer(self):
        o,a=self.state();a['manaCostString']='{X}{G/P}{G}{G}';self.assertIsNone(convoke_plans(o,a))
        o,a=self.state();a['parameterSpec']['allowedFields'].pop('convokedCreatures')
        self.assertIsNone(convoke_plans(o,a));self.assertIsNone(enumerate_choices(o))
        with self.assertRaises(RecordValidationError):validate_action_params({'convokedCreatures':{'e92':{'color':'GREEN'}}},a)
    def test_native_ceiling_is_not_silently_narrowed_when_plan_is_unproved(self):
        o,a=self.state();a['maxAffordableX']=20
        self.assertIsNone(convoke_plans(o,a))
