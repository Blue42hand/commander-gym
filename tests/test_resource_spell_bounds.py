"""Timing and nonzero resource benefits use native semantic facts, not DRAW tags."""
from copy import deepcopy
import gzip,json,unittest
from pathlib import Path
from commander_gym.heuristic_facts import CardFacts
from commander_gym.policy_action_bounds import bounded_observation
from commander_gym.default_policies import DefaultPolicySubsystem
from test_heuristic_policy import observation,offer,zone,facts,fact

class ResourceSpellBoundsTests(unittest.TestCase):
    def board(self):
        doc=deepcopy(facts()._document);doc['cards']['Resource Spell']=fact(types=['INSTANT'],tags=[],selfResourceDrawSpell=True)
        a=offer('CastSpell',1,card='spell');a.update(requiresTargets=False,hasXCost=False)
        o=observation(offer('PassPriority',0),a)
        zone(o,'Hand','me',spell={'name':'Resource Spell','controllerId':'me','activeEffects':[]})
        o['state'].update(currentStep='DECLARE_BLOCKERS',activePlayerId='me',combat={'attackingPlayerId':'me','attackers':[
            {'creatureId':'attacker','attackingTarget':{'type':'Player','playerId':'enemy'},'isBlocked':False,'blockedBy':[]}]})
        return o,CardFacts(doc)
    def test_ordinary_self_resource_cast_waits_in_irrelevant_combat(self):
        o,f=self.board();before=deepcopy(o);b,r=bounded_observation(o,f)
        self.assertEqual([a['kind'] for a in b['legalActions']],['PassPriority'])
        self.assertIn('draw-waits-for-development-or-relevant-combat',[v['rule'] for v in r]);self.assertEqual(o,before)
        self.assertEqual(DefaultPolicySubsystem(f).try_choose(o).choice.action_id,0)
    def test_uncertain_or_relevant_casts_retain_model_domain(self):
        for mode in ['blocked','defending','unknown-combat','live-stack','source-modifier','x','target','extra-cost','alternative','prebound','optional-cost','unknown','override','no-pass','payment']:
            o,f=self.board();a=o['legalActions'][1];kwargs={}
            if mode=='blocked':o['state']['combat']['attackers'][0]['isBlocked']=True
            if mode=='defending':o['state']['combat']['attackers'][0]['attackingTarget']['playerId']='me'
            if mode=='unknown-combat':o['state']['combat']={}
            if mode=='live-stack':zone(o,'Stack','enemy',spell2={'name':'Unknown','controllerId':'enemy'})
            if mode=='source-modifier':o['state']['cards']['spell']['activeEffects']=['unknown']
            if mode=='x':a['hasXCost']=True
            if mode=='target':a['requiresTargets']=True
            if mode=='extra-cost':a['additionalCostInfo']={'costType':'Unknown'}
            if mode=='alternative':a['action']['alternativePayment']={'unknown':True}
            if mode=='prebound':a['action']['targets']=['enemy']
            if mode=='optional-cost':a['action']['optionalCostPayments']=['unreviewed']
            if mode=='unknown':d=deepcopy(f._document);d['cards']['Resource Spell'].pop('selfResourceDrawSpell');f=CardFacts(d)
            if mode=='override':kwargs['reaction_timing_exceptions']={'Resource Spell'}
            if mode=='no-pass':o['legalActions']=o['legalActions'][1:]
            if mode=='payment':o['nativePaymentError']='retry'
            b,_=bounded_observation(o,f,**kwargs);self.assertTrue(any(a['kind']=='CastSpell' for a in b['legalActions']),mode)
    def test_resource_spells_remain_available_in_main_and_end_windows(self):
        for step in ['PRECOMBAT_MAIN','POSTCOMBAT_MAIN','END','END_STEP']:
            o,f=self.board();o['state'].update(currentStep=step,combat=None,activePlayerId='enemy' if step in ['END','END_STEP'] else 'me')
            self.assertTrue(any(a['kind']=='CastSpell' for a in bounded_observation(o,f)[0]['legalActions']),step)
    def test_empty_self_draw_X_needs_a_positive_base_effect(self):
        for high in [0,1,3]:
            o,f=self.board();o['state'].update(currentStep='PRECOMBAT_MAIN',combat=None)
            d=deepcopy(f._document);d['cards']['Resource Spell']['emptySelfDrawAtZeroX']=True;f=CardFacts(d)
            a=o['legalActions'][1];a.update(hasXCost=True,minX=0,maxAffordableX=high);a['parameterSpec']['allowedFields']['xValue']='INTEGER'
            b,r=bounded_observation(o,f);casts=[a for a in b['legalActions'] if a['kind']=='CastSpell']
            if high:self.assertEqual(casts[0]['minX'],1)
            else:self.assertEqual(casts,[])
    def test_zero_X_override_and_uncertain_effects_keep_inference(self):
        for mode in ['unknown','override','stack','source-modifier','combat','fixed-x']:
            o,f=self.board();o['state'].update(currentStep='PRECOMBAT_MAIN',combat=None)
            d=deepcopy(f._document);d['cards']['Resource Spell']['emptySelfDrawAtZeroX']=True;f=CardFacts(d)
            a=o['legalActions'][1];a.update(hasXCost=True,minX=0,maxAffordableX=0);a['parameterSpec']['allowedFields']['xValue']='INTEGER';kwargs={}
            if mode=='unknown':d['cards']['Resource Spell'].pop('emptySelfDrawAtZeroX');f=CardFacts(d)
            if mode=='override':kwargs['reaction_timing_exceptions']={'Resource Spell'}
            if mode=='stack':zone(o,'Stack','enemy',onstack={'name':'Unknown','controllerId':'enemy'})
            if mode=='source-modifier':o['state']['cards']['spell']['activeEffects']=['unknown']
            if mode=='combat':o['state']['currentStep']='DECLARE_BLOCKERS'
            if mode=='fixed-x':a['action']['xValue']=0
            self.assertTrue(any(a['kind']=='CastSpell' for a in bounded_observation(o,f,**kwargs)[0]['legalActions']),mode)
    def test_new_optional_native_flags_are_strict_booleans(self):
        for field in ['selfResourceDrawSpell','emptySelfDrawAtZeroX']:
            for value in [None,[],{},'true',1]:
                o,f=self.board();d=deepcopy(f._document);d['cards']['Resource Spell'][field]=value
                with self.assertRaises(ValueError):CardFacts(d)


class SavedResourceSpellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).parent/'fixtures'
        cls.facts=CardFacts(json.load(gzip.open(root/'cycle-22-resource-facts.json.gz','rt')))
        cls.timing=json.load(gzip.open(root/'cycle-22-resource-timing.json.gz','rt'))
        cls.zero=json.load(gzip.open(root/'cycle-22-zero-draw.json.gz','rt'))
    def test_irrelevant_combat_passes_are_local_and_relevant_decisions_stay_open(self):
        from commander_gym.engine_deck_policy import EngineDeckPolicy
        self.assertEqual(len(self.timing['rows']),8)
        for row in self.timing['rows']:
            o=deepcopy(row['observation']);before=deepcopy(o);r=EngineDeckPolicy(self.facts,'talrand').try_choose(o)
            self.assertEqual(row['evidence']['nativeBaselineStatus'],'accepted')
            if row['sequence'] in {5438,5529,5970}:
                self.assertIsNotNone(r.choice,row['sequence']);self.assertEqual(r.choice.action_id,row['choice']['actionId'])
            else:self.assertIsNone(r.choice,row['sequence'])
            self.assertEqual(o,before)
    def test_actual_zero_draw_cast_is_outside_the_default_policy_menu(self):
        o=deepcopy(self.zero['observation']);before=deepcopy(o);b,r=bounded_observation(o,self.facts)
        self.assertEqual(self.zero['choice']['params']['xValue'],0)
        self.assertFalse(any(a['actionId']==self.zero['choice']['actionId'] for a in b['legalActions']))
        self.assertIn('hold-zero-X-empty-self-draw',[v['rule'] for v in r]);self.assertEqual(o,before)
