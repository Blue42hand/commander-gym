"""Authentic game27 failure plus native-executed manual payment continuation."""
from copy import deepcopy
import gzip,json,unittest
from pathlib import Path
from commander_gym.default_policies import DefaultPolicySubsystem
from commander_gym.engine_deck_policy import EngineDeckPolicy
from commander_gym.heuristic_facts import CardFacts
from commander_gym.heuristic_policy import ids_in

class HiddenBoardManaPaymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).parent/'fixtures'
        cls.saved=json.load(gzip.open(root/'cycle-21-hidden-board-payment.json.gz','rt'))
        cls.followups=json.load(gzip.open(root/'cycle-21-native-manual-payment-followups.json.gz','rt'))
        cls.facts=CardFacts(json.load(gzip.open(root/'cycle-19-protection-facts.json.gz','rt')))
    def initial(self):return deepcopy(self.saved['observation'])
    def color(self):return deepcopy(self.followups['manualColorObservation'])
    def test_default_and_deck_overlay_complete_native_manual_payment_without_model(self):
        for policy in [DefaultPolicySubsystem(self.facts),EngineDeckPolicy(self.facts,'sythis')]:
            o=self.initial();before=deepcopy(o);result=policy.try_choose(o)
            self.assertEqual(result.choice.action_id,16);self.assertEqual(result.choice.params,{})
            self.assertEqual(result.metadata['reason'],'default-manual-offered-mana-payment');self.assertEqual(o,before)
            c=policy.try_choose(self.color()).choice
            self.assertEqual(c.response,{'type':'ColorChosenResponse','decisionId':'r41','color':'GREEN'})
            c=policy.try_choose(deepcopy(self.followups['fundedPaymentObservation'])).choice
            self.assertTrue(c.response['autoPay']);self.assertFalse(c.response['declined']);self.assertEqual(c.response['selectedSources'],[])
    def test_unrelated_or_changed_color_question_defers_and_discards_intent(self):
        for change in ['source','actor','turn','step','pool','colors','prompt','count','phase','namespace']:
            p=DefaultPolicySubsystem(self.facts);p.try_choose(self.initial());o=self.color();q=o['pendingDecision']
            if change=='source':q['context']['sourceId']='other'
            if change=='actor':q['playerId']='policy-alela'
            if change=='turn':o['state']['turnNumber']+=1
            if change=='step':o['state']['currentStep']='POSTCOMBAT_MAIN'
            if change=='pool':o['state']['players'][0]['manaPool']['red']=1
            if change=='colors':q['availableColors']=['WHITE']
            if change=='prompt':q['prompt']='Choose protection color'
            if change=='count':q['maxColors']=2
            if change=='phase':q['context']['phase']='UNKNOWN'
            if change=='namespace':o['state']['players'][-1]['playerId']='another-game-seat'
            result=p.try_choose(o)
            if change=='colors':self.assertEqual(result.metadata['reason'],'forced-color')
            else:self.assertIsNone(result.choice,change)
            self.assertIsNone(p.try_choose(self.color()).choice,change)
    def test_no_invented_action_or_activation_parameters(self):
        for change in ['no-actions','unaffordable','target','x','extra-cost','unknown-colors','restricted','ambiguous-ability']:
            o=self.initial();p=o['pendingDecision']
            if change=='no-actions':o['legalActions']=[]
            elif change=='ambiguous-ability':
                o['legalActions'] += [deepcopy(a) for a in o['legalActions'] if a['action'].get('sourceId') in ['e482','e483']]
            elif change=='restricted':o['state']['players'][0]['manaPool']['restrictedMana']=[{'amount':1}]
            else:
                for a in o['legalActions']:
                    if a['action'].get('sourceId') not in ['e482','e483']:continue
                    if change=='unaffordable':a['affordable']=a['isAffordable']=False
                    if change=='target':a['requiresTargets']=True
                    if change=='x':a['hasXCost']=True
                    if change=='extra-cost':a['additionalCostInfo']={'unknown':True}
                    if change=='unknown-colors':a['availableManaColors']=['UNKNOWN']
            self.assertIsNone(DefaultPolicySubsystem(self.facts).try_choose(o).choice,change)
    def test_face_down_identity_is_not_needed_and_fresh_color_question_defers(self):
        o=self.initial()
        for i in ids_in(o['state'],'Battlefield'):
            if o['state']['cards'][i].get('isFaceDown'):o['state']['cards'][i]['name']='Unknown face-down card'
        self.assertEqual(DefaultPolicySubsystem(self.facts).try_choose(o).choice.action_id,16)
        self.assertIsNone(DefaultPolicySubsystem(self.facts).try_choose(self.color()).choice)
    def test_existing_batch_path_remains_for_public_board(self):
        o=self.initial()
        for c in o['state']['cards'].values():c['isFaceDown']=False
        self.assertEqual(DefaultPolicySubsystem(self.facts).try_choose(o).choice.response['selectedSources'],['e482'])
    def test_intervening_action_callback_clears_color_intent(self):
        p=DefaultPolicySubsystem(self.facts);p.try_choose(self.initial());o=self.initial();o['pendingDecision']=None
        p.try_choose(o)
        self.assertIsNone(p.try_choose(self.color()).choice)
