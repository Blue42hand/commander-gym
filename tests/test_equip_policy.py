from copy import deepcopy
import gzip
import json
from pathlib import Path
import unittest
from commander_gym.heuristic_facts import CardFacts
from commander_gym.policy_action_bounds import bounded_observation
from test_heuristic_policy import observation,offer,zone,facts,fact

class EquipPolicyTests(unittest.TestCase):
    def board(self):
        doc=deepcopy(facts()._document)
        doc['cards']['Training Blade']=fact(types=['ARTIFACT'],equipAbilities=['Training Blade:1'])
        a=offer('ActivateAbility',1)
        a['action'].update(sourceId='blade',abilityId='Training Blade:1',paymentStrategy={'type':'AutoPay'})
        a.update(requiresTargets=True,minTargets=1,targetCount=1,validTargets=['host','other'])
        o=observation(offer('PassPriority',0),a)
        zone(o,'Battlefield','me',blade={'name':'Training Blade','controllerId':'me'},host={'name':'Elf','attachments':['blade'],'controllerId':'me'},other={'name':'Elf','attachments':[],'controllerId':'me'})
        return o,CardFacts(doc)

    def test_only_current_host_is_removed_and_input_is_preserved(self):
        o,f=self.board();before=deepcopy(o);v,r=bounded_observation(o,f)
        self.assertEqual(v['legalActions'][1]['validTargets'],['other']);self.assertEqual(o,before)
        self.assertTrue(any(x['rule']=='avoid-equipping-current-host' for x in r))
        o['legalActions'][1]['validTargets']=['host']
        self.assertEqual([a['kind'] for a in bounded_observation(o,f)[0]['legalActions']],['PassPriority'])

    def test_uncertainty_or_deck_override_retains_offer(self):
        for mode in ['unknown-ability','unknown-attachments','override','pending','payment','complex','no-pass']:
            with self.subTest(mode=mode):
                o,f=self.board();kwargs={};o['legalActions'][1]['validTargets']=['host']
                if mode=='unknown-ability':o['legalActions'][1]['action']['abilityId']='Training Blade:2'
                elif mode=='unknown-attachments':o['state']['cards']['host'].pop('attachments')
                elif mode=='override':kwargs['reaction_timing_exceptions']={'Training Blade'}
                elif mode=='pending':o['pendingDecision']={'type':'Unknown'}
                elif mode=='payment':o['nativePaymentError']='retry'
                elif mode=='complex':o['legalActions'][1]['targetRequirements']=[{'index':0}]
                elif mode=='no-pass':o['legalActions']=o['legalActions'][1:]
                v,_=bounded_observation(o,f,**kwargs)
                self.assertTrue(any(a['kind']=='ActivateAbility' for a in v['legalActions']))

    def test_saved_game22_repeated_equip_is_removed(self):
        fixture=json.load(gzip.open(Path(__file__).with_name('fixtures')/'cycle-16-equip.json.gz','rt'))
        self.assertEqual(fixture['sequence'],6577);self.assertTrue(fixture['evidence']['nativeMaskedMatch'])
        o=fixture['observation'];before=deepcopy(o)
        v,r=bounded_observation(o,CardFacts(fixture['cardFacts']))
        self.assertNotIn(fixture['recordedChoice']['actionId'],[a['actionId'] for a in v['legalActions']])
        self.assertTrue(any(x['rule']=='avoid-equipping-current-host' for x in r));self.assertEqual(o,before)
