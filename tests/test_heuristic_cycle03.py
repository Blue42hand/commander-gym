"""Native redundant-counter regression and conservative counter battle bounds."""
import gzip,json,unittest
from pathlib import Path
from copy import deepcopy
from commander_gym.heuristic_facts import CardFacts
from commander_gym.engine_deck_policy import EngineDeckPolicy
from commander_gym.policy_action_bounds import bounded_observation,pending_counter_answers
from commander_gym.bounded_policy_fallback import decision_context,compact_decision_context
from test_engine_deck_policy import expand

class CycleThreeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).parent/'fixtures'
        with gzip.open(root/'cycle-03-regressions.json.gz','rt') as f:cls.rows=json.load(f)
        cls.facts=CardFacts.load(root/'competence-public-facts.json')
    def state(self,n):return deepcopy(self.rows[str(n)]['observation'])
    def test_do_not_counter_same_spell_twice(self):
        o=self.state(3642);before=deepcopy(o)
        self.assertEqual(pending_counter_answers(o),{'e265'})
        b,r=bounded_observation(o,self.facts)
        self.assertTrue(any(x['rule']=='avoid-redundant-pending-counter' for x in r))
        self.assertFalse(any('e265' in (a.get('validTargets') or []) for a in b['legalActions'] if self.facts.get(o['state']['cards'].get(a['action'].get('cardId'),{}).get('name')).get('tags') and a['kind'].startswith('Cast')))
        choice=EngineDeckPolicy(self.facts,'talrand').try_choose(o).choice
        self.assertIsNotNone(choice)
        self.assertEqual(next(a['kind'] for a in o['legalActions'] if a['actionId']==choice.action_id),'PassPriority')
        self.assertEqual(o,before)
    def test_first_counter_stays_available(self):
        o=self.state(3635);b,r=bounded_observation(o,self.facts)
        self.assertEqual(pending_counter_answers(o),set())
        self.assertTrue(any('e265' in (a.get('validTargets') or []) for a in b['legalActions']))
    def test_opponent_countering_our_answer_restores_uncertainty(self):
        o=self.state(3642)
        o['state']['cards']['response']={'name':'Counterspell','controllerId':'policy-sythis','targets':[{'type':'Spell','spellEntityId':'e197'}]}
        z=next(z for z in o['state']['zones'] if z.get('zoneId',{}).get('zoneType')=='Stack');z['cardIds'].append('response');z['size']=len(z['cardIds'])
        self.assertEqual(pending_counter_answers(o),set())
        self.assertTrue(any('e265' in (a.get('validTargets') or []) for a in bounded_observation(o,self.facts)[0]['legalActions']))
    def test_conditional_and_opponent_answers_do_not_replace_model(self):
        for change in [{'name':'Mana Leak'},{'controllerId':'policy-alela'},{'abilityIdentity':{'abilityId':'unknown'}},{'activeEffects':['unknown']}]:
            o=self.state(3642);o['state']['cards']['e197'].update(change)
            self.assertEqual(pending_counter_answers(o),set(),change)
    def test_leave_greaves_protecting_commander_without_repeated_inference(self):
        for n in [5840,6022]:
            o=self.state(n);r=EngineDeckPolicy(self.facts,'alela').try_choose(o)
            self.assertIsNotNone(r.choice)
            self.assertEqual(next(a['kind'] for a in o['legalActions'] if a['actionId']==r.choice.action_id),'PassPriority')

    def test_compact_context_preserves_stack_targets_and_effects(self):
        o=self.state(3642);v=decision_context(o,frozen=True,policy_context=None,facts=self.facts)
        for view in [v,expand(compact_decision_context(v))]:
            self.assertEqual(view['cards']['e197']['targets'],[{'type':'Spell','spellEntityId':'e265'}])
            self.assertEqual(view['cards']['e197']['stackText'],'Counter target spell')
            self.assertEqual(view['cards']['e265']['targets'],[{'type':'Permanent','entityId':'e200'}])
