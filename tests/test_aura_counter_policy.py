from copy import deepcopy
import gzip,json,unittest
from pathlib import Path
from commander_gym.heuristic_facts import CardFacts
from commander_gym.default_policies import DefaultPolicySubsystem
from commander_gym.policy_action_bounds import bounded_observation,ranked_combat_lock_targets
from commander_gym.bounded_policy_fallback import decision_context,_candidate_description
from test_heuristic_policy import observation,offer,zone,facts,fact,decision

class AuraCounterPolicyTests(unittest.TestCase):
    def aura(self):
        d=deepcopy(facts()._document)
        d['cards'].update({
            'Binding Aura':fact(types=['ENCHANTMENT'],tags=['NEUTRALIZE'],spellPolarity=['HARMFUL'],attachedCombatLock={'activatedAbilities':'all'}),
            'Huge Flyer':fact(activatedAbilityCounts={'mana':0,'nonMana':0}),
            'Trigger Commander':fact(tags=['DRAW'],staticValue=100.,repeatable=True,activatedAbilityCounts={'mana':0,'nonMana':0}),
            'Mana Engine':fact(tags=['RAMP'],variableMana=True,activatedAbilityCounts={'mana':1,'nonMana':0})})
        a=offer('CastSpell',1,card='aura');a.update(requiresTargets=True,minTargets=1,targetCount=1,validTargets=['large','commander','mana'])
        o=observation(offer('PassPriority',0),a)
        zone(o,'Hand','me',aura={'name':'Binding Aura','controllerId':'me'})
        zone(o,'Battlefield','enemy',large={'name':'Huge Flyer','controllerId':'enemy','power':17,'toughness':17,'cardTypes':['CREATURE']},
             commander={'name':'Trigger Commander','controllerId':'enemy','power':2,'toughness':2,'cardTypes':['CREATURE'],'isCommander':True},
             mana={'name':'Mana Engine','controllerId':'enemy','power':2,'toughness':2,'cardTypes':['CREATURE']})
        return o,CardFacts(d)

    def positive(self):
        d=deepcopy(facts()._document);d['cards']['Counter Spirit']=fact(positiveCounterTriggers={'Put a +1/+1 counter on target creature':1})
        o=decision('ChooseTargetsDecision','TargetsResponse',{'selectedTargets':'MAP'},
                   context={'sourceName':'Counter Spirit','effectHint':'Put a +1/+1 counter on target creature.'},
                   targetRequirements=[{'index':0,'minTargets':1,'maxTargets':1}],legalTargets={'0':['mine','enemy']})
        zone(o,'Battlefield','me',mine={'name':'Elf','controllerId':'me','cardTypes':['CREATURE']})
        zone(o,'Battlefield','enemy',enemy={'name':'Dragon','controllerId':'enemy','cardTypes':['CREATURE']})
        return o,CardFacts(d)

    def test_combat_lock_does_not_value_a_trigger_engine_as_removed(self):
        o,f=self.aura();before=deepcopy(o);v,_=bounded_observation(o,f)
        self.assertEqual(v['legalActions'][1]['validTargets'],['large']);self.assertEqual(o,before)

    def test_activation_lock_scores_only_abilities_it_suppresses(self):
        o,f=self.aura();o['state']['cards']['large'].update(power=3,toughness=3)
        targets=['large','commander','mana']
        self.assertEqual(ranked_combat_lock_targets(o,targets,f,{'activatedAbilities':'all'}),['mana'])
        self.assertNotIn('mana',ranked_combat_lock_targets(o,targets,f,{'activatedAbilities':'none'}))

    def test_uncovered_or_modified_target_facts_keep_inference_domain(self):
        for mode in ['modified','unknown-counts','unknown-source','unknown-stats']:
            with self.subTest(mode=mode):
                o,f=self.aura();d=deepcopy(f._document)
                if mode=='modified':o['state']['cards']['large']['activeEffects']=['unknown']
                elif mode=='unknown-counts':d['cards']['Huge Flyer'].pop('activatedAbilityCounts')
                elif mode=='unknown-source':d['cards']['Binding Aura'].pop('attachedCombatLock')
                else:o['state']['cards']['large'].pop('power')
                v,_=bounded_observation(o,CardFacts(d));self.assertEqual(v['legalActions'][1]['validTargets'],['large','commander','mana'])

    def test_combat_and_response_tradeoffs_keep_the_native_domain(self):
        for mode in ['combat','stack','opponent','payment']:
            with self.subTest(mode=mode):
                o,f=self.aura()
                if mode=='combat':o['state'].update(currentStep='DECLARE_BLOCKERS',combat={'attackers':[]})
                elif mode=='stack':zone(o,'Stack','enemy',threat={'name':'Unknown'})
                elif mode=='opponent':o['state']['activePlayerId']='enemy'
                else:o['nativePaymentError']='retry'
                # Other policy layers may hold this synthetic spell for timing.
                # This source-aware scorer must preserve uncertain tradeoffs.
                self.assertEqual(ranked_combat_lock_targets(o,['large','commander','mana'],f,{'activatedAbilities':'all'}),['large','commander','mana'])

    def test_model_context_describes_actual_combat_scope(self):
        o,f=self.aura();v,_=bounded_observation(o,f);a=v['legalActions'][1]
        context=decision_context(v,frozen=False,policy_context=None,facts=f)
        self.assertTrue(context['cards']['aura']['reviewedEffects']['keepsTriggeredAndStaticAbilities'])
        wire={'channel':'action','choice':{'semanticId':a['semanticId'],'params':{'targets':['large']}}}
        self.assertEqual(_candidate_description(wire,v,f)['reviewedEffects'],context['cards']['aura']['reviewedEffects'])

    def test_positive_counter_uses_own_target_without_mutating_native_input(self):
        o,f=self.positive();before=deepcopy(o);v,r=bounded_observation(o,f)
        self.assertEqual(v['pendingDecision']['legalTargets']['0'],['mine']);self.assertEqual(o,before)
        result=DefaultPolicySubsystem(f).try_choose(o);self.assertIsNotNone(result.choice)
        self.assertEqual(result.choice.response['selectedTargets'],{'0':['mine']})

    def test_positive_counter_mandatory_or_uncertain_choices_remain_available(self):
        for mode in ['no-own','unknown-controller','override','hint-mismatch','complex','payment']:
            with self.subTest(mode=mode):
                o,f=self.positive();kwargs={}
                if mode=='no-own':o['pendingDecision']['legalTargets']['0']=['enemy']
                elif mode=='unknown-controller':o['state']['cards']['enemy'].pop('controllerId')
                elif mode=='override':kwargs['allow_self_targets']={'Counter Spirit'}
                elif mode=='hint-mismatch':o['pendingDecision']['context']['effectHint']='Deal 1 damage to target creature'
                elif mode=='complex':o['pendingDecision']['targetRequirements'][0]['differentNames']=True
                else:o['nativePaymentError']='retry'
                v,_=bounded_observation(o,f,**kwargs);self.assertEqual(v['pendingDecision']['legalTargets'],o['pendingDecision']['legalTargets'])

    def test_saved_arrest_targets_the_large_combat_threat(self):
        fixture=json.load(gzip.open(Path(__file__).with_name('fixtures')/'cycle-17-aura-counter.json.gz','rt'))
        r=fixture['rows']['7821'];self.assertTrue(r['evidence']['nativeMaskedMatch']);o=r['observation'];before=deepcopy(o)
        v,_=bounded_observation(o,CardFacts(fixture['cardFacts']))
        a=next(a for a in v['legalActions'] if a['actionId']==r['baseline']['actionId'])
        self.assertEqual(a['validTargets'],['e362']);self.assertEqual(o,before)

    def test_saved_visitor_never_grows_the_opposing_flyer_when_own_targets_exist(self):
        fixture=json.load(gzip.open(Path(__file__).with_name('fixtures')/'cycle-17-aura-counter.json.gz','rt'))
        r=fixture['rows']['7828'];self.assertTrue(r['evidence']['nativeMaskedMatch']);o=r['observation'];before=deepcopy(o)
        v,_=bounded_observation(o,CardFacts(fixture['cardFacts']))
        ts=v['pendingDecision']['legalTargets']['0'];self.assertTrue(ts);self.assertNotIn('e362',ts)
        self.assertTrue(all(o['state']['cards'][t]['controllerId']==o['agentToAct'] for t in ts));self.assertEqual(o,before)

    def test_malformed_native_effect_contracts_are_rejected(self):
        for field,value in [('attachedCombatLock',{'activatedAbilities':[]}),('attachedCombatLock',{}),
                            ('activatedAbilityCounts',{'mana':True,'nonMana':0}),('positiveCounterTriggers',{'effect':-1})]:
            o,f=self.aura();d=deepcopy(f._document);d['cards']['Binding Aura'][field]=value
            with self.assertRaises(ValueError):CardFacts(d)
