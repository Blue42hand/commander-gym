from copy import deepcopy
import unittest
import gzip
import json
from pathlib import Path
from commander_gym.heuristic_facts import CardFacts
from commander_gym.policy_action_bounds import bounded_observation, own_stack_can_resolve
from test_heuristic_policy import facts, fact, observation, offer, zone


class PendingRemovalPolicyTests(unittest.TestCase):
    def test_saved_mixed_stack_removes_the_already_answered_target(self):
        fixture=json.load(gzip.open(Path(__file__).with_name('fixtures')/'cycle-15-pending-removal.json.gz','rt'))
        self.assertEqual(fixture['sequence'],5382)
        self.assertTrue(fixture['evidence']['nativeMaskedMatch'])
        o=fixture['observation'];f=CardFacts(fixture['cardFacts']);before=deepcopy(o)
        self.assertTrue(own_stack_can_resolve(o,f))
        bounded,r=bounded_observation(o,f)
        self.assertTrue(any(x['rule']=='let-own-stack-resolve' for x in r))
        for a in bounded['legalActions']:
            if a['kind'].startswith('Cast') and o['state']['cards'].get(a['action'].get('cardId'),{}).get('name')=='Into the Roil':
                self.assertNotIn('e200',a.get('validTargets',[]))
        self.assertTrue(any(a['kind']=='PassPriority' for a in bounded['legalActions']))
        from commander_gym.default_policies import DefaultPolicySubsystem
        result=DefaultPolicySubsystem(f).try_choose(o)
        self.assertIsNotNone(result.choice)
        self.assertEqual(next(a['kind'] for a in o['legalActions'] if a['actionId']==result.choice.action_id),'PassPriority')
        self.assertEqual(o,before)

    def board(self):
        doc=deepcopy(facts()._document)
        doc['cards']['Quiet Blessing']=fact(simpleLifeGainTriggers={'Quiet Blessing:1':1})
        f=CardFacts(doc)
        cast=offer('CastSpell',1,card='bounce');cast.update(minTargets=1,targetCount=1,validTargets=['threat'],requiresTargets=True)
        cast['action']['paymentStrategy']={'type':'AutoPay'}
        o=observation(offer('PassPriority',0),cast)
        zone(o,'Hand','me',bounce={'name':'Into the Roil'})
        zone(o,'Battlefield','enemy',threat={'name':'Commander','controllerId':'enemy'})
        zone(o,'Stack','me',exile={'name':'Reality Shift','controllerId':'me','targets':[{'type':'Permanent','entityId':'threat'}]},
            life={'name':'Quiet Blessing trigger','controllerId':'enemy','targets':[],
                  'abilityIdentity':{'cardDefinitionId':'Quiet Blessing#TST-1','abilityId':'Quiet Blessing:1'}})
        return o,f

    def test_unrelated_opponent_life_gain_does_not_authorize_redundant_removal(self):
        o,f=self.board();before=deepcopy(o)
        self.assertTrue(own_stack_can_resolve(o,f))
        bounded,r=bounded_observation(o,f)
        self.assertEqual([a['kind'] for a in bounded['legalActions']],['PassPriority'])
        self.assertTrue(any(x['rule']=='let-own-stack-resolve' for x in r));self.assertEqual(o,before)

    def test_other_removal_waits_until_the_owned_stack_resolves(self):
        o,f=self.board();zone(o,'Battlefield','enemy',other={'name':'Commander','controllerId':'enemy'})
        o['legalActions'][1]['validTargets'].append('other')
        bounded,_=bounded_observation(o,f)
        self.assertEqual([a['kind'] for a in bounded['legalActions']],['PassPriority'])
        next(z for z in o['state']['zones'] if z['zoneId']['zoneType']=='Stack')['cardIds'].remove('exile')
        bounded,_=bounded_observation(o,f)
        self.assertTrue(any(a['kind']=='CastSpell' for a in bounded['legalActions']))

    def test_threats_unknown_effects_and_deck_overrides_retain_inference(self):
        for case in ['opponent-spell','unknown-ability','targeted-trigger','counter-own-removal','opponent-only-stack','combat','deck-override','native-payment','unknown-controller']:
            with self.subTest(case=case):
                o,f=self.board();cards=o['state']['cards'];kwargs={}
                if case=='opponent-spell':cards['life'].pop('abilityIdentity');cards['life']['name']='Heroic Intervention'
                elif case=='unknown-ability':cards['life']['abilityIdentity']['abilityId']='Quiet Blessing:2'
                elif case=='targeted-trigger':cards['life']['targets']=[{'type':'Permanent','entityId':'threat'}]
                elif case=='counter-own-removal':zone(o,'Stack','enemy',counter={'name':'Counterspell','controllerId':'enemy','targets':[{'type':'Spell','spellEntityId':'exile'}]})
                elif case=='opponent-only-stack':next(z for z in o['state']['zones'] if z['zoneId']['zoneType']=='Stack')['cardIds'].remove('exile')
                elif case=='combat':o['state']['combat']={'attackers':[{'attackerId':'threat'}]}
                elif case=='deck-override':kwargs['reaction_timing_exceptions']={'Into the Roil'}
                elif case=='native-payment':o['nativePaymentError']='Needs manual payment'
                elif case=='unknown-controller':cards['life'].pop('controllerId')
                bounded,_=bounded_observation(o,f,**kwargs)
                self.assertTrue(any(a['kind']=='CastSpell' for a in bounded['legalActions']))

    def test_unknown_native_fact_contract_is_not_guessed(self):
        o,f=self.board();doc=deepcopy(f._document);doc['cards']['Quiet Blessing'].pop('simpleLifeGainTriggers')
        self.assertFalse(own_stack_can_resolve(o,CardFacts(doc)))

    def test_malformed_life_gain_contract_is_rejected(self):
        _,f=self.board()
        for value in [{'x':True},{'x':0},{'x':11},{'':1},[]]:
            doc=deepcopy(f._document);doc['cards']['Quiet Blessing']['simpleLifeGainTriggers']=value
            with self.assertRaisesRegex(ValueError,'invalid simple life gain trigger contract'):CardFacts(doc)
