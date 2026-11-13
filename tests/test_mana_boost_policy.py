from copy import deepcopy
import unittest
import gzip
import json
from pathlib import Path

from commander_gym.heuristic_facts import CardFacts
from commander_gym.policy_action_bounds import bounded_observation
from test_heuristic_policy import observation, offer, zone, facts, fact


class ManaBoostPolicyTests(unittest.TestCase):
    def test_saved_game20_ritual_choice_is_replaced_with_holding_the_card(self):
        fixture=json.load(gzip.open(Path(__file__).with_name('fixtures')/'cycle-14-ritual.json.gz','rt'))
        self.assertEqual(fixture['sequence'],413)
        self.assertTrue(fixture['evidence']['nativeMaskedMatch'])
        o=fixture['observation'];_,f=self.board();doc=deepcopy(f._document)
        doc['cards']['High Tide']=doc['cards'].pop('Sapphire Surge')
        f=CardFacts(doc);before=deepcopy(o)
        bounded,r=bounded_observation(o,f)
        self.assertFalse(any(a['kind']=='CastSpell' for a in bounded['legalActions']))
        self.assertTrue(any(x['rule']=='hold-land-mana-boost-without-remaining-source' for x in r))
        from commander_gym.default_policies import DefaultPolicySubsystem
        result=DefaultPolicySubsystem(f).try_choose(o)
        self.assertIsNotNone(result.choice)
        chosen=next(a for a in o['legalActions'] if a['actionId']==result.choice.action_id)
        self.assertEqual(chosen['kind'],'PassPriority')
        self.assertEqual(o,before)

    def board(self):
        # Synthetic card name proves this default policy follows a native mechanic
        # contract rather than the Talrand profile or a High Tide name check.
        doc=deepcopy(facts()._document)
        doc['cards']['Island']=fact(manaCost='',types=['LAND'],subtypes=['Island'],manaColors=['U'],simpleMana=True)
        doc['cards']['Sapphire Surge']=fact(manaCost='{U}',types=['INSTANT'],temporaryLandManaBoost={'subtype':'Island','color':'U','amount':1})
        f=CardFacts(doc)
        cast=offer('CastSpell',1,card='spell');cast['action']['paymentStrategy']={'type':'AutoPay'}
        o=observation(offer('PassPriority',0),cast)
        zone(o,'Hand','me',spell={'name':'Sapphire Surge'})
        zone(o,'Battlefield','me',land={'name':'Island','cardTypes':['LAND'],'subtypes':['Island'],'isTapped':False})
        o['state']['players'][0]['manaPool']={**{c:0 for c in ['white','blue','black','red','green','colorless']},'restrictedMana':[]}
        return o,f

    def test_single_source_boost_is_held_without_mutating_native_input(self):
        o,f=self.board();before=deepcopy(o)
        bounded,r=bounded_observation(o,f)
        self.assertEqual([a['kind'] for a in bounded['legalActions']],['PassPriority'])
        self.assertEqual(r[0]['rule'],'hold-land-mana-boost-without-remaining-source')
        self.assertEqual(o,before)

    def test_plans_and_uncertainty_keep_inference(self):
        for case in ['second-land','engine','land-drop','floating','unknown-pool','stack','payment','override']:
            with self.subTest(case=case):
                o,f=self.board();kwargs={}
                if case=='second-land':zone(o,'Battlefield','me',other={'name':'Island','cardTypes':['LAND'],'subtypes':['Island'],'isTapped':False})
                elif case=='engine':zone(o,'Battlefield','me',engine={'name':'Commander','cardTypes':['CREATURE'],'isCommander':True})
                elif case=='land-drop':o['legalActions'].append(offer('PlayLand',2,card='newland'))
                elif case=='floating':o['state']['players'][0]['manaPool']['blue']=1
                elif case=='unknown-pool':del o['state']['players'][0]['manaPool']
                elif case=='stack':zone(o,'Stack','enemy',threat={'name':'Unknown'})
                elif case=='payment':o['nativePaymentError']='Needs manual mana'
                elif case=='override':kwargs['reaction_timing_exceptions']={'Sapphire Surge'}
                bounded,_=bounded_observation(o,f,**kwargs)
                self.assertTrue(any(a['kind']=='CastSpell' for a in bounded['legalActions']))

    def test_missing_native_mechanic_contract_keeps_inference(self):
        o,f=self.board();doc=deepcopy(f._document);del doc['cards']['Sapphire Surge']['temporaryLandManaBoost']
        bounded,_=bounded_observation(o,CardFacts(doc))
        self.assertTrue(any(a['kind']=='CastSpell' for a in bounded['legalActions']))

    def test_malformed_native_boost_is_rejected(self):
        _,f=self.board()
        for change in [{'amount':True},{'subtype':'Elf'},{'color':'C'},{'extra':'code'}]:
            doc=deepcopy(f._document);doc['cards']['Sapphire Surge']['temporaryLandManaBoost'].update(change)
            with self.assertRaisesRegex(ValueError,'invalid temporary land mana boost'):
                CardFacts(doc)
