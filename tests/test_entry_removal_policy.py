"""Whole native entry-removal plans; never predict a target domain as legal."""
from copy import deepcopy
import gzip,json,unittest
from pathlib import Path
from commander_gym.default_policies import DefaultPolicySubsystem
from commander_gym.heuristic_facts import CardFacts
from test_heuristic_policy import observation,offer,zone,fact,facts

class EntryRemovalPolicyTests(unittest.TestCase):
    def board(self):
        d=deepcopy(facts()._document);d['cards']['Removal Body']=fact(types=['ENCHANTMENT'],simplePermanent=False,tags=['REMOVAL'],entryStatReduction={'abilityId':'Removal Body:1','power':-3,'toughness':-3,'suppressesHexproof':True,'suppressesWard':True})
        d['cards']['Mana Engine']=fact(repeatable=True,tags=['RAMP'],variableMana=True)
        a=offer('CastSpell',1,card='spell');a['action']['paymentStrategy']={'type':'AutoPay'}
        o=observation(offer('PassPriority',0),a);o['state']['players'][0]['librarySize']=40
        zone(o,'Hand','me',spell={'name':'Removal Body','controllerId':'me'})
        zone(o,'Battlefield','enemy',engine={'name':'Mana Engine','controllerId':'enemy','cardTypes':['CREATURE'],'power':1,'toughness':2,'keywords':[],
            'abilityFlags':[],'protections':[],'hexproofFromColors':[],'hexproofFromMonocolored':False,'hexproofFromMulticolored':False,'isFaceDown':False,'isPhasedOut':False})
        return o,CardFacts(d)

    def test_known_engine_gets_a_plain_cast_plan_from_default_policies(self):
        o,f=self.board();before=deepcopy(o);r=DefaultPolicySubsystem(f).try_choose(o)
        self.assertIsNotNone(r.choice);self.assertEqual(r.choice.action_id,1);self.assertEqual(dict(r.choice.params),{})
        self.assertEqual(r.metadata['reason'],'shared-entry-removal-clear-engine');self.assertEqual(o,before)

    def test_unknown_protection_effects_timing_cost_and_override_stay_with_inference(self):
        for mode in ['stack','opponent-turn','combat','unknown-fact','toughness','shroud','protection','unknown-keywords','missing-hex','changed-source','changed-victim','player-effect','face-down','phased-out','complex-cost','manual-cost','reaction-override','self-override','low-library','vanilla']:
            with self.subTest(mode=mode):
                o,f=self.board();c=o['state']['cards']['engine'];policy=DefaultPolicySubsystem(f)
                if mode=='stack':zone(o,'Stack','enemy',spellOnStack={'name':'Unknown'})
                elif mode=='opponent-turn':o['state']['activePlayerId']='enemy'
                elif mode=='combat':o['state']['currentStep']='DECLARE_BLOCKERS'
                elif mode=='unknown-fact':o['state']['cards']['spell']['name']='Unknown'
                elif mode=='toughness':c['toughness']=7
                elif mode=='shroud':c['keywords']=['SHROUD']
                elif mode=='protection':c['protections']=['BLACK']
                elif mode=='unknown-keywords':c.pop('keywords')
                elif mode=='missing-hex':c.pop('hexproofFromColors')
                elif mode=='changed-source':o['state']['cards']['spell']['activeEffects']=['unknown']
                elif mode=='changed-victim':c['activeEffects']=['unknown']
                elif mode=='player-effect':o['state']['players'][1]['activeEffects']=['unknown']
                elif mode=='face-down':c['isFaceDown']=True
                elif mode=='phased-out':c['isPhasedOut']=True
                elif mode=='complex-cost':o['legalActions'][1]['additionalCostInfo']={'costType':'Unknown'}
                elif mode=='manual-cost':o['legalActions'][1]['action']['paymentStrategy']={'type':'Manual'}
                elif mode=='reaction-override':policy.reaction_timing_exceptions={'Removal Body'}
                elif mode=='self-override':policy.self_target_exceptions={'Removal Body'}
                elif mode=='low-library':o['state']['players'][0]['librarySize']=3
                elif mode=='vanilla':c['name']='Elf'
                r=policy.try_choose(o);self.assertNotEqual(r.metadata.get('reason'),'shared-entry-removal-clear-engine')

    def test_zero_toughness_kills_indestructible_but_damage_does_not(self):
        o,f=self.board();c=o['state']['cards']['engine'];c['keywords']=['INDESTRUCTIBLE']
        self.assertEqual(DefaultPolicySubsystem(f).try_choose(o).metadata.get('reason'),'shared-entry-removal-clear-engine')
        c.update(toughness=4,damage=2)
        self.assertNotEqual(DefaultPolicySubsystem(f).try_choose(o).metadata.get('reason'),'shared-entry-removal-clear-engine')
        c['keywords']=[]
        self.assertEqual(DefaultPolicySubsystem(f).try_choose(o).metadata.get('reason'),'shared-entry-removal-clear-engine')

    def test_suppressed_hexproof_can_be_targeted_after_entry(self):
        o,f=self.board();o['state']['cards']['engine']['keywords']=['HEXPROOF']
        self.assertEqual(DefaultPolicySubsystem(f).try_choose(o).metadata.get('reason'),'shared-entry-removal-clear-engine')
        d=deepcopy(f._document);d['cards']['Removal Body']['entryStatReduction']['suppressesHexproof']=False
        self.assertNotEqual(DefaultPolicySubsystem(CardFacts(d)).try_choose(o).metadata.get('reason'),'shared-entry-removal-clear-engine')

    def test_authentic_accepted_pass_now_has_a_native_payable_entry_plan(self):
        root=Path(__file__).with_name('fixtures');row=json.load(gzip.open(root/'cycle-18-entry-removal.json.gz','rt'))
        f=CardFacts(json.load(gzip.open(root/'cycle-18-entry-facts.json.gz','rt')))
        self.assertTrue(row['evidence']['nativeBaselineAccepted']);o=row['observation'];r=DefaultPolicySubsystem(f).try_choose(o)
        self.assertIsNotNone(r.choice);a=next(a for a in o['legalActions'] if a['actionId']==r.choice.action_id)
        self.assertEqual(a['kind'],'CastSpell');self.assertEqual(o['state']['cards'][a['action']['cardId']]['name'],'Nowhere to Run')
        self.assertTrue(a['affordable']);self.assertEqual(dict(r.choice.params),{})
