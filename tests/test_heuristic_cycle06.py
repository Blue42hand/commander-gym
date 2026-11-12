"""Recorded Treasure payment and low-impact counterspell mistakes."""
import gzip,json,unittest
from copy import deepcopy
from pathlib import Path
from commander_gym.heuristic_facts import CardFacts
from commander_gym.engine_deck_policy import EngineDeckPolicy
from commander_gym.policy_action_bounds import bounded_observation

class CycleSixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).parent/'fixtures'
        cls.rows=json.load(gzip.open(root/'cycle-06-regressions.json.gz','rt'))
        cls.facts=CardFacts(json.load(gzip.open(root/'cycle-04-public-facts.json.gz','rt')))
    def state(self,n):return deepcopy(self.rows[str(n)]['observation'])
    def test_complete_mixed_color_cost_from_native_treasures(self):
        for seq,wanted in [(2077,['e400','e401']),(2091,['e401'])]:
            o=self.state(seq);before=deepcopy(o);c=EngineDeckPolicy(self.facts,'alela').try_choose(o).choice
            self.assertEqual(c.response['selectedSources'],wanted);self.assertFalse(c.response['autoPay']);self.assertFalse(c.response['declined'])
            self.assertEqual(o,before)
    def test_wrong_color_pool_is_not_payment_and_declines_locally(self):
        o=self.state(2105);c=EngineDeckPolicy(self.facts,'alela').try_choose(o).choice
        self.assertTrue(c.response['declined']);self.assertFalse(c.response['autoPay'])
    def test_floating_colors_reduce_sources_and_save_sacrifices(self):
        o=self.state(2077);player=next(p for p in o['state']['players'] if p['playerId']==o['agentToAct']);player['manaPool']['white']=1
        c=EngineDeckPolicy(self.facts,'alela').try_choose(o).choice;self.assertEqual(len(c.response['selectedSources']),1)
        player['manaPool']['blue']=1;c=EngineDeckPolicy(self.facts,'alela').try_choose(o).choice
        self.assertEqual(c.response['selectedSources'],[]);self.assertFalse(c.response['autoPay']);self.assertFalse(c.response['declined'])
        o=self.state(2077);rows=o['pendingDecision']['availableSources'];rows[0]['requiresSacrifice']=False;rows[0]['manaAmount']=2;rows[0]['producesColors']=['WHITE']
        o['pendingDecision']['requiredCost']='{2}';c=EngineDeckPolicy(self.facts,'alela').try_choose(o).choice
        self.assertEqual(c.response['selectedSources'],['e400'])
    def test_unknown_payment_mechanics_still_defer(self):
        for change in ['hybrid','batch','otherCost','restricted','unknown','large','phase']:
            o=self.state(2077);p=o['pendingDecision'];rows=p['availableSources']
            if change=='hybrid':p['requiredCost']='{W/U}{U}'
            if change=='batch':rows[0]['manaAmount']=2
            if change=='otherCost':rows[0]['requiresTappingAnotherPermanent']=True
            if change=='restricted':next(p for p in o['state']['players'] if p['playerId']==o['agentToAct'])['manaPool']['restrictedMana']=[{'amount':1}]
            if change=='unknown':rows[0].pop('producesColors')
            if change=='large':p['availableSources']=rows*5
            if change=='phase':p['context']['phase']='UNREVIEWED'
            self.assertIsNone(EngineDeckPolicy(self.facts,'alela').try_choose(o).choice,change)
    def test_treasure_refunding_counter_is_saved_for_material_threat(self):
        o=self.state(2035);before=deepcopy(o);c=EngineDeckPolicy(self.facts,'talrand').try_choose(o).choice
        self.assertEqual(next(a['kind'] for a in o['legalActions'] if a['actionId']==c.action_id),'PassPriority');self.assertEqual(o,before)
        for name in ['Sol Ring','Unknown threat','Time Warp']:
            o=self.state(2035);o['state']['cards']['e389']['name']=name;b,_=bounded_observation(o,self.facts)
            self.assertTrue(any('e389' in (a.get('validTargets') or []) for a in b['legalActions']),name)
        b,_=bounded_observation(self.state(2035),self.facts,counter_target_exceptions={"An Offer You Can't Refuse"})
        self.assertTrue(any('e389' in (a.get('validTargets') or []) for a in b['legalActions']))

    def test_colored_mana_matching_handles_large_identical_pools_and_small_exact_cases(self):
        from commander_gym.heuristic_policy import MaskedHeuristicSubsystem,mana_cost
        from itertools import product
        from collections import Counter
        payable=MaskedHeuristicSubsystem._payable
        self.assertFalse(payable(mana_cost('{W}{W}{W}{W}{W}{W}{W}{W}{U}'),[{'W'}]*32))
        self.assertTrue(payable(mana_cost('{W}{W}{W}{W}{W}{W}{W}{W}{U}'),[{'W'}]*31+[{'U'}]))
        for rows in product([{'W'},{'U'},{'W','U'},{'C'}],repeat=3):
            for white,blue,generic in product(range(3),repeat=3):
                cost=(white+blue+generic,Counter({'W':white,'U':blue}))
                expected=False
                if cost[0]<=len(rows):
                    for assignment in product(*rows):
                        pool=Counter(assignment)
                        if pool['W']>=white and pool['U']>=blue:expected=True;break
                self.assertEqual(payable(cost,rows),expected,(rows,cost))
    def test_recorded_commander_does_not_attack_into_overwhelming_blockers(self):
        o=self.state(6368);before=deepcopy(o);c=EngineDeckPolicy(self.facts,'talrand').try_choose(o).choice
        self.assertEqual(c.params,{'attackers':{}});self.assertEqual(o,before)
    def test_unrelated_keywords_do_not_hide_safe_attack_lanes(self):
        o=self.state(5199);c=EngineDeckPolicy(self.facts,'alela').try_choose(o).choice
        self.assertEqual(set(c.params['attackers']),{'e320'})
        self.assertIn(c.params['attackers']['e320'],{'policy-talrand','policy-sythis'})
        o=self.state(6032);c=EngineDeckPolicy(self.facts,'sythis').try_choose(o).choice
        self.assertEqual(c.params['attackers'],{'e251':'policy-talrand','e200':'policy-talrand'})
    def test_double_strike_retaliation_is_counted_and_unknown_shadow_still_defers(self):
        for keyword,expected in [('FIRST_STRIKE',{'e320':'policy-talrand'}),('DOUBLE_STRIKE',{}),('SHADOW',None)]:
            o=self.state(5199);o['legalActions'][0]['validAttackTargets']=['policy-talrand']
            o['state']['cards']['e100']['keywords']=[keyword]
            c=EngineDeckPolicy(self.facts,'alela').try_choose(o).choice
            if expected is None:self.assertIsNone(c)
            else:self.assertEqual(c.params['attackers'],expected)
    def test_native_modal_preview_uses_all_four_beneficial_modes(self):
        from commander_gym.modal_policies import modal_plan
        o=self.state(6655);before=deepcopy(o);a=next(a for a in o['legalActions'] if a['kind']=='CastSpellModal')
        plan=modal_plan(o,a,self.facts);by_label={m['description']:m['target'] for m in plan['modes']}
        self.assertEqual(set(by_label),{'Destroy target artifact','Destroy target creature','Destroy target enchantment','Destroy target land'})
        self.assertEqual(by_label['Destroy target enchantment'],'e235')  # Frees Lathril, rather than a mana dork.
        self.assertEqual(by_label['Destroy target land'],'e227')  # Serra's Sanctum.
        self.assertEqual(len(set(by_label.values())),4);self.assertEqual(o,before)
        p=EngineDeckPolicy(self.facts,'lathril');p.try_choose(o)
        for seq in [6662,6669]:
            state=self.state(seq);c=p.try_choose(state).choice
            self.assertNotEqual(state['pendingDecision']['options'][c.response['optionIndex']],'Done')
        done=self.state(6669);done['pendingDecision']['options']=['Done']
        self.assertEqual(p.try_choose(done).choice.response['optionIndex'],0)
        target=self.state(6676);self.assertEqual(p.try_choose(target).choice.response['selectedTargets'],{'0':['e200']})
    def test_modal_preview_never_spends_a_mode_on_own_permanent_or_unknown_cost(self):
        from commander_gym.modal_policies import modal_plan
        o=self.state(6655);a=next(a for a in o['legalActions'] if a['kind']=='CastSpellModal')
        a['modalEnumeration']['modes'][0]['targetRequirements'][0]['validTargets']=['e64']
        plan=modal_plan(o,a,self.facts);self.assertFalse(any(m['description']=='Destroy target artifact' for m in plan['modes']))
        a['modalEnumeration']['additionalManaCostPerExtraMode']='{1}';self.assertIsNone(modal_plan(o,a,self.facts))
        self.assertIsNone(EngineDeckPolicy(self.facts,'lathril').try_choose(self.state(6662)).choice)
    def test_changed_modal_target_domain_returns_to_model(self):
        p=EngineDeckPolicy(self.facts,'lathril');p.try_choose(self.state(6655));o=self.state(6676)
        o['pendingDecision']['legalTargets']['0'].remove('e200')
        self.assertIsNone(p.try_choose(o).choice)
