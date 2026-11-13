"""A native-generated bottom-library ordering gets a stable complete permutation."""
from copy import deepcopy
import gzip,json,unittest
from pathlib import Path
from commander_gym.default_policies import DefaultPolicySubsystem
from commander_gym.lathril_policy import LathrilDeckSubsystem
from commander_gym.heuristic_facts import CardFacts
from commander_gym.pilot_composition import SubsystemDecision

class BottomLibraryOrderPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).with_name('fixtures')
        cls.fixture=json.load(gzip.open(root/'cycle-20-order-explanation-failure.json.gz','rt'))
        cls.facts=CardFacts(json.load(gzip.open(root/'cycle-19-protection-facts.json.gz','rt')))

    def test_authentic_failed_state_now_uses_general_default_without_model(self):
        for factory in [DefaultPolicySubsystem,LathrilDeckSubsystem]:
            o=self.fixture['observation'];before=deepcopy(o);r=factory(self.facts).try_choose(o)
            self.assertEqual(r.choice.response['orderedObjects'],o['pendingDecision']['cards'])
            self.assertEqual(r.choice.response['decisionId'],o['pendingDecision']['id'])
            self.assertEqual(r.metadata['reason'],'default-native-bottom-library-order')
            self.assertNotIn('modelIo',r.choice.metadata);self.assertEqual(o,before)

    def test_native_bottom_purpose_does_not_depend_on_source_card_name(self):
        o=deepcopy(self.fixture['observation']);o['pendingDecision']['context']['sourceName']='Another native bottom-placement effect'
        self.assertIsNotNone(DefaultPolicySubsystem(self.facts).try_choose(o).choice)

    def test_top_order_unknown_purpose_near_deckout_and_cancellation_defer(self):
        for mode in ['top','other-library','unknown-prompt','phase','near-deckout','unknown-library','duplicate','cancellable']:
            with self.subTest(mode=mode):
                o=deepcopy(self.fixture['observation']);p=o['pendingDecision']
                if mode=='top':p['prompt']='Look at the top 4 cards of your library. Put them back in any order.'
                elif mode=='other-library':p['prompt']='Put the revealed cards on the bottom of their library in any order.'
                elif mode=='unknown-prompt':p['prompt']='Order these cards.'
                elif mode=='phase':p['context']['phase']='CASTING'
                elif mode=='duplicate':p['cards'][1]=p['cards'][0]
                elif mode=='cancellable':p['responseSpec']['cancelAllowed']=True
                else:
                    player=next(x for x in o['state']['players'] if x['playerId']==o['agentToAct'])
                    if mode=='near-deckout':player['librarySize']=len(p['cards'])+8
                    else:player.pop('librarySize')
                self.assertIsNone(DefaultPolicySubsystem(self.facts).try_choose(o).choice)

    def test_deck_can_supersede_or_defer_the_general_bottom_policy(self):
        class Custom(DefaultPolicySubsystem):
            def _deck_pending(self,o,p):
                return self._decision(p,'OrderedResponse','deck-bottom-plan',orderedObjects=list(reversed(p['cards'])))
        class Defer(DefaultPolicySubsystem):
            def _deck_pending(self,o,p):return SubsystemDecision(reason='deck-bottom-needs-model')
        o=self.fixture['observation']
        self.assertEqual(Custom(self.facts).try_choose(o).choice.response['orderedObjects'],list(reversed(o['pendingDecision']['cards'])))
        self.assertIsNone(Defer(self.facts).try_choose(o).choice)
