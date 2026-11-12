"""Recorded native failures and timing mistakes from the eighth mixed game."""
import gzip,json,unittest
from pathlib import Path
from copy import deepcopy
from commander_gym.heuristic_facts import CardFacts
from commander_gym.default_policies import DefaultPolicySubsystem
from commander_gym.engine_deck_policy import EngineDeckPolicy
from commander_gym.lathril_policy import LathrilDeckSubsystem
from commander_gym.policy_action_bounds import bounded_observation
from commander_gym.bounded_policy_fallback import enumerate_choices,_candidate_description,_action,BoundedPolicyFallback
from test_lmstudio import config,FakeTransport,completion

class CycleTwoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).parent/'fixtures'
        with gzip.open(root/'cycle-02-regressions.json.gz','rt') as f:cls.rows=json.load(f)
        cls.facts=CardFacts.load(root/'competence-public-facts.json')
    def state(self,n):return deepcopy(self.rows[str(n)]['observation'])
    def policy(self,o):
        deck=o['agentToAct'].removeprefix('policy-')
        return LathrilDeckSubsystem(self.facts) if deck=='lathril' else EngineDeckPolicy(self.facts,deck)
    def test_early_draw_activations_and_own_stack_wait(self):
        for n in [2568,3205,4577,4066,5326]:
            o=self.state(n);before=deepcopy(o);r=self.policy(o).try_choose(o)
            self.assertIsNotNone(r.choice,n)
            self.assertEqual(next(a['kind'] for a in o['legalActions'] if a['actionId']==r.choice.action_id),'PassPriority',n)
            self.assertEqual(before,o)
    def test_commander_exile_recovers_but_hand_keeps_cheaper_cast(self):
        p=DefaultPolicySubsystem(self.facts)
        self.assertTrue(p.try_choose(self.state(3604)).choice.response['choice'])
        self.assertFalse(p.try_choose(self.state(4101)).choice.response['choice'])
    def test_floating_mana_pays_counter_tax(self):
        o=self.state(4997);r=DefaultPolicySubsystem(self.facts).try_choose(o)
        self.assertIsNotNone(r.choice)
        self.assertTrue(DefaultPolicySubsystem(self.facts)._floating_payable(o,'{2}'))
        self.assertFalse(DefaultPolicySubsystem(self.facts)._floating_payable(o,'{99}'))
    def test_roil_describes_only_paid_kicker_draw(self):
        o=self.state(4066)
        for a in o['legalActions']:
            if o['state']['cards'].get(a['action'].get('cardId'),{}).get('name')=='Into the Roil':
                d=_candidate_description(_action(a,{}),o)
                self.assertEqual(d['reviewedEffects']['drawCards'],int(a['action'].get('declaredCostSlot')=='KICKED'))
    def test_channel_response_uses_frozen_native_parameters(self):
        o=self.state(5326);o['state']['currentStep']='PRECOMBAT_MAIN'
        b,_=bounded_observation(o,self.facts);w=enumerate_choices(b)
        self.assertIsNotNone(w)
        channels=[(i,x) for i,x in enumerate(w) if x['channel']=='action' and x['choice']['params'].get('targets')]
        self.assertTrue(channels)
        i,expected=channels[0]
        t=FakeTransport(completion(json.dumps({'option':i})))
        t.models['models'][0]['loaded_instances'][0]['config']['context_length']=131072
        r=BoundedPolicyFallback(config(max_prompt_bytes=122880,context_window=131072),t,facts=self.facts).choose_with_policy_context(o,[])
        self.assertEqual(r.metadata['boundedFallback']['mode'],'frozen-choices')
        self.assertEqual(r.params,expected['choice']['params'])
        self.assertNotIn('sourceId',r.params);self.assertNotIn('repeatCount',r.params)
    def test_fixed_sacrifices_enumerate_and_complex_costs_defer(self):
        o=self.state(3205);o['state']['currentStep']='PRECOMBAT_MAIN'
        a=next(a for a in o['legalActions'] if (a.get('additionalCostInfo') or {}).get('costType')=='SacrificePermanent')
        o['legalActions']=[a];w=enumerate_choices(o)
        self.assertIsNotNone(w)
        for choice in w:
            ids=choice['choice']['params']['sacrificedPermanents']
            self.assertEqual(len(ids),a['additionalCostInfo']['sacrificeCount'])
            self.assertTrue(set(ids)<=set(a['additionalCostInfo']['validSacrificeTargets']))
        a['additionalCostInfo']['sacrificeCountIsX']=True
        self.assertIsNone(enumerate_choices(o))
