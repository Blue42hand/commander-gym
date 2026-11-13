"""Basic tapped-land development waits for deliberate resource windows."""
from copy import deepcopy
from pathlib import Path
import gzip,json,unittest
from commander_gym.heuristic_facts import CardFacts
from commander_gym.policy_action_bounds import bounded_observation
from test_heuristic_policy import facts,fact,offer,observation,zone

class LandRampTimingTests(unittest.TestCase):
    def board(self):
        doc=deepcopy(facts()._document)
        doc['cards']['Land engine']=fact(types=['ARTIFACT'],selfTappedLandRampAbilities=['Land engine:1'],simplePermanent=False)
        doc['cards']['Reorder']=fact(types=['ENCHANTMENT'],freeOptionalLibraryReorders={'native-reorder':3})
        a=offer('ActivateAbility',1);a['action'].update(sourceId='ramp',abilityId='Land engine:1',paymentStrategy={'type':'AutoPay'})
        a['additionalCostInfo']={'costType':'SacrificeSelf','sacrificeCount':1,'validSacrificeTargets':['ramp']}
        o=observation(offer('PassPriority',0),a);o['state'].update(currentStep='UPKEEP',activePlayerId='enemy',combat=None)
        zone(o,'Battlefield','me',ramp={'name':'Land engine','controllerId':'me','cardTypes':['ARTIFACT'],'activeEffects':[]})
        return o,CardFacts(doc)

    def test_quiet_and_certified_library_reorder_windows_use_local_pass(self):
        for mode in ['quiet','reorder','noncreature-combat']:
            o,f=self.board();before=deepcopy(o)
            if mode=='reorder':zone(o,'Stack','enemy',look={'name':'Reorder trigger','controllerId':'enemy','abilityIdentity':{'abilityId':'native-reorder'}})
            elif mode=='noncreature-combat':o['state'].update(currentStep='DECLARE_BLOCKERS',combat={'attackers':[{'creatureId':'attacker'}]})
            v,r=bounded_observation(o,f)
            self.assertEqual([a['kind'] for a in v['legalActions']],['PassPriority'])
            self.assertIn('tapped-land-ramp-in-main-or-opponent-end-step',[q['rule'] for q in r])
            if mode=='quiet':self.assertEqual(o,before)

    def test_main_end_step_overrides_and_unknown_or_live_creature_combat_keep_choice(self):
        for mode in ['main','end','override','creature-combat','unknown-combat','missing-combat','unknown-stack','hidden-stack','modified','pending','payment','unknown-ability','unknown-phase','unknown-active']:
            with self.subTest(mode=mode):
                o,f=self.board();kw={}
                if mode=='main':o['state'].update(activePlayerId='me',currentStep='PRECOMBAT_MAIN')
                elif mode=='end':o['state']['currentStep']='END'
                elif mode=='override':kw['reaction_timing_exceptions']={'Land engine'}
                elif mode=='creature-combat':
                    o['state'].update(currentStep='DECLARE_BLOCKERS',combat={'attackers':[{'creatureId':'attacker'}]});o['state']['cards']['ramp']['cardTypes']=['CREATURE']
                elif mode=='unknown-combat':o['state']['combat']={}
                elif mode=='missing-combat':o['state'].pop('combat')
                elif mode in {'unknown-stack','hidden-stack'}:
                    zone(o,'Stack','enemy',threat={'name':'Unknown','controllerId':'enemy'})
                    if mode=='hidden-stack':o['state']['zones'][-1]['isVisible']=False
                elif mode=='modified':o['state']['cards']['ramp']['activeEffects']=['unknown']
                elif mode=='pending':o['pendingDecision']={'type':'Unknown'}
                elif mode=='payment':o['nativePaymentError']='retry'
                elif mode=='unknown-ability':o['legalActions'][1]['action']['abilityId']='unknown'
                elif mode=='unknown-phase':o['state']['currentStep']='UNKNOWN'
                elif mode=='unknown-active':o['state']['activePlayerId']=None
                before=deepcopy(o);v,_=bounded_observation(o,f,**kw)
                self.assertTrue(any(a['actionId']==1 for a in v['legalActions']))
                self.assertEqual(o,before)

    def test_pure_draw_and_mixed_reorder_stack_wait_but_unknown_effects_defer(self):
        for mode in ['direct','granted','mixed','unknown','modified','hidden','incomplete','creature-combat','override']:
            with self.subTest(mode=mode):
                o,f=self.board();doc=f.document()
                doc['cards']['Draw']=fact(types=['ENCHANTMENT'],freeOptionalDraws={'native-draw':1},grantedFreeOptionalDraws={'native-granted-draw':1})
                f=CardFacts(doc);o['state'].update(currentStep='COMBAT_DAMAGE',combat={'attackers':[{'creatureId':'attacker'}]})
                zone(o,'Stack','me',trigger={'name':'Draw trigger','controllerId':'me','abilityIdentity':{'abilityId':'native-granted-draw' if mode=='granted' else 'native-draw'}})
                if mode=='mixed':
                    o['state']['cards']['reorder']={'abilityIdentity':{'abilityId':'native-reorder'}}
                    o['state']['zones'][-1]['cardIds'].append('reorder');o['state']['zones'][-1]['size']=2
                elif mode=='unknown':o['state']['cards']['trigger']['abilityIdentity']['abilityId']='unknown'
                elif mode=='modified':o['state']['cards']['trigger']['activeEffects']=['unknown']
                elif mode=='hidden':o['state']['zones'][-1]['isVisible']=False
                elif mode=='incomplete':o['state']['zones'][-1]['size']=2
                elif mode=='creature-combat':o['state']['cards']['ramp']['cardTypes']=['CREATURE']
                before=deepcopy(o);kw={'reaction_timing_exceptions':{'Land engine'}} if mode=='override' else {}
                v,_=bounded_observation(o,f,**kw)
                self.assertEqual(any(a['actionId']==1 for a in v['legalActions']),mode not in {'direct','granted','mixed'})
                self.assertEqual(o,before)

    def test_native_contract_requires_a_list_of_ability_ids(self):
        for value in [None,{},True,[1],['']]:
            o,f=self.board();doc=deepcopy(f._document);doc['cards']['Land engine']['selfTappedLandRampAbilities']=value
            with self.assertRaises(ValueError):CardFacts(doc)

class SavedLandRampTimingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).with_name('fixtures')
        cls.facts=CardFacts(json.load(gzip.open(root/'cycle-26-native-facts.json.gz','rt')))
        cls.rows=json.load(gzip.open(root/'cycle-26-ramp-windows.json.gz','rt'))['rows']

    def test_same_accepted_passes_are_local_outside_development_and_end_step(self):
        from commander_gym.engine_deck_policy import EngineDeckPolicy
        seen=[]
        for row in self.rows:
            o=row['observation'];before=deepcopy(o)
            self.assertTrue(row['evidence']['nativeMaskedMatch']);self.assertEqual(row['evidence']['nativeBaselineStatus'],'accepted')
            c=EngineDeckPolicy(self.facts,'talrand').try_choose(o).choice
            if row['sequence']==13353:self.assertIsNone(c)
            else:
                self.assertIsNotNone(c,row['sequence']);self.assertEqual(c.action_id,row['choice']['actionId']);self.assertEqual(dict(c.params),row['choice']['params']);seen.append(row['sequence'])
            self.assertEqual(o,before)
        self.assertEqual(len(seen),10)

class SavedDrawStackTimingTests(unittest.TestCase):
    def test_authentic_accepted_passes_and_own_main_boundary(self):
        from commander_gym.engine_deck_policy import EngineDeckPolicy
        root=Path(__file__).with_name('fixtures')
        f=CardFacts(json.load(gzip.open(root/'cycle-26-native-facts.json.gz','rt')))
        rows=json.load(gzip.open(root/'cycle-27-draw-stack-windows.json.gz','rt'))['rows']
        self.assertEqual({r['sequence'] for r in rows},{3937,4042,4077})
        for row in rows:
            o=row['observation'];before=deepcopy(o)
            self.assertTrue(row['evidence']['nativeMaskedMatch'])
            self.assertEqual(row['evidence']['nativeBaselineStatus'],'accepted')
            c=EngineDeckPolicy(f,'talrand').try_choose(o).choice
            if row['sequence']==3937:self.assertIsNone(c)
            else:
                self.assertIsNotNone(c)
                self.assertEqual(c.action_id,row['choice']['actionId'])
                self.assertEqual(dict(c.params),row['choice']['params'])
            self.assertEqual(o,before)
