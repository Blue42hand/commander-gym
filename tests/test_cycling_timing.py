"""Ordinary cycling timing, using authentic native offers and explicit safety facts."""
from copy import deepcopy
from pathlib import Path
import gzip,json,unittest
from commander_gym.heuristic_facts import CardFacts
from commander_gym.policy_action_bounds import bounded_observation
from test_heuristic_policy import fact,facts,offer,observation,zone

class CyclingTimingTests(unittest.TestCase):
    def board(self):
        doc=facts().document()
        for f in doc['cards'].values():f['cyclingSensitive']=False
        doc['cards']['Cycler']=fact(types=['ENCHANTMENT'],cyclingSensitive=False)
        doc['cards']['Draw grant']=fact(types=['ENCHANTMENT'],cyclingSensitive=False,grantedFreeOptionalDraws={'draw-trigger':1})
        a=offer('CycleCard',1,card='cycle');a.update(requiresTargets=False,hasXCost=False)
        o=observation(offer('PassPriority',0),a);o['state'].update(currentStep='UPKEEP',activePlayerId='enemy',combat=None)
        for p in o['state']['players']:
            p['librarySize']=40
            p['manaPool']={c:0 for c in ['white','blue','black','red','green','colorless']}
            p['manaPool']['restrictedMana']=[]
        zone(o,'Hand','me',cycle={'name':'Cycler','controllerId':'me','activeEffects':[]})
        zone(o,'Battlefield','me',rock={'name':'Mana Rock','controllerId':'me','activeEffects':[]})
        zone(o,'Battlefield','enemy')
        zone(o,'Stack','me')
        return o,doc

    def test_ordinary_cycling_waits_in_quiet_and_known_draw_windows(self):
        for mode in ['quiet','draw-stack','irrelevant-combat','certified-badge']:
            o,d=self.board()
            if mode=='draw-stack':
                o['state']['cards']['draw']={'abilityIdentity':{'abilityId':'draw-trigger'},'activeEffects':[]}
                z=next(z for z in o['state']['zones'] if z['zoneId']['zoneType']=='Stack');z['cardIds']=['draw'];z['size']=1
            if mode=='irrelevant-combat':o['state']['players'].append({**o['state']['players'][1],'playerId':'third'});zone(o,'Battlefield','third');o['state'].update(currentStep='DECLARE_BLOCKERS',combat={'attackingPlayerId':'enemy','attackers':[{'creatureId':'other','attackingTarget':{'type':'Player','playerId':'third'}}]})
            if mode=='certified-badge':o['state']['cards']['rock']['activeEffects']=[{'drawNeutral':True,'effectId':'native-informational-badge'}]
            before=deepcopy(o);v,r=bounded_observation(o,CardFacts(d))
            self.assertEqual([a['kind'] for a in v['legalActions']],['PassPriority'])
            self.assertIn('cycle-in-main-or-opponent-end-step',[a['rule'] for a in r]);self.assertEqual(o,before)

    def test_uncertainty_relevant_combat_and_timing_overrides_keep_cycling(self):
        modes=['main','enemy-end','override','defending','blocked-attacker','unknown-combat','unknown-phase','unknown-active','unknown-stack','hidden-stack','incomplete-stack','missing-stack','floating-mana','restricted-mana','unknown-pool','missing-battlefield','player-effect','source-effect','source-sensitive','source-unknown','board-sensitive','board-unknown','board-modifier','unproven-badge','mixed-badges','face-down','unknown-library','library-risk','x','granted','typecycling','pending','payment','no-pass']
        for mode in modes:
            with self.subTest(mode=mode):
                o,d=self.board();kw={};a=o['legalActions'][1]
                if mode=='main':o['state'].update(activePlayerId='me',currentStep='PRECOMBAT_MAIN')
                elif mode=='enemy-end':o['state']['currentStep']='END'
                elif mode=='override':kw['reaction_timing_exceptions']={'Cycler'}
                elif mode in ['defending','blocked-attacker']:
                    o['state'].update(currentStep='DECLARE_BLOCKERS',combat={'attackingPlayerId':'enemy' if mode=='defending' else 'me','attackers':[{'creatureId':'other','isBlocked':True,'blockedBy':['blocker'],'attackingTarget':{'type':'Player','playerId':'me' if mode=='defending' else 'enemy'}}]})
                elif mode=='unknown-combat':o['state'].pop('combat')
                elif mode=='unknown-phase':o['state']['currentStep']='UNKNOWN'
                elif mode=='unknown-active':o['state']['activePlayerId']=None
                elif mode in ['unknown-stack','hidden-stack','incomplete-stack']:
                    z=o['state']['zones'][-1];z['cardIds']=['unknown'];z['size']=2 if mode=='incomplete-stack' else 1
                    z['isVisible']=mode!='hidden-stack';o['state']['cards']['unknown']={'name':'Unknown'}
                elif mode=='missing-stack':o['state']['zones'].pop()
                elif mode=='floating-mana':o['state']['players'][0]['manaPool']['colorless']=2
                elif mode=='restricted-mana':o['state']['players'][0]['manaPool']['restrictedMana']=[{'unknown':True}]
                elif mode=='unknown-pool':o['state']['players'][0].pop('manaPool')
                elif mode=='missing-battlefield':o['state']['zones']=[z for z in o['state']['zones'] if z['zoneId']!={'ownerId':'enemy','zoneType':'Battlefield'}]
                elif mode=='player-effect':o['state']['players'][0]['activeEffects']=['unknown']
                elif mode=='source-effect':o['state']['cards']['cycle']['activeEffects']=['unknown']
                elif mode=='source-sensitive':d['cards']['Cycler']['cyclingSensitive']=True
                elif mode=='source-unknown':d['cards']['Cycler'].pop('cyclingSensitive')
                elif mode=='board-sensitive':d['cards']['Mana Rock']['cyclingSensitive']=True
                elif mode=='board-unknown':d['cards']['Mana Rock'].pop('cyclingSensitive')
                elif mode=='board-modifier':o['state']['cards']['rock']['activeEffects']=['unknown']
                elif mode=='unproven-badge':o['state']['cards']['rock']['activeEffects']=[{'effectId':'condition_compare','description':'Only a count'}]
                elif mode=='mixed-badges':o['state']['cards']['rock']['activeEffects']=[{'drawNeutral':True},{'drawNeutral':None}]
                elif mode=='face-down':o['state']['cards']['rock']['isFaceDown']=True
                elif mode=='unknown-library':o['state']['players'][0].pop('librarySize')
                elif mode=='library-risk':o['state']['players'][0]['librarySize']=2
                elif mode=='x':a['hasXCost']=True
                elif mode=='granted':a['action']['grantedBy']='unknown'
                elif mode=='typecycling':a['kind']='TypecycleCard'
                elif mode=='pending':o['pendingDecision']={'type':'Unknown'}
                elif mode=='payment':o['nativePaymentError']='retry'
                elif mode=='no-pass':o['legalActions']=o['legalActions'][1:]
                before=deepcopy(o);v,_=bounded_observation(o,CardFacts(d),**kw)
                self.assertTrue(any(a['actionId']==1 for a in v['legalActions']));self.assertEqual(o,before)

    def test_cycling_sensitivity_is_an_optional_strict_boolean(self):
        for value in [None,[],{},1,'false']:
            o,d=self.board();d['cards']['Cycler']['cyclingSensitive']=value
            with self.assertRaises(ValueError):CardFacts(d)

class SavedCyclingTimingTests(unittest.TestCase):
    def test_same_native_accepted_passes_become_local_only_when_covered(self):
        from commander_gym.engine_deck_policy import EngineDeckPolicy
        root=Path(__file__).with_name('fixtures')
        facts=CardFacts(json.load(gzip.open(root/'cycle-30-native-facts.json.gz','rt')))
        rows=json.load(gzip.open(root/'cycle-30-cycling-windows.json.gz','rt'))['rows']
        local=[];deferred=[]
        for row in rows:
            o=row['observation'];before=deepcopy(o)
            self.assertTrue(row['evidence']['nativeMaskedMatch']);self.assertEqual(row['evidence']['nativeBaselineStatus'],'accepted')
            c=EngineDeckPolicy(facts,'talrand').try_choose(o).choice
            if c is not None:
                self.assertEqual(c.action_id,row['choice']['actionId']);self.assertEqual(dict(c.params),row['choice']['params']);local.append(row['sequence'])
            else:deferred.append(row['sequence'])
            self.assertEqual(o,before)
        self.assertGreaterEqual(len(local),20)
        self.assertTrue(deferred)
