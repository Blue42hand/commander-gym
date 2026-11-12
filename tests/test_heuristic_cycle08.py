"""Recorded premature backup counter; wait for conditional counters to resolve."""
import gzip,json,unittest
from copy import deepcopy
from pathlib import Path
from commander_gym.heuristic_facts import CardFacts
from commander_gym.engine_deck_policy import EngineDeckPolicy
from commander_gym.policy_action_bounds import pending_counter_answers,bounded_observation

class CycleEightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).parent/'fixtures';cls.rows=json.load(gzip.open(root/'cycle-08-regressions.json.gz','rt'))
        cls.facts=CardFacts(json.load(gzip.open(root/'cycle-07-public-facts.json.gz','rt')))
    def state(self,n):return deepcopy(self.rows[str(n)]['observation'])
    def test_wait_for_own_conditional_counter_before_spending_two_more_cards(self):
        o=self.state(1382);before=deepcopy(o);self.assertEqual(pending_counter_answers(o,self.facts),{'e340'})
        b,r=bounded_observation(o,self.facts)
        for a in b['legalActions']:
            if 'COUNTERSPELL' in self.facts.get(o['state']['cards'].get(a['action'].get('cardId'),{}).get('name')).get('tags',[]):
                self.assertNotIn('e340',a.get('validTargets') or [])
        c=EngineDeckPolicy(self.facts,'talrand').try_choose(o).choice;self.assertIsNotNone(c)
        self.assertEqual(next(a['kind'] for a in o['legalActions'] if a['actionId']==c.action_id),'PassPriority');self.assertEqual(o,before)
    def test_first_counter_and_later_response_after_resolution_stay_available(self):
        o=self.state(1375);self.assertEqual(pending_counter_answers(o,self.facts),set())
        self.assertTrue(any('e340' in (a.get('validTargets') or []) for a in bounded_observation(o,self.facts)[0]['legalActions']))
        o=self.state(1382)
        for z in o['state']['zones']:
            if z.get('zoneId',{}).get('zoneType')=='Stack':z['cardIds'].remove('e154');z['size']=len(z['cardIds'])
        self.assertEqual(pending_counter_answers(o,self.facts),set())
        self.assertTrue(any('e340' in (a.get('validTargets') or []) for a in bounded_observation(o,self.facts)[0]['legalActions']))
    def test_counter_battle_restores_uncertainty_and_unknown_effects_defer(self):
        for change in ['opponent-response','unknown-card','ability','active-effect']:
            o=self.state(1382)
            if change=='opponent-response':
                o['state']['cards']['response']={'name':'Negate','controllerId':'policy-alela','targets':[{'type':'Spell','spellEntityId':'e154'}]}
                z=next(z for z in o['state']['zones'] if z.get('zoneId',{}).get('zoneType')=='Stack');z['cardIds'].append('response');z['size']=len(z['cardIds'])
            if change=='unknown-card':o['state']['cards']['e154']['name']='Unreviewed effect'
            if change=='ability':o['state']['cards']['e154']['abilityIdentity']={'abilityId':'unknown'}
            if change=='active-effect':o['state']['cards']['e154']['activeEffects']=['unknown']
            self.assertEqual(pending_counter_answers(o,self.facts),set(),change)
            self.assertTrue(any('e340' in (a.get('validTargets') or []) for a in bounded_observation(o,self.facts)[0]['legalActions']),change)
    def test_other_native_classified_counters_use_same_sequencing_without_name_lists(self):
        for name in ['Spell Stutter','Force of Negation']:
            o=self.state(1382);o['state']['cards']['e154']['name']=name
            self.assertEqual(pending_counter_answers(o,self.facts),{'e340'},name)
    def test_conditional_discard_keeps_nonlands_and_discards_one_native_basic_option(self):
        from commander_gym.selection_contracts import conditional_selection_valid
        o=self.state(2838);before=deepcopy(o);c=EngineDeckPolicy(self.facts,'talrand').try_choose(o).choice
        self.assertIsNotNone(c);self.assertEqual(len(c.response['selectedCards']),1)
        self.assertIn(c.response['selectedCards'][0],{'e146','e193'})
        self.assertTrue(conditional_selection_valid(c.response['selectedCards'],o['pendingDecision']))
        self.assertEqual(o,before)
    def test_generic_frozen_choices_never_offer_the_invalid_single_nonmatching_card(self):
        from commander_gym.bounded_policy_fallback import enumerate_choices
        from commander_gym.selection_contracts import conditional_selection_valid
        o=self.state(2838);choices=enumerate_choices(o);self.assertIsNotNone(choices)
        self.assertEqual(len(choices),17)
        self.assertFalse(any(c['response']['selectedCards']==['e150'] for c in choices))
        self.assertTrue(any(c['response']['selectedCards']==['e146'] for c in choices))
        self.assertTrue(any(set(c['response']['selectedCards'])=={'e150','e179'} for c in choices))
        self.assertTrue(all(conditional_selection_valid(c['response']['selectedCards'],o['pendingDecision']) for c in choices))
    def test_invalid_conditional_minimum_is_rejected_before_native_execution(self):
        from commander_gym.bounded_policy_fallback import validate_selection_bounds
        from commander_gym.openai_responses_pilot import OpenAIResponsesPilot,OpenAIResponsesPilotError
        from commander_gym.pilot import PilotContractError
        o=self.state(2838);wire={'channel':'decision','response':{'type':'CardsSelectedResponse','selectedCards':['e150']}}
        with self.assertRaises(PilotContractError):validate_selection_bounds(wire,o)
        from test_openai_responses_pilot import FakeClient,FakeResponse
        p=OpenAIResponsesPilot(client=FakeClient(FakeResponse('{}')),model='contract-test')
        with self.assertRaises(OpenAIResponsesPilotError):p._choice_from_response({'status':'completed','output_text':json.dumps(wire)},o,retry_count=0)
        for selected in [['e146'],['e150','e179'],['e146','e150']]:
            wire['response']['selectedCards']=selected;validate_selection_bounds(wire,o)
            p._choice_from_response({'status':'completed','output_text':json.dumps(wire)},o,retry_count=0)
    def test_multiple_native_alternatives_follow_native_any_unmet_condition_semantics(self):
        from commander_gym.selection_contracts import conditional_selection_valid,conditional_minima
        p={'conditionalMinimums':[{'requiredSelections':3,'minimumSelections':1,'requiredMatches':1,'matchingOptions':['artifact']},
                                  {'requiredSelections':3,'minimumSelections':1,'requiredMatches':1,'matchingOptions':['land']}]}
        self.assertTrue(conditional_selection_valid(['artifact'],p));self.assertTrue(conditional_selection_valid(['land'],p))
        self.assertFalse(conditional_selection_valid(['other'],p));self.assertTrue(conditional_selection_valid(['a','b','c'],p))
        for raw in [False,{},[{'requiredSelections':True,'minimumSelections':1,'matchingOptions':[]}]]:
            with self.assertRaises(ValueError):conditional_minima({'conditionalMinimums':raw})
    def test_unknown_discard_semantics_defer_but_keep_structural_choices_valid(self):
        from commander_gym.bounded_policy_fallback import enumerate_choices
        o=self.state(2838);o['pendingDecision']['context']['sourceName']='Unknown selection effect'
        self.assertIsNone(EngineDeckPolicy(self.facts,'talrand').try_choose(o).choice)
        self.assertEqual(len(enumerate_choices(o)),17)
