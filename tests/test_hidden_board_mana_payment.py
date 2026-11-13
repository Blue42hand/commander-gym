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

class SavedDeferredManaColorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).parent/'fixtures'
        cls.rows={r['sequence']:r for r in json.load(gzip.open(root/'cycle-29-mana-color-windows.json.gz','rt'))['rows']}
        cls.facts=CardFacts(json.load(gzip.open(root/'cycle-28-native-facts.json.gz','rt')))
    def initial(self):return deepcopy(self.rows[5197]['observation'])
    def color(self):return deepcopy(self.rows[5204]['observation'])
    def test_default_and_deck_complete_deferred_manual_activation_color(self):
        for policy in [DefaultPolicySubsystem(self.facts),EngineDeckPolicy(self.facts,'alela')]:
            initial=self.initial();before=deepcopy(initial)
            self.assertIsNone(policy.try_choose(initial).choice)
            self.assertEqual(initial,before)
            color=self.color();before=deepcopy(color);c=policy.try_choose(color).choice
            self.assertEqual(c.response,{**self.rows[5204]['choice']['response'],'color':'BLUE'})
            self.assertEqual(color,before)
            self.assertIsNone(policy.try_choose(self.color()).choice)
            self.assertEqual(self.rows[5204]['evidence']['nativeBaselineStatus'],'accepted')
    def test_changed_or_unrelated_followup_clears_memory(self):
        for mode in ['source','actor','turn','step','pool','namespace','prompt','phase','count','unavailable','intervening-decision','intervening-action']:
            with self.subTest(mode=mode):
                policy=DefaultPolicySubsystem(self.facts);policy.try_choose(self.initial());o=self.color();p=o['pendingDecision']
                if mode=='source':p['context']['sourceId']='unknown'
                elif mode=='actor':p['playerId']='policy-talrand'
                elif mode=='turn':o['state']['turnNumber']+=1
                elif mode=='step':o['state']['currentStep']='POSTCOMBAT_MAIN'
                elif mode=='pool':next(v for v in o['state']['players'] if v['playerId']==o['agentToAct'])['manaPool']['red']=1
                elif mode=='namespace':o['state']['players'][-1]['playerId']='other-game'
                elif mode=='prompt':p['prompt']='Choose protection color'
                elif mode=='phase':p['context']['phase']='UNKNOWN'
                elif mode=='count':p['maxColors']=2
                elif mode=='unavailable':p['availableColors']=['WHITE','BLACK']
                elif mode=='intervening-decision':p['context']['sourceId']='different-resolution'
                elif mode=='intervening-action':o['pendingDecision']=None
                policy.try_choose(o)
                self.assertIsNone(policy.try_choose(self.color()).choice)
    def test_unknown_or_nonunique_required_color_keeps_inference(self):
        for mode in ['generic','multiple-pips','unknown-cost','restricted','unknown-pool','no-color-actions','unaffordable']:
            with self.subTest(mode=mode):
                policy=DefaultPolicySubsystem(self.facts);o=self.initial();p=o['pendingDecision']
                if mode=='generic':p['requiredCost']='{1}'
                elif mode=='multiple-pips':p['requiredCost']='{W}{U}'
                elif mode=='unknown-cost':p['requiredCost']='unknown'
                elif mode=='restricted':next(v for v in o['state']['players'] if v['playerId']==o['agentToAct'])['manaPool']['restrictedMana']=[{'unknown':True}]
                elif mode=='unknown-pool':next(v for v in o['state']['players'] if v['playerId']==o['agentToAct'])['manaPool'].pop('blue')
                else:
                    for a in o['legalActions']:
                        if mode=='no-color-actions':a['requiresManaColorChoice']=False
                        else:a['affordable']=a['isAffordable']=False
                policy.try_choose(o)
                self.assertIsNone(policy.try_choose(self.color()).choice)
    def test_fresh_color_question_has_no_assumed_payment_goal(self):
        self.assertIsNone(DefaultPolicySubsystem(self.facts).try_choose(self.color()).choice)
