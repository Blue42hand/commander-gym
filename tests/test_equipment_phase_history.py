"""Accepted attachment observations prevent within-phase protection oscillation."""
from copy import deepcopy
import gzip,json,unittest
from pathlib import Path
from types import SimpleNamespace
from commander_gym.heuristic_facts import CardFacts
from commander_gym.policy_action_bounds import bounded_observation,remember_equipment_hosts
from commander_gym.bounded_policy_fallback import BoundedPolicyFallback
from test_lmstudio import config,discovery,completion,FakeTransport

class EquipmentPhaseHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).with_name('fixtures')
        cls.rows={r['sequence']:r for r in json.load(gzip.open(root/'cycle-19-late-regressions.json.gz','rt'))['rows']}
        cls.facts=CardFacts(json.load(gzip.open(root/'cycle-19-protection-facts.json.gz','rt')))

    def test_authentic_engine_transfers_stop_returning_to_visited_hosts(self):
        owner=SimpleNamespace()
        for seq in [12527,12548,12569,12590]:
            with self.subTest(sequence=seq):
                o=self.rows[seq]['observation'];h=remember_equipment_hosts(owner,o,self.facts)
                v,r=bounded_observation(o,self.facts,equipment_history=h)
                original=self.rows[seq]['recordedChoice'];target=original['params']['targets'][0]
                a=next((a for a in v['legalActions'] if a['actionId']==original['actionId']),None)
                if seq==12527:self.assertIn(target,a['validTargets'])
                else:
                    self.assertTrue(a is None or target not in a['validTargets'])
                    self.assertTrue(any(x['rule']=='avoid-repeating-protection-host-in-same-phase' for x in r))

    def test_repeated_input_never_assumes_a_model_transfer_succeeded(self):
        owner=SimpleNamespace();o=self.rows[12527]['observation']
        for _ in range(3):
            h=remember_equipment_hosts(owner,o,self.facts)
            self.assertEqual(h['hosts']['e392'],['e300'])
        self.assertEqual(remember_equipment_hosts(owner,self.rows[12548]['observation'],self.facts)['hosts']['e392'],['e300','e331'])

    def test_new_phase_or_turn_resets_prior_hosts(self):
        for mode in ['phase','turn']:
            owner=SimpleNamespace();remember_equipment_hosts(owner,self.rows[12527]['observation'],self.facts)
            o=deepcopy(self.rows[12548]['observation'])
            if mode=='phase':o['state']['currentStep']='POSTCOMBAT_MAIN'
            else:o['state']['turnNumber']+=1
            h=remember_equipment_hosts(owner,o,self.facts)
            self.assertEqual(h['hosts']['e392'],['e331'])

    def test_deck_override_and_meaningful_haste_remain_available(self):
        for mode in ['override','haste','tapped-host','unknown-sickness','wrong-history']:
            owner=SimpleNamespace();remember_equipment_hosts(owner,self.rows[12527]['observation'],self.facts)
            o=deepcopy(self.rows[12548]['observation']);h=remember_equipment_hosts(owner,o,self.facts);kwargs={}
            if mode=='override':kwargs['reaction_timing_exceptions']={'Swiftfoot Boots'}
            elif mode=='haste':
                offer=next(a for a in o['legalActions'] if a['actionId']==self.rows[12548]['recordedChoice']['actionId'])
                fresh=next(t for t in offer['validTargets'] if t not in {'e300','e331'})
                o['state']['cards'][fresh]['hasSummoningSickness']=True
            elif mode=='tapped-host':o['state']['cards']['e331']['isTapped']=True
            elif mode=='unknown-sickness':o['state']['cards']['e300'].pop('hasSummoningSickness')
            else:h['turn']-=1
            v,_=bounded_observation(o,self.facts,equipment_history=h,**kwargs)
            original=self.rows[12548]['recordedChoice'];a=next(a for a in v['legalActions'] if a['actionId']==original['actionId'])
            self.assertIn(fresh if mode=='haste' else 'e300',a['validTargets'])

    def test_losing_equipment_haste_does_not_reopen_a_visited_host_loop(self):
        owner=SimpleNamespace();remember_equipment_hosts(owner,self.rows[12527]['observation'],self.facts)
        o=deepcopy(self.rows[12548]['observation']);h=remember_equipment_hosts(owner,o,self.facts)
        # Native sickness includes haste. A former host can become sick again
        # when the equipment leaves it; that does not justify cycling back.
        o['state']['cards']['e300']['hasSummoningSickness']=True
        v,_=bounded_observation(o,self.facts,equipment_history=h)
        original=self.rows[12548]['recordedChoice'];a=next((a for a in v['legalActions'] if a['actionId']==original['actionId']),None)
        self.assertTrue(a is None or 'e300' not in a['validTargets'])

    def test_malformed_optional_history_retains_model_discretion(self):
        owner=SimpleNamespace();o=self.rows[12548]['observation']
        base=remember_equipment_hosts(owner,o,self.facts)
        for mapping in [None,[],{'e392':None},{'e392':[[]]}]:
            h={**base,'hosts':mapping}
            v,_=bounded_observation(o,self.facts,equipment_history=h)
            original=self.rows[12548]['recordedChoice'];a=next(a for a in v['legalActions'] if a['actionId']==original['actionId'])
            self.assertIn('e300',a['validTargets'])

    def test_overlay_history_reaches_bounded_fallback_without_entering_prompt(self):
        owner=SimpleNamespace();h=remember_equipment_hosts(owner,self.rows[12527]['observation'],self.facts)
        o=deepcopy(self.rows[12548]['observation']);original=self.rows[12548]['recordedChoice']
        # A reduced menu isolates the bridge from unrelated cast choices. Every
        # retained offer and the complete masked native card state are unchanged.
        o['legalActions']=[a for a in o['legalActions'] if a['kind']=='PassPriority' or a['actionId']==original['actionId']]
        models=discovery();models['models'][0]['loaded_instances'][0]['config']['context_length']=131072
        t=FakeTransport(completion('{"option":0}'),models=models)
        c=BoundedPolicyFallback(config(context_window=131072,max_prompt_bytes=122880),t,facts=self.facts).choose_with_policy_context(o,[{'metadata':{'policyLayers':{'status':'deferred'},'equipmentHistory':h}}])
        self.assertTrue(any(x['rule']=='avoid-repeating-protection-host-in-same-phase' for x in c.metadata['boundedFallback']['policyRestrictions']))
        request=next(body for path,body,_ in t.calls if path=='/v1/chat/completions')
        self.assertNotIn('equipmentHistory',json.dumps(request));self.assertNotIn('_equipment_phase_history',json.dumps(request))
