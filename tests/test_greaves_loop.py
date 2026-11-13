"""Recorded Greaves oscillation is bounded without removing useful haste."""
from copy import deepcopy
import gzip,json,unittest
from pathlib import Path
from types import SimpleNamespace
from commander_gym.heuristic_facts import CardFacts
from commander_gym.policy_action_bounds import bounded_observation,remember_equipment_hosts

class GreavesLoopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).with_name('fixtures')
        cls.rows=json.load(gzip.open(root/'cycle-31-equipment-loop.json.gz','rt'))['rows']
        cls.facts=CardFacts(json.load(gzip.open(root/'cycle-31-native-facts.json.gz','rt')))

    def offer(self,o,v):
        original=next(a for a in o['legalActions'] if a.get('action',{}).get('sourceId')=='e184' and a['kind']=='ActivateAbility')
        return next((a for a in v['legalActions'] if a['actionId']==original['actionId']),None)

    def test_all_recorded_bad_transfers_are_excluded(self):
        owner=SimpleNamespace()
        for row in self.rows:
            o=row['observation'];before=deepcopy(o)
            self.assertTrue(row['evidence']['nativeMaskedMatch']);self.assertTrue(row['evidence']['matchedNativePassAccepted'])
            h=remember_equipment_hosts(owner,o,self.facts)
            v,r=bounded_observation(o,self.facts,equipment_history=h)
            a=self.offer(o,v);target=row['choice']['params']['targets'][0]
            self.assertTrue(a is None or target not in a['validTargets'],row['sequence'])
            self.assertTrue(r);self.assertEqual(o,before)

    def test_fresh_hullbreaker_haste_target_is_retained(self):
        o=self.rows[0]['observation'];h=remember_equipment_hosts(SimpleNamespace(),o,self.facts)
        v,_=bounded_observation(o,self.facts,equipment_history=h)
        self.assertTrue(o['state']['cards']['e174']['hasSummoningSickness'])
        self.assertIn('e174',self.offer(o,v)['validTargets'])
        self.assertNotIn('e470',self.offer(o,v)['validTargets'])

    def test_postcombat_tapped_host_history_prevents_repeated_transfers(self):
        owner=SimpleNamespace()
        for index,row in enumerate(self.rows[:3]):
            o=deepcopy(row['observation']);o['state']['currentStep']='POSTCOMBAT_MAIN'
            o['state']['cards']['e100']['isTapped']=True
            h=remember_equipment_hosts(owner,o,self.facts)
            v,r=bounded_observation(o,self.facts,equipment_history=h)
            a=self.offer(o,v);target=row['choice']['params']['targets'][0]
            if index==0:self.assertIn(target,a['validTargets'])
            else:
                self.assertTrue(a is None or target not in a['validTargets'])
                self.assertTrue(any(x['rule']=='avoid-repeating-protection-host-in-same-phase' for x in r))

    def test_observed_tap_change_resets_history(self):
        owner=SimpleNamespace()
        for row in self.rows[:2]:h=remember_equipment_hosts(owner,row['observation'],self.facts)
        self.assertEqual(set(h['hosts']['e184']),{'e100','e470'})
        o=deepcopy(self.rows[1]['observation']);o['state']['cards']['e100']['isTapped']=True
        h=remember_equipment_hosts(owner,o,self.facts)
        self.assertEqual(h['hosts']['e184'],['e470'])
        v,_=bounded_observation(o,self.facts,equipment_history=h)
        self.assertIn('e100',self.offer(o,v)['validTargets'])

    def test_deck_override_preserves_equipment_discretion(self):
        owner=SimpleNamespace()
        for row in self.rows[:2]:h=remember_equipment_hosts(owner,row['observation'],self.facts)
        o=self.rows[1]['observation']
        v,_=bounded_observation(o,self.facts,equipment_history=h,reaction_timing_exceptions={'Lightning Greaves'})
        self.assertIn('e100',self.offer(o,v)['validTargets'])

    def test_deck_policy_never_returns_the_recorded_bad_equips(self):
        from commander_gym.engine_deck_policy import EngineDeckPolicy
        policy=EngineDeckPolicy(self.facts,'talrand')
        for row in self.rows:
            result=policy.try_choose(row['observation'])
            c=result.choice
            self.assertTrue(c is None or c.action_id!=row['choice']['actionId']
                            or dict(c.params)!=row['choice']['params'],row['sequence'])
