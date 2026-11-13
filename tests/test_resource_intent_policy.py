"""Bound ordinary resource benefits to an actual payoff without replacing native legality."""
from copy import deepcopy
import gzip,json,unittest
from pathlib import Path
from commander_gym.heuristic_facts import CardFacts
from commander_gym.policy_action_bounds import bounded_observation
from test_heuristic_policy import observation,offer,zone,fact,facts

class ResourceIntentPolicyTests(unittest.TestCase):
    def draw_board(self):
        d=deepcopy(facts()._document);d['cards']['Resource Spell']=fact(types=['SORCERY'],tags=['DRAW'],spellPolarity=['BENEFICIAL'],beneficialPlayerDrawCount=3)
        a=offer('CastSpell',1,card='spell');a.update(requiresTargets=True,minTargets=1,targetCount=1,validTargets=['enemy','me'])
        o=observation(offer('PassPriority',0),a)
        for p in o['state']['players']:p['librarySize']=40
        zone(o,'Hand','me',spell={'name':'Resource Spell','controllerId':'me'})
        return o,CardFacts(d)

    def test_ordinary_positive_player_draw_is_for_actor(self):
        o,f=self.draw_board();before=deepcopy(o);v,r=bounded_observation(o,f)
        self.assertEqual(v['legalActions'][1]['validTargets'],['me']);self.assertEqual(o,before)
        self.assertIn('ordinary-player-card-advantage-for-self',[x['rule'] for x in r])

    def test_library_risk_draw_punishment_and_deck_override_preserve_player_domain(self):
        for mode in ['own-low','enemy-low','unknown-library','punishment','modifier','unknown-board','override','unknown-count','x','correction','prebound']:
            with self.subTest(mode=mode):
                o,f=self.draw_board();kwargs={};d=deepcopy(f._document)
                if mode=='own-low':o['state']['players'][0]['librarySize']=4
                elif mode=='enemy-low':o['state']['players'][1]['librarySize']=2
                elif mode=='unknown-library':o['state']['players'][1].pop('librarySize')
                elif mode=='punishment':d['cards']['Punisher']=fact(drawSensitive=True);zone(o,'Battlefield','enemy',punisher={'name':'Punisher','controllerId':'enemy'})
                elif mode=='modifier':o['state']['players'][0]['activeEffects']=['unknown']
                elif mode=='unknown-board':zone(o,'Battlefield','enemy',unknown={'name':'Unknown','controllerId':'enemy'})
                elif mode=='override':kwargs['allow_self_targets']={'Resource Spell'}
                elif mode=='unknown-count':d['cards']['Resource Spell'].pop('beneficialPlayerDrawCount')
                elif mode=='x':o['legalActions'][1]['hasXCost']=True
                elif mode=='correction':o['nativePaymentError']='retry'
                elif mode=='prebound':o['legalActions'][1]['action']['targets']=['enemy']
                v,r=bounded_observation(o,CardFacts(d),**kwargs)
                self.assertEqual(v['legalActions'][1]['validTargets'],['enemy','me'])

    def pump_board(self):
        d=deepcopy(facts()._document)
        d['cards']['Booster']=fact(simplePermanent=False,positiveStatBoostAbilities=['Booster:1'],powerManaAbilities=[])
        d['cards']['Power Mage']=fact(simplePermanent=False,powerManaAbilities=['Power Mage:1'])
        d['cards']['Elf']['powerManaAbilities']=[]
        a=offer('ActivateAbility',1);a['action'].update(sourceId='booster',abilityId='Booster:1',paymentStrategy={'type':'AutoPay'})
        a.update(requiresTargets=True,minTargets=1,targetCount=1,validTargets=['mage','elf'])
        mana=offer('ActivateAbility',2,mana=True);mana['action'].update(sourceId='mage',abilityId='Power Mage:1')
        o=observation(offer('PassPriority',0),a,mana)
        zone(o,'Battlefield','me',booster={'name':'Booster','controllerId':'me'},mage={'name':'Power Mage','controllerId':'me','isTapped':False},elf={'name':'Elf','controllerId':'me','isTapped':False})
        return o,CardFacts(d)

    def test_outside_combat_pump_preserves_power_mana_but_not_fixed_mana_body(self):
        o,f=self.pump_board();v,_=bounded_observation(o,f)
        self.assertEqual(v['legalActions'][1]['validTargets'],['mage'])
        o['legalActions'][2]['affordable']=o['legalActions'][2]['isAffordable']=False
        self.assertFalse(any(a['actionId']==1 for a in bounded_observation(o,f)[0]['legalActions']))

    def test_combat_threat_unknown_targets_and_overrides_remain_available(self):
        for mode in ['combat','unknown-stack','unknown-phase','unknown-target','changed-source','override','target-override','payment-correction']:
            with self.subTest(mode=mode):
                o,f=self.pump_board();kwargs={}
                if mode=='combat':o['state']['currentStep']='DECLARE_BLOCKERS'
                elif mode=='unknown-stack':zone(o,'Stack','enemy',threat={'name':'Unknown'})
                elif mode=='unknown-phase':o['state']['currentStep']='UNKNOWN'
                elif mode=='unknown-target':o['state']['cards']['elf']['name']='Unknown'
                elif mode=='changed-source':o['state']['cards']['booster']['activeEffects']=['unknown']
                elif mode=='override':kwargs['reaction_timing_exceptions']={'Booster'}
                elif mode=='target-override':kwargs['allow_self_targets']={'Booster'}
                elif mode=='payment-correction':o['nativePaymentError']='retry'
                v,_=bounded_observation(o,f,**kwargs);a=next(a for a in v['legalActions'] if a['actionId']==1)
                self.assertIn('elf',a['validTargets'])

    def test_pure_life_gain_can_resolve_before_pump(self):
        o,f=self.pump_board();d=deepcopy(f._document);d['cards']['Life Source']=fact(simpleLifeGainTriggers={'Life Source:1':1})
        zone(o,'Stack','enemy',life={'abilityIdentity':{'cardDefinitionId':'Life Source#SET-1','abilityId':'Life Source:1'}})
        v,_=bounded_observation(o,CardFacts(d));self.assertEqual(v['legalActions'][1]['validTargets'],['mage'])

    def test_saved_native_resource_mistakes_are_all_excluded(self):
        root=Path(__file__).with_name('fixtures');x=json.load(gzip.open(root/'cycle-18-resource-mistakes.json.gz','rt'));f=CardFacts(x['cardFacts'])
        self.assertEqual(len(x['rows']),4)
        for row in x['rows']:
            with self.subTest(sequence=row['sequence']):
                o=row['observation'];before=deepcopy(o);v,r=bounded_observation(o,f);a=next((a for a in v['legalActions'] if a['actionId']==row['baseline']['actionId']),None)
                if row['sequence']==8792:self.assertEqual(a['validTargets'],[o['agentToAct']])
                else:self.assertIsNone(a)
                self.assertEqual(o,before)
