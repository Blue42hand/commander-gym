"""Avoid paying twice for the same pending pure self-return effect."""
from copy import deepcopy
import unittest,json,gzip
from pathlib import Path
from commander_gym.heuristic_facts import CardFacts
from commander_gym.policy_action_bounds import bounded_observation
from test_heuristic_policy import observation,offer,zone,facts,fact

class SelfReturnPolicyTests(unittest.TestCase):
    def board(self):
        d=deepcopy(facts()._document);d['cards']['Reusable Aura']=fact(types=['ENCHANTMENT'],selfReturnToHandAbilities=['Reusable Aura:1'])
        a=offer('ActivateAbility',1);a['action'].update(sourceId='source',abilityId='Reusable Aura:1',paymentStrategy={'type':'AutoPay'})
        o=observation(offer('PassPriority',0),a)
        zone(o,'Battlefield','me',source={'name':'Reusable Aura','controllerId':'me'})
        zone(o,'Stack','me',ability={'controllerId':'me','abilityIdentity':{'cardDefinitionId':'Reusable Aura#SET-1','abilityId':'Reusable Aura:1'}})
        return o,CardFacts(d)

    def test_duplicate_is_held_without_mutating_native_input(self):
        o,f=self.board();before=deepcopy(o);v,r=bounded_observation(o,f)
        self.assertEqual([a['kind'] for a in v['legalActions']],['PassPriority'])
        self.assertIn('self-return-already-on-stack',[x['rule'] for x in r]);self.assertEqual(o,before)

    def test_first_activation_remains_available(self):
        o,f=self.board();o['state']['zones']=[z for z in o['state']['zones'] if z['zoneId']['zoneType']!='Stack']
        self.assertTrue(any(a['kind']=='ActivateAbility' for a in bounded_observation(o,f)[0]['legalActions']))

    def test_ambiguous_source_intervening_response_and_overrides_retain_inference(self):
        for mode in ['other-definition','other-ability','opponent-top','unknown-top','response','duplicate-source','source-copy','changed-source','changed-stack','pending','correction','override','unknown-contract']:
            with self.subTest(mode=mode):
                o,f=self.board();kwargs={};cards=o['state']['cards']
                if mode=='other-definition':cards['ability']['abilityIdentity']['cardDefinitionId']='Other#SET-1'
                elif mode=='other-ability':cards['ability']['abilityIdentity']['abilityId']='Reusable Aura:2'
                elif mode=='opponent-top':cards['ability']['controllerId']='enemy'
                elif mode=='unknown-top':cards['ability'].pop('abilityIdentity')
                elif mode=='response':o['state']['zones'][1]['cardIds'].append('response');cards['response']={'name':'Unknown response','controllerId':'enemy'}
                elif mode=='duplicate-source':o['state']['zones'][0]['cardIds'].append('copy');cards['copy']={'name':'Reusable Aura','controllerId':'me'}
                elif mode=='source-copy':cards['source']['copyOf']='original'
                elif mode=='changed-source':cards['source']['activeEffects']=['unknown']
                elif mode=='changed-stack':cards['ability']['activeEffects']=['unknown']
                elif mode=='pending':o['pendingDecision']={'kind':'Unknown'}
                elif mode=='correction':o['nativePaymentError']='retry'
                elif mode=='override':kwargs['reaction_timing_exceptions']={'Reusable Aura'}
                elif mode=='unknown-contract':o['legalActions'][1]['action']['abilityId']='Reusable Aura:2'
                v,r=bounded_observation(o,f,**kwargs)
                self.assertNotIn('self-return-already-on-stack',[x['rule'] for x in r])

    def test_authentic_repeated_activations_are_excluded(self):
        root=Path(__file__).with_name('fixtures');x=json.load(gzip.open(root/'cycle-18-self-return.json.gz','rt'))
        facts=CardFacts(json.load(gzip.open(root/'cycle-18-facts.json.gz','rt')))
        self.assertEqual(len(x['rows']),3)
        for row in x['rows']:
            with self.subTest(sequence=row['sequence']):
                self.assertTrue(row['evidence']['nativeMaskedMatch']);view,r=bounded_observation(row['observation'],facts)
                self.assertFalse(any(a['actionId']==row['baseline']['actionId'] for a in view['legalActions']))
                self.assertIn('self-return-already-on-stack',[x['rule'] for x in r])
