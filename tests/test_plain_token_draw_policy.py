"""Known plain token bodies do not turn a free draw into an uncertain decision."""
from copy import deepcopy
import unittest,json,gzip
from pathlib import Path
from commander_gym.engine_deck_policy import EngineDeckPolicy
from commander_gym.heuristic_facts import CardFacts

class PlainTokenDrawPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).with_name('fixtures')
        cls.fixture=json.load(gzip.open(root/'cycle-18-free-draw.json.gz','rt'))
        cls.facts=CardFacts(json.load(gzip.open(root/'cycle-18-entry-facts.json.gz','rt')))

    def test_all_eight_native_accepted_draws_are_now_identical_local_choices(self):
        self.assertEqual(len(self.fixture['rows']),8)
        for row in self.fixture['rows']:
            with self.subTest(sequence=row['sequence']):
                o=deepcopy(row['observation']);before=deepcopy(o)
                self.assertEqual(row['nativeBaselineStatus'],'accepted')
                result=EngineDeckPolicy(self.facts,row['deck'].lower()).try_choose(o)
                self.assertIsNotNone(result.choice)
                self.assertEqual(dict(result.choice.response),row['baseline']['response'])
                self.assertTrue(result.choice.response['choice']);self.assertEqual(o,before)

    def test_unknown_token_abilities_or_modifiers_keep_model_control(self):
        for changes in [{'oracleText':'Whenever you draw a card, ...'}, {'abilityFlags':['UNKNOWN']},
                        {'keywords':['UNKNOWN']}, {'isFaceDown':True}, {'isDoubleFaced':True},
                        {'cardFaces':[{}]}, {'copyOf':'unknown-source'}, {'activeEffects':['unknown']}]:
            with self.subTest(changes=changes):
                row=self.fixture['rows'][0];o=deepcopy(row['observation'])
                token=next(c for c in o['state']['cards'].values() if c.get('isToken') and not self.facts.get(c.get('name')))
                token.update(changes)
                self.assertIsNone(EngineDeckPolicy(self.facts,'sythis').try_choose(o).choice)

    def test_draw_sensitive_permanents_and_near_empty_library_remain_uncertain(self):
        from test_heuristic_policy import fact,zone
        for mode in ['punishment','library','player-effect']:
            with self.subTest(mode=mode):
                row=self.fixture['rows'][0];o=deepcopy(row['observation']);f=self.facts
                if mode=='punishment':
                    document=deepcopy(f._document);document['cards']['Draw Punisher']=fact(types=['ENCHANTMENT'],drawSensitive=True)
                    f=CardFacts(document);zone(o,'Battlefield','policy-talrand',punisher={'name':'Draw Punisher','controllerId':'policy-talrand'})
                elif mode=='library':next(p for p in o['state']['players'] if p['playerId']==o['agentToAct'])['librarySize']=1
                else:o['state']['players'][0]['activeEffects']=['unknown']
                self.assertIsNone(EngineDeckPolicy(f,'sythis').try_choose(o).choice)
