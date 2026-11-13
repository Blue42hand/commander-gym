"""Return payments are native contracts; the policy ranks complete actions."""
from copy import deepcopy
from pathlib import Path
import gzip,json,unittest
from commander_gym.bounded_policy_fallback import (
    BoundedPolicyFallback,enumerate_choices,_unproductive_return_untap_choice,MAX_CHOICES)
from commander_gym.heuristic_facts import CardFacts
from test_lmstudio import config,FakeTransport,completion
from test_engine_deck_policy import expand

class BounceChoicePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).with_name('fixtures')
        cls.facts=CardFacts(json.load(gzip.open(root/'cycle-22-resource-facts.json.gz','rt')))
        cls.row=next(r for r in json.load(gzip.open(root/'cycle-23-bounce-windows.json.gz','rt'))['rows'] if r['sequence']==6857)

    def input(self):
        from commander_gym.policy_action_bounds import bounded_observation
        return bounded_observation(self.row['observation'],self.facts)[0]

    def test_actual_native_return_menu_is_completely_enumerated(self):
        o=self.input();before=deepcopy(o);wires=enumerate_choices(o)
        self.assertIsNotNone(wires)
        a=next(a for a in o['legalActions'] if a['actionId']==self.row['choice']['actionId'])
        paid=[w['choice']['params'] for w in wires if w['choice']['semanticId']==a['semanticId']]
        expected=[{'targets':[t],'bouncedPermanents':[b]} for t in a['validTargets'] for b in a['additionalCostInfo']['validBounceTargets']]
        self.assertEqual({json.dumps(p,sort_keys=True) for p in paid},{json.dumps(p,sort_keys=True) for p in expected})
        self.assertEqual(o,before)

    def test_actual_reactive_dead_target_is_pruned_from_complete_model_choices(self):
        o=self.row['observation'];before=deepcopy(o)
        t=FakeTransport(completion(json.dumps({'option':0})))
        t.models['models'][0]['loaded_instances'][0]['config']['context_length']=131072
        c=BoundedPolicyFallback(config(context_window=131072,max_prompt_bytes=122880),t,facts=self.facts).choose_with_policy_context(o,[])
        self.assertEqual(c.metadata['boundedFallback']['mode'],'frozen-choices')
        self.assertIn('return-untap-needs-own-surviving-target',[r['rule'] for r in c.metadata['boundedFallback']['policyRestrictions']])
        req=next(b for p,b,_ in t.calls if p=='/v1/chat/completions')
        view=expand(json.loads(req['messages'][1]['content']))
        paid=[c for c in view['choices'] if c['parameters'].get('bouncedPermanents')]
        self.assertTrue(paid)
        self.assertTrue(all(c['parameters']['targets']!=c['parameters']['bouncedPermanents'] for c in paid))
        self.assertTrue(all(t['relationship']!='opponent' for c in paid for t in c['targetFacts']))
        self.assertEqual(o,before)

    def test_return_target_policy_keeps_unknown_effects_and_deck_rebuy_override(self):
        o=self.input();a=next(a for a in o['legalActions'] if a['actionId']==7)
        wire={'channel':'action','choice':{'semanticId':a['semanticId'],'params':self.row['choice']['params']}}
        self.assertTrue(_unproductive_return_untap_choice(wire,o,self.facts))
        self.assertFalse(_unproductive_return_untap_choice(wire,o,self.facts,{'Wirewood Symbiote'}))
        for mode in ['unknown','modifier','complex','payment-retry','different-target','no-pass']:
            with self.subTest(mode=mode):
                v=deepcopy(o);w=deepcopy(wire);b=next(b for b in v['legalActions'] if b['actionId']==7)
                if mode=='unknown':b['action']['abilityId']='unknown-ability'
                elif mode=='modifier':v['state']['cards'][b['action']['sourceId']]['activeEffects']=['unknown']
                elif mode=='complex':b['additionalCostInfo']['alsoRequired']=[{'costType':'Unknown'}]
                elif mode=='payment-retry':v['nativePaymentError']='retry'
                elif mode=='different-target':w['choice']['params']['targets']=['e98']
                else:v['legalActions']=[b]
                self.assertFalse(_unproductive_return_untap_choice(w,v,self.facts))

    def test_large_and_complex_return_menus_defer_without_truncation(self):
        for mode in ['large','compound','prebound','unknown-field','duplicates','mana']:
            with self.subTest(mode=mode):
                o=self.input();a=next(a for a in o['legalActions'] if a['actionId']==7)
                if mode=='large':a['additionalCostInfo']['validBounceTargets']=[f'e{i}' for i in range(MAX_CHOICES+1)]
                elif mode=='compound':a['additionalCostInfo']['alsoRequired']=[{'costType':'Unknown'}]
                elif mode=='prebound':a['action']['costPayment']={'bouncedPermanents':['e71']}
                elif mode=='unknown-field':a['parameterSpec']['allowedFields']['unknownCost']='INTEGER'
                elif mode=='mana':a['manaCostString']='{1}'
                else:a['additionalCostInfo']['validBounceTargets']=['e71','e71']
                self.assertIsNone(enumerate_choices(o))

    def test_opponent_untaps_need_an_explicit_rebuy_exception(self):
        o=self.input();a=next(a for a in o['legalActions'] if a['actionId']==7)
        wire={'channel':'action','choice':{'semanticId':a['semanticId'],'params':{'targets':['e200'],'bouncedPermanents':['e71']}}}
        self.assertTrue(_unproductive_return_untap_choice(wire,o,self.facts))
        self.assertFalse(_unproductive_return_untap_choice(wire,o,self.facts,{'Wirewood Symbiote'}))
        o['state']['cards']['e200'].pop('controllerId')
        self.assertFalse(_unproductive_return_untap_choice(wire,o,self.facts))

    def test_both_native_accepted_reactive_mistakes_are_excluded(self):
        root=Path(__file__).with_name('fixtures')
        rows=json.load(gzip.open(root/'cycle-24-reactive-bounce.json.gz','rt'))['rows']
        self.assertEqual([r['sequence'] for r in rows],[6857,8768])
        for row in rows:
            with self.subTest(sequence=row['sequence']):
                self.assertTrue(row['evidence']['nativeMaskedMatch'])
                self.assertEqual(row['evidence']['nativeBaselineStatus'],'accepted')
                o=row['observation'];before=deepcopy(o)
                from commander_gym.policy_action_bounds import bounded_observation
                view,_=bounded_observation(o,self.facts)
                a=next(a for a in view['legalActions'] if a['actionId']==row['choice']['actionId'])
                wire={'channel':'action','choice':{'semanticId':a['semanticId'],'params':row['choice']['params']}}
                wires=enumerate_choices(view)
                self.assertIsNotNone(wires)
                self.assertIn(wire,wires)
                self.assertTrue(_unproductive_return_untap_choice(wire,view,self.facts))
                self.assertTrue(any(not _unproductive_return_untap_choice(w,view,self.facts) for w in wires))
                self.assertEqual(o,before)
