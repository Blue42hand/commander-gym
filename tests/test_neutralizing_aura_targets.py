"""Known neutralizers reach the shared enemy/threat policy despite UNKNOWN polarity."""
from copy import deepcopy
import gzip,json,unittest
from pathlib import Path
from commander_gym.heuristic_facts import CardFacts
from commander_gym.policy_action_bounds import bounded_observation

class NeutralizingAuraTargetsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).with_name('fixtures')
        cls.row=next(r for r in json.load(gzip.open(root/'cycle-19-equipment.json.gz','rt'))['rows'] if r['sequence']==10350)
        cls.facts=CardFacts(json.load(gzip.open(root/'cycle-19-protection-facts.json.gz','rt')))

    def test_actual_ordinary_token_is_excluded_in_favor_of_live_engines(self):
        o=self.row['observation'];before=deepcopy(o);v,r=bounded_observation(o,self.facts)
        a=next(a for a in v['legalActions'] if a['actionId']==self.row['recordedChoice']['actionId'])
        self.assertNotIn('e438',a['validTargets'])
        self.assertNotIn('e235',a['validTargets'])
        self.assertTrue(set(a['validTargets']) & {'e0','e100','e300'})
        self.assertTrue(any(x['rule']=='opponent-removal-targets' for x in r))
        self.assertTrue(any(x['rule']=='reviewed-removal-threat-shortlist' for x in r));self.assertEqual(o,before)

    def test_explicit_deck_self_target_override_preserves_full_native_domain(self):
        o=self.row['observation'];v,_=bounded_observation(o,self.facts,allow_self_targets={'Darksteel Mutation'})
        original=next(a for a in o['legalActions'] if a['actionId']==self.row['recordedChoice']['actionId'])
        a=next(a for a in v['legalActions'] if a['actionId']==original['actionId'])
        self.assertEqual(a['validTargets'],original['validTargets'])

    def test_unreviewed_aura_remains_for_model(self):
        o=deepcopy(self.row['observation']);card=next(a['action']['cardId'] for a in o['legalActions'] if a['actionId']==self.row['recordedChoice']['actionId'])
        o['state']['cards'][card]['name']='Unreviewed mixed Aura'
        v,_=bounded_observation(o,self.facts)
        a=next(a for a in v['legalActions'] if a['actionId']==self.row['recordedChoice']['actionId'])
        self.assertIn('e438',a['validTargets']);self.assertIn('e235',a['validTargets'])

    def test_actual_witness_protection_retains_reasonable_target_and_native_effect_hint(self):
        from commander_gym.bounded_policy_fallback import _candidate_description
        root=Path(__file__).with_name('fixtures')
        r=json.load(gzip.open(root/'cycle-19-witness-protection.json.gz','rt'))['row']
        self.assertEqual(r['nativeBaselineStatus'],'accepted')
        o=r['observation'];v,rules=bounded_observation(o,self.facts)
        a=next(a for a in v['legalActions'] if a['actionId']==r['baseline']['actionId'])
        self.assertIn('e116',a['validTargets'])
        self.assertEqual(o['state']['cards']['e100']['zone']['zoneType'],'Command')
        self.assertNotIn('e100',a['validTargets'])
        self.assertTrue(all(o['state']['cards'][t]['controllerId']!=o['agentToAct'] for t in a['validTargets']))
        self.assertTrue(any(x['rule']=='reviewed-removal-threat-shortlist' for x in rules))
        description=_candidate_description({'channel':'action','choice':{'semanticId':a['semanticId'],'params':{'targets':[a['validTargets'][0]]}}},v,self.facts)
        self.assertEqual(description['nativeEffectFacts'],{'attachedCreatureLosesAllAbilities':True,'otherEffectsRemainInPrintedRules':True})
