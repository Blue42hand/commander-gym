"""Whole-effect untap bounds; native legality and payment remain authoritative."""
from copy import deepcopy
import gzip,json,unittest
from pathlib import Path
from commander_gym.heuristic_facts import CardFacts
from commander_gym.policy_action_bounds import bounded_observation
from test_heuristic_policy import observation,offer,zone,facts,fact

class BounceUntapPolicyTests(unittest.TestCase):
    def board(self):
        d=deepcopy(facts()._document);d['cards']['Rebounder']=fact(types=['CREATURE'],simplePermanent=False,bounceTargetUntapAbilities=['Rebounder:1'])
        a=offer('ActivateAbility',1);a['action'].update(sourceId='source',abilityId='Rebounder:1',paymentStrategy={'type':'AutoPay'})
        a.update(requiresTargets=True,minTargets=1,targetCount=1,validTargets=['source','engine'],
                 additionalCostInfo={'costType':'BouncePermanent','bounceCount':1,'validBounceTargets':['engine','elf']})
        o=observation(offer('PassPriority',0),a)
        zone(o,'Battlefield','me',source={'name':'Rebounder','controllerId':'me','isTapped':False},
             engine={'name':'Mana Engine','controllerId':'me','isTapped':True},elf={'name':'Elf','controllerId':'me','isTapped':False})
        return o,CardFacts(d)

    def test_keep_useful_target_and_preserve_it_during_payment(self):
        o,f=self.board();before=deepcopy(o);v,r=bounded_observation(o,f);a=v['legalActions'][1]
        self.assertEqual(a['validTargets'],['engine']);self.assertEqual(a['additionalCostInfo']['validBounceTargets'],['elf']);self.assertEqual(o,before)
        self.assertEqual({x['rule'] for x in r},{'bounce-untap-needs-useful-target','preserve-sole-untap-target-during-bounce-payment'})

    def test_no_effect_or_no_surviving_target_is_held(self):
        for mode in ['already-untapped','sole-payment']:
            o,f=self.board()
            if mode=='already-untapped':o['state']['cards']['engine']['isTapped']=False
            else:o['legalActions'][1]['additionalCostInfo']['validBounceTargets']=['engine']
            self.assertEqual([a['kind'] for a in bounded_observation(o,f)[0]['legalActions']],['PassPriority'])

    def test_uncertain_window_effect_payment_and_overrides_keep_original_offer(self):
        for mode in ['stack','payment-error','pending','combat','unknown-phase','unknown-ability','override','self-override','changed-source','complex-cost','preselected-payment','grouped']:
            with self.subTest(mode=mode):
                o,f=self.board();kwargs={}
                if mode=='stack':zone(o,'Stack','enemy',threat={'name':'Unknown'})
                elif mode=='payment-error':o['nativePaymentError']='retry'
                elif mode=='pending':o['pendingDecision']={'type':'Unknown'}
                elif mode=='combat':o['state']['currentStep']='DECLARE_BLOCKERS'
                elif mode=='unknown-phase':o['state']['currentStep']='UNKNOWN'
                elif mode=='unknown-ability':o['legalActions'][1]['action']['abilityId']='Rebounder:2'
                elif mode=='override':kwargs['reaction_timing_exceptions']={'Rebounder'}
                elif mode=='self-override':kwargs['allow_self_targets']={'Rebounder'}
                elif mode=='changed-source':o['state']['cards']['source']['activeEffects']=['unknown']
                elif mode=='complex-cost':o['legalActions'][1]['additionalCostInfo']['alsoRequired']=[{'costType':'Unknown'}]
                elif mode=='preselected-payment':o['legalActions'][1]['action']['costPayment']={'bouncedPermanents':['elf']}
                elif mode=='grouped':o['legalActions'][1]['targetRequirements']=[{'index':0}]
                v,r=bounded_observation(o,f,**kwargs)
                self.assertEqual(v['legalActions'][1],o['legalActions'][1])

    def test_every_known_quiet_noncombat_window_requires_useful_target(self):
        for step in ['UPKEEP','DRAW','PRECOMBAT_MAIN','POSTCOMBAT_MAIN','END','CLEANUP']:
            for active in ['me','enemy']:
                with self.subTest(step=step,active=active):
                    o,f=self.board();o['state'].update(currentStep=step,activePlayerId=active)
                    v,r=bounded_observation(o,f)
                    if active=='me' and step in {'UPKEEP','DRAW'}:
                        # The existing development rule waits for our main phase.
                        self.assertEqual([a['kind'] for a in v['legalActions']],['PassPriority'])
                        self.assertTrue(any(x['rule']=='development-waits-for-main' for x in r))
                    else:
                        a=v['legalActions'][1]
                        self.assertEqual(a['validTargets'],['engine'])
                        self.assertEqual(a['additionalCostInfo']['validBounceTargets'],['elf'])

    def test_returning_a_disabled_commander_remains_a_legitimate_cost_benefit(self):
        o,f=self.board();d=deepcopy(f._document)
        d['cards']['Binding Seal']=fact(types=['ENCHANTMENT'],attachedCombatLock={'activatedAbilities':'all'})
        o['state']['cards']['engine'].update(isCommander=True,attachments=['curse'])
        o['state']['cards']['curse']={'name':'Binding Seal','controllerId':'enemy'}
        v,_=bounded_observation(o,CardFacts(d));self.assertEqual(v['legalActions'][1],o['legalActions'][1])

    def test_unknown_tap_status_retains_inference_and_multiple_targets_keep_payment_domain(self):
        o,f=self.board();o['state']['cards']['source'].pop('isTapped');v,_=bounded_observation(o,f)
        self.assertEqual(v['legalActions'][1]['validTargets'],['source','engine'])
        self.assertEqual(v['legalActions'][1]['additionalCostInfo']['validBounceTargets'],['engine','elf'])

    def test_saved_quiet_windows_exclude_each_actual_noop_or_target_return(self):
        fixture=json.load(gzip.open(Path(__file__).with_name('fixtures')/'cycle-17-untap-windows.json.gz','rt'))
        f=CardFacts(fixture['cardFacts'])
        self.assertEqual(len(fixture['rows']),9)
        for row in fixture['rows']:
            with self.subTest(sequence=row['sequence']):
                self.assertTrue(row['evidence']['nativeMaskedMatch']);o=row['observation'];before=deepcopy(o)
                v,_=bounded_observation(o,f);b=row['baseline'];a=next((a for a in v['legalActions'] if a['actionId']==b['actionId']),None)
                if a is not None:
                    self.assertTrue(not set(b['params']['targets'])<=set(a['validTargets']) or
                                    not set(b['params']['bouncedPermanents'])<=set(a['additionalCostInfo']['validBounceTargets']))
                self.assertEqual(o,before)

    def test_saved_native_state_excludes_both_parts_of_the_bad_choice(self):
        fixture=json.load(gzip.open(Path(__file__).with_name('fixtures')/'cycle-17-bounce-untap.json.gz','rt'))
        self.assertEqual(fixture['sequence'],3754);self.assertTrue(fixture['evidence']['nativeMaskedMatch'])
        o=fixture['observation'];before=deepcopy(o);v,r=bounded_observation(o,CardFacts(fixture['cardFacts']))
        a=next(a for a in v['legalActions'] if a['actionId']==fixture['baseline']['actionId'])
        self.assertEqual(a['validTargets'],['e31']);self.assertNotIn('e31',a['additionalCostInfo']['validBounceTargets'])
        self.assertIn('e86',a['additionalCostInfo']['validBounceTargets']);self.assertEqual(o,before)

    def test_begin_combat_requires_known_empty_combat(self):
        for mode in ['missing','unknown','live']:
            with self.subTest(mode=mode):
                o,f=self.board();o['state']['currentStep']='BEGIN_COMBAT'
                if mode=='missing':o['state'].pop('combat',None)
                elif mode=='unknown':o['state']['combat']={}
                else:o['state']['combat']={'attackers':[{'id':'engine'}]}
                v,_=bounded_observation(o,f)
                self.assertEqual(v['legalActions'][1],o['legalActions'][1])

    def test_begin_combat_preserves_a_useful_untap(self):
        o,f=self.board();o['state'].update(currentStep='BEGIN_COMBAT',combat=None)
        v,_=bounded_observation(o,f)
        self.assertEqual(v['legalActions'][1]['validTargets'],['engine'])
        self.assertEqual(v['legalActions'][1]['additionalCostInfo']['validBounceTargets'],['elf'])

    def test_begin_combat_keeps_reactive_and_explicit_rebuy_plans(self):
        for mode in ['stack','override','self-override']:
            with self.subTest(mode=mode):
                o,f=self.board();o['state'].update(currentStep='BEGIN_COMBAT',combat=None);kwargs={}
                if mode=='stack':zone(o,'Stack','enemy',threat={'name':'Unknown'})
                elif mode=='override':kwargs['reaction_timing_exceptions']={'Rebounder'}
                else:kwargs['allow_self_targets']={'Rebounder'}
                v,_=bounded_observation(o,f,**kwargs)
                self.assertEqual(v['legalActions'][1],o['legalActions'][1])

    def test_saved_begin_combat_mistake_is_replaced_by_local_pass(self):
        from commander_gym.engine_deck_policy import EngineDeckPolicy
        root=Path(__file__).with_name('fixtures')
        fixture=json.load(gzip.open(root/'cycle-23-combat-bounce.json.gz','rt'))
        f=CardFacts(json.load(gzip.open(root/'cycle-22-resource-facts.json.gz','rt')))
        self.assertEqual(fixture['sequence'],2188)
        self.assertTrue(fixture['evidence']['nativeMaskedMatch'])
        self.assertEqual(fixture['evidence']['nativeBaselineStatus'],'accepted')
        o=fixture['observation'];before=deepcopy(o)
        v,_=bounded_observation(o,f)
        self.assertNotIn(fixture['choice']['actionId'],[a['actionId'] for a in v['legalActions']])
        chosen=EngineDeckPolicy(f,'lathril').try_choose(o).choice
        self.assertIsNotNone(chosen)
        self.assertEqual(next(a['kind'] for a in o['legalActions'] if a['actionId']==chosen.action_id),'PassPriority')
        self.assertEqual(o,before)
