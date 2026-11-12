"""Actual native Boomerang priority windows and END step compatibility."""
import gzip,json,unittest
from copy import deepcopy
from pathlib import Path
from commander_gym.heuristic_facts import CardFacts
from commander_gym.engine_deck_policy import EngineDeckPolicy
from commander_gym.policy_action_bounds import bounded_observation
from test_heuristic_policy import offer,zone

class CycleSevenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).parent/'fixtures';cls.rows=json.load(gzip.open(root/'cycle-07-regressions.json.gz','rt'))
        cls.facts=CardFacts(json.load(gzip.open(root/'cycle-07-public-facts.json.gz','rt')))
    def state(self,n):return deepcopy(self.rows[str(n)]['observation'])
    def test_recorded_quiet_nonmain_removal_passes_without_inference(self):
        for seq in [2615,2643,2671]:
            o=self.state(seq);before=deepcopy(o);c=EngineDeckPolicy(self.facts,'talrand').try_choose(o).choice
            self.assertIsNotNone(c);self.assertEqual(next(a['kind'] for a in o['legalActions'] if a['actionId']==c.action_id),'PassPriority')
            self.assertEqual(o,before)
    def test_native_end_window_and_real_combat_keep_reactive_choices(self):
        for window in ['main','opponent-end','stack','combat','correction']:
            o=self.state(2587)
            if window=='opponent-end':o['state'].update(currentStep='END',activePlayerId='policy-alela')
            if window=='stack':o['state']['currentStep']='UPKEEP';zone(o,'Stack','policy-alela',threat={'name':'Unknown spell','controllerId':'policy-alela'})
            if window=='combat':o['state']['currentStep']='DECLARE_ATTACKERS';o['state']['combat']={'attackers':[{'creatureId':'e0'}]}
            if window=='correction':o['state']['currentStep']='BEGIN_COMBAT';o['nativePaymentError']='Needs a payment correction'
            b,_=bounded_observation(o,self.facts)
            self.assertTrue(any(o['state']['cards'].get(a['action'].get('cardId'),{}).get('name')=='Boomerang' for a in b['legalActions']),window)
    def test_generic_native_removal_tags_cover_an_unlisted_reaction(self):
        o=self.state(2615);source=next(a['action']['cardId'] for a in o['legalActions'] if a['kind']=='CastSpell')
        doc=self.facts.document();doc['cards']['Unlisted interaction']=doc['cards']['Boomerang'];facts=CardFacts(doc)
        o['state']['cards'][source]['name']='Unlisted interaction'
        c=EngineDeckPolicy(facts,'talrand').try_choose(o).choice
        self.assertEqual(next(a['kind'] for a in o['legalActions'] if a['actionId']==c.action_id),'PassPriority')
    def test_deck_timing_exception_and_uncovered_competing_action_defer(self):
        o=self.state(2615);p=EngineDeckPolicy(self.facts,'talrand');p.reaction_timing_exceptions=frozenset({'Boomerang'})
        self.assertIsNone(p.try_choose(o).choice)
        o=self.state(2615);o['legalActions'].append(offer('ActivateAbility',999,card='unknown'))
        self.assertIsNone(EngineDeckPolicy(self.facts,'talrand').try_choose(o).choice)
    def test_own_bounce_still_requires_rescue_or_deck_approval(self):
        o=self.state(2587);b,_=bounded_observation(o,self.facts);me=o['agentToAct']
        for a in b['legalActions']:
            if o['state']['cards'].get(a['action'].get('cardId'),{}).get('name')=='Boomerang':
                self.assertTrue(all(o['state']['cards'].get(t,{}).get('controllerId')!=me for t in a['validTargets']))
    def test_native_suppression_facts_remove_disabled_engine_value_and_ground_prompt(self):
        from commander_gym.policy_action_bounds import abilities_removed,removal_target_value
        from commander_gym.bounded_policy_fallback import decision_context
        o=self.state(2587);self.assertTrue(abilities_removed(o,o['state']['cards']['e200'],self.facts))
        self.assertLess(removal_target_value(o,'e200',self.facts),5)
        view=decision_context(o,frozen=True,policy_context=None,facts=self.facts)
        self.assertTrue(view['cards']['e200']['printedAbilitiesRemoved'])
        doc=self.facts.document();doc['cards']['Unlisted suppression aura']=doc['cards']['Utter Insignificance']
        facts=CardFacts(doc);o['state']['cards']['e323']['name']='Unlisted suppression aura'
        self.assertTrue(abilities_removed(o,o['state']['cards']['e200'],facts))
    def test_suppression_fact_is_typed_and_combat_locks_do_not_remove_draw_triggers(self):
        from commander_gym.policy_action_bounds import abilities_removed
        for bad in [1,'true',None]:
            doc=self.facts.document();doc['cards']['Utter Insignificance']['removesAttachedAbilities']=bad
            with self.assertRaises(ValueError):CardFacts(doc)
        for name in ['Arrest',"Faith's Fetters"]:
            o=self.state(2587);o['state']['cards']['e323']['name']=name
            self.assertFalse(abilities_removed(o,o['state']['cards']['e200'],self.facts),name)
    def test_payment_corrections_keep_draw_tagged_removal_available(self):
        o=self.state(2615);o['nativePaymentError']='Correction required'
        source=next(a['action']['cardId'] for a in o['legalActions'] if a['kind']=='CastSpell')
        o['state']['cards'][source]['name']='Into the Roil'
        b,_=bounded_observation(o,self.facts)
        self.assertTrue(any(a['action'].get('cardId')==source for a in b['legalActions']))
    def test_recorded_native_end_step_is_held_own_turn_and_available_on_opponent_turn(self):
        o=self.state(2727);c=EngineDeckPolicy(self.facts,'talrand').try_choose(o).choice
        self.assertEqual(next(a['kind'] for a in o['legalActions'] if a['actionId']==c.action_id),'PassPriority')
        o=self.state(3119);self.assertEqual(o['state']['currentStep'],'END')
        self.assertNotEqual(o['state']['activePlayerId'],o['agentToAct'])
        self.assertIsNone(EngineDeckPolicy(self.facts,'talrand').try_choose(o).choice)
        b,_=bounded_observation(o,self.facts)
        self.assertTrue(any(o['state']['cards'].get(a['action'].get('cardId'),{}).get('name')=='Boomerang' for a in b['legalActions']))

    def test_mixed_card_intent_does_not_bound_an_unknown_activated_ability(self):
        o=self.state(2615);source=next(a['action']['cardId'] for a in o['legalActions'] if a['kind']=='CastSpell')
        doc=self.facts.document();doc['cards']['Mixed artifact']={**doc['cards']['Boomerang'],'tags':['RAMP','REMOVAL']}
        facts=CardFacts(doc);o['state']['cards'][source]['name']='Mixed artifact'
        a=next(a for a in o['legalActions'] if a['kind']=='CastSpell');a['kind']='ActivateAbility'
        a['action']={**a['action'],'type':'ActivateAbility','sourceId':source};a['action'].pop('cardId')
        b,r=bounded_observation(o,facts)
        self.assertTrue(any(x['actionId']==a['actionId'] for x in b['legalActions']))
        self.assertFalse(any(x['rule']=='hold-reviewed-reaction-for-useful-window' and x.get('action')==a['semanticId'] for x in r))
