"""Native recorded counterexamples and shared competency contracts, cycle 01."""
import gzip,json,unittest
from pathlib import Path
from copy import deepcopy
from commander_gym.heuristic_facts import CardFacts
from commander_gym.engine_deck_policy import EngineDeckPolicy
from commander_gym.lathril_policy import LathrilDeckSubsystem
from commander_gym.default_policies import DefaultPolicySubsystem
from commander_gym.policy_action_bounds import bounded_observation
from test_heuristic_policy import observation,offer,zone,decision

class SharedCompetencyCycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).parent/'fixtures'
        with gzip.open(root/'cycle-01-regressions.json.gz','rt') as f:cls.rows=json.load(f)
        cls.facts=CardFacts.load(root/'competence-public-facts.json')
    def state(self,n):return deepcopy(self.rows[str(n)]['observation'])
    def policy(self,o):
        key=o['agentToAct'].removeprefix('policy-')
        return LathrilDeckSubsystem(self.facts) if key=='lathril' else EngineDeckPolicy(self.facts,key)
    def test_own_upkeep_development_waits_for_main_including_flashback(self):
        for n in [4116,4382,5628,5698,7602]:
            o=self.state(n);r=self.policy(o).try_choose(o)
            self.assertIsNotNone(r.choice,n)
            self.assertEqual(next(a['kind'] for a in o['legalActions'] if a['actionId']==r.choice.action_id),'PassPriority',n)
        o=self.state(5628);zone(o,'Stack','enemy',threat={'name':'Unknown threat'})
        b,_=bounded_observation(o,self.facts)
        self.assertTrue(any(a['kind']=='CastSpell' for a in b['legalActions']))
    def test_curiosity_does_not_consume_mana_without_own_body(self):
        o=self.state(1421);b,_=bounded_observation(o,self.facts)
        self.assertFalse(any(o['state']['cards'].get(a['action'].get('cardId'),{}).get('name')=='Curiosity' for a in b['legalActions']))
    def test_ponder_order_then_shuffle_uses_only_observed_reveal(self):
        o=self.state(1722);p=self.policy(o);r=p.try_choose(o)
        self.assertEqual(o['pendingDecision']['cardInfo'][r.choice.response['orderedObjects'][0]]['name'],'Island')
        r=p.try_choose(self.state(1729));self.assertFalse(r.choice.response['choice'])
        self.assertFalse(p.try_choose(self.state(1729)).choice.response['choice'])
        self.assertIsNone(self.policy(o).try_choose(self.state(1729)).choice)
    def test_scry_preserves_next_land_drop_and_discard_preserves_development(self):
        o=self.state(2835);r=self.policy(o).try_choose(o)
        self.assertNotIn('e173',r.choice.response['selectedCards'])
        o=self.state(4151);r=self.policy(o).try_choose(o)
        self.assertEqual(len(r.choice.response['selectedCards']),2)
        self.assertNotIn('e199',r.choice.response['selectedCards'])
        self.assertNotIn('e170',r.choice.response['selectedCards'])
    def test_equipment_and_free_search_no_longer_need_inference(self):
        o=self.state(6055);r=self.policy(o).try_choose(o)
        self.assertEqual(r.choice.params,{'targets':['e20']})
        o=self.state(6202);r=self.policy(o).try_choose(o)
        self.assertEqual(next(a['kind'] for a in o['legalActions'] if a['actionId']==r.choice.action_id),'PassPriority')
        o=self.state(6181);self.assertTrue(self.policy(o).try_choose(o).choice.response['choice'])
        o=self.state(6188);r=self.policy(o).try_choose(o)
        self.assertTrue(set(r.choice.response['selectedCards'])<=set(o['pendingDecision']['options']))
        o=self.state(7924);self.assertTrue(self.policy(o).try_choose(o).choice.response['choice'])
    def test_removal_frees_commander_and_does_not_fetter_basic_mana(self):
        o=self.state(6524);b,_=bounded_observation(o,self.facts)
        a=next(a for a in b['legalActions'] if o['state']['cards'].get(a['action'].get('cardId'),{}).get('name')=="Faith's Fetters")
        self.assertNotIn('e395',a['validTargets']);self.assertIn('e0',a['validTargets'])
        o=self.state(7763);r=self.policy(o).try_choose(o)
        self.assertEqual(r.choice.response['selectedTargets'],{'0':['e275']})
    def test_block_policy_preserves_commander_against_nonlethal_attack(self):
        o=self.state(7441);r=self.policy(o).try_choose(o)
        self.assertIsNotNone(r.choice);self.assertNotIn('e200',r.choice.params['blockers'])
    def test_native_ready_flags_and_complex_combat_remain_respected(self):
        a=offer('DeclareAttackers',1);a.update(validAttackers=['own'],validAttackTargets=['enemy'],parameterSpec={'allowedFields':{'attackers':'ENTITY_ID_MAP'}})
        o=observation(a);zone(o,'Battlefield','me',own={'name':'Elf','controllerId':'me','cardTypes':['CREATURE'],'power':4,'toughness':4,'hasSummoningSickness':True})
        p=DefaultPolicySubsystem(self.facts);self.assertEqual(p.try_choose(o).choice.params,{'attackers':{}})
        o['state']['cards']['own'].update(hasSummoningSickness=False,keywords=['TRAMPLE'])
        zone(o,'Battlefield','enemy',blocker={'name':'Guard','controllerId':'enemy','cardTypes':['CREATURE'],'power':2,'toughness':2})
        self.assertEqual(p._simple_combat(o,a).choice.params,{'attackers':{'own':'enemy'}})
        o['state']['cards']['own']['keywords']=['SHADOW']
        self.assertIsNone(p._simple_combat(o,a))
    def test_land_search_balances_needed_colors_without_hidden_order(self):
        o=decision('SelectCardsDecision','CardsSelectedResponse',{'selectedCards':'ENTITY_ID_ARRAY'},options=['f','p','s'],minSelections=0,maxSelections=3,context={'sourceName':'Land Tax'},cardInfo={i:{'name':n,'typeLine':'Basic Land'} for i,n in [('f','Forest'),('p','Plains'),('s','Swamp')]})
        r=DefaultPolicySubsystem(self.facts).try_choose(o)
        self.assertEqual(set(r.choice.response['selectedCards']),{'f','p','s'})

    def test_prompt_distinguishes_live_elves_from_printed_deck_counts(self):
        from commander_gym.bounded_policy_fallback import decision_context,_candidate_description,_action
        o=self.state(4382);view=decision_context(o,frozen=True,policy_context=None,facts=self.facts)
        own=view['battlefieldSummary']['policy-lathril']
        self.assertLess(own['tribeCounts']['Elf'],10)
        self.assertGreater(view['deckSubtypeCounts']['Elf'],own['tribeCounts']['Elf'])
        o=self.state(6055);a=next(a for a in o['legalActions'] if a.get('action',{}).get('abilityId')=='Skullclamp:2')
        described=_candidate_description(_action(a,{'targets':['e20']}),o)
        self.assertEqual(described['card']['name'],'Skullclamp')
        self.assertEqual(described['targetFacts'][0]['name'],'Wirewood Herald')
        self.assertEqual(described['targetFacts'][0]['relationship'],'self')
        self.assertTrue(described['nativeAffordable'])

    def test_transformed_creature_does_not_count_as_live_draw_engine(self):
        o=self.state(6524);engines=self.policy(o)._engines(o)
        self.assertNotIn('Setessan Champion',[c.get('name') for c in engines])
        self.assertIn("Sythis, Harvest's Hand",[c.get('name') for c in engines])
