from copy import deepcopy
import gzip
import json
from pathlib import Path
import unittest

from commander_gym.heuristic_facts import CardFacts
from commander_gym.policy_action_bounds import bounded_observation
from commander_gym.default_policies import DefaultPolicySubsystem
from test_heuristic_policy import observation, offer, zone, facts, fact


class DrawTimingPolicyTests(unittest.TestCase):
    def board(self):
        doc=deepcopy(facts()._document)
        doc['cards']['Quiet Tome']=fact(types=['ARTIFACT'],tags=['DRAW'],repeatable=True,selfDrawAbilities=['Quiet Tome:1'])
        f=CardFacts(doc);a=offer('ActivateAbility',1)
        a['action'].update(sourceId='tome',abilityId='Quiet Tome:1',paymentStrategy={'type':'AutoPay'})
        o=observation(offer('PassPriority',0),a)
        zone(o,'Battlefield','me',tome={'name':'Quiet Tome','isTapped':False,'controllerId':'me'})
        o['state'].update(currentStep='DECLARE_BLOCKERS',combat={'attackingPlayerId':'me','attackers':[
            {'creatureId':'attacker','attackingTarget':{'type':'Player','playerId':'enemy'},'isBlocked':False,'blockedBy':[]}]})
        return o,f

    def test_quiet_unblocked_combat_draw_waits_without_mutating_input(self):
        o,f=self.board();before=deepcopy(o);bounded,r=bounded_observation(o,f)
        self.assertEqual([a['kind'] for a in bounded['legalActions']],['PassPriority'])
        self.assertTrue(any(x['rule']=='draw-waits-for-development-or-relevant-combat' for x in r))
        self.assertEqual(DefaultPolicySubsystem(f).try_choose(o).choice.action_id,0);self.assertEqual(o,before)

    def test_relevant_and_uncertain_decisions_retain_inference(self):
        for mode in ['blocked','defending','unknown-target','missing-block-status','stack','payment','no-pass','override','changed-source','unknown-ability','targeted','teams']:
            with self.subTest(mode=mode):
                o,f=self.board();attacker=o['state']['combat']['attackers'][0];kwargs={}
                if mode=='blocked':attacker.update(isBlocked=True,blockedBy=['blocker'])
                elif mode=='defending':attacker['attackingTarget']['playerId']='me';o['state']['combat']['attackingPlayerId']='enemy'
                elif mode=='unknown-target':attacker['attackingTarget']={'type':'Permanent','entityId':'walker'}
                elif mode=='missing-block-status':attacker.pop('isBlocked')
                elif mode=='stack':zone(o,'Stack','enemy',spell={'name':'Unknown','controllerId':'enemy'})
                elif mode=='payment':o['nativePaymentError']='Mana required'
                elif mode=='no-pass':o['legalActions']=o['legalActions'][1:]
                elif mode=='override':kwargs['reaction_timing_exceptions']={'Quiet Tome'}
                elif mode=='changed-source':o['state']['cards']['tome']['activeEffects']=['unknown']
                elif mode=='unknown-ability':o['legalActions'][1]['action']['abilityId']='Quiet Tome:2'
                elif mode=='targeted':o['legalActions'][1]['requiresTargets']=True
                elif mode=='teams':o['state']['players'][0]['teamSharedLife']=True
                bounded,_=bounded_observation(o,f,**kwargs)
                self.assertTrue(any(a['kind']=='ActivateAbility' for a in bounded['legalActions']))

    def test_combat_end_and_empty_combat_wait_but_normal_draw_windows_remain(self):
        for step,active,combat,held in [
            ('END_COMBAT','me',{'attackingPlayerId':'me','attackers':[]},True),
            ('BEGIN_COMBAT','enemy',{'attackingPlayerId':'enemy','attackers':[]},True),
            ('PRECOMBAT_MAIN','me',None,False),('POSTCOMBAT_MAIN','me',None,False),
            ('END','enemy',None,False)]:
            with self.subTest(step=step):
                o,f=self.board();o['state'].update(currentStep=step,activePlayerId=active,combat=combat)
                bounded,_=bounded_observation(o,f)
                self.assertEqual(any(a['kind']=='ActivateAbility' for a in bounded['legalActions']),not held)

    def test_whole_card_draw_tag_does_not_certify_a_different_ability(self):
        o,f=self.board();o['state'].update(currentStep='POSTCOMBAT_MAIN',activePlayerId='enemy',combat=None)
        o['legalActions'][1]['action']['abilityId']='Quiet Tome:2'
        bounded,_=bounded_observation(o,f)
        self.assertTrue(any(a['kind']=='ActivateAbility' for a in bounded['legalActions']))

    def test_malformed_native_draw_ability_contract_is_rejected(self):
        _,f=self.board()
        for value in [None,{},[''],[7]]:
            doc=deepcopy(f._document);doc['cards']['Quiet Tome']['selfDrawAbilities']=value
            with self.assertRaises(ValueError):CardFacts(doc)

    def test_saved_game22_combat_end_pass_is_handled_locally(self):
        fixture=json.load(gzip.open(Path(__file__).with_name('fixtures')/'cycle-16-draw-window.json.gz','rt'))
        self.assertEqual(fixture['sequence'],7368);self.assertTrue(fixture['evidence']['nativeMaskedMatch'])
        o=fixture['observation'];before=deepcopy(o);f=CardFacts(fixture['cardFacts'])
        result=DefaultPolicySubsystem(f).try_choose(o);self.assertIsNotNone(result.choice)
        self.assertEqual(result.choice.action_id,fixture['recordedChoice']['actionId']);self.assertEqual(o,before)
