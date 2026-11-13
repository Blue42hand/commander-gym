"""Pure utility effects retain inference unless their lack of benefit is known."""
from copy import deepcopy
import gzip,json,unittest
from pathlib import Path
from commander_gym.heuristic_facts import CardFacts
from commander_gym.policy_action_bounds import bounded_observation
from test_heuristic_policy import observation,offer,zone,facts,fact

class UtilityPolicyTests(unittest.TestCase):
    def board(self,attack=False):
        d=deepcopy(facts()._document)
        key='opponentAttackRequirementAbilities' if attack else 'opponentEvasionRemovalAbilities'
        d['cards']['Utility']=fact(types=['ARTIFACT'],simplePermanent=False,evasionSensitive=False,**{key:['Utility:1']})
        d['cards']['Body']=fact(evasionSensitive=False)
        d['cards']['Untapper']=fact(simplePermanent=False,evasionSensitive=False,simpleTargetUntapAbilities=['Untapper:1'])
        a=offer('ActivateAbility',1);a['action'].update(sourceId='source',abilityId='Utility:1',paymentStrategy={'type':'AutoPay'})
        o=observation(offer('PassPriority',0),a)
        zone(o,'Battlefield','me',source={'name':'Utility','cardTypes':['ARTIFACT'],'controllerId':'me'})
        zone(o,'Battlefield','enemy',body={'name':'Body','cardTypes':['CREATURE'],'controllerId':'enemy','keywords':[],
            'hexproofFromColors':[],'hexproofFromMonocolored':False,'hexproofFromMulticolored':False})
        return o,CardFacts(d)

    def held(self,o,f,**kwargs):
        return any(r['rule'].startswith(('evasion-removal-needs','opponent-attack-requirement-needs')) for r in bounded_observation(o,f,**kwargs)[1])

    def test_no_protected_subject_is_held_even_during_known_untap_response(self):
        o,f=self.board();before=deepcopy(o);self.assertTrue(self.held(o,f));self.assertEqual(o,before)
        zone(o,'Stack','enemy',ability={'name':'Untap ability','abilityIdentity':{'cardDefinitionId':'Untapper','abilityId':'Untapper:1'}})
        self.assertTrue(self.held(o,f))

    def test_existing_or_possible_protection_and_missing_facts_retain_inference(self):
        for mode in ['hexproof','shroud','color','mono','multi','missing-color','missing-mono','unknown-card','changed-card','player-effect','future-grant','unknown-stack','changed-stack','missing-owner']:
            with self.subTest(mode=mode):
                o,f=self.board();c=o['state']['cards']['body'];d=deepcopy(f._document)
                if mode in ['hexproof','shroud']:c['keywords']=[mode.upper()]
                elif mode=='color':c['hexproofFromColors']=['BLUE']
                elif mode=='mono':c['hexproofFromMonocolored']=True
                elif mode=='multi':c['hexproofFromMulticolored']=True
                elif mode=='missing-color':c.pop('hexproofFromColors')
                elif mode=='missing-mono':c.pop('hexproofFromMonocolored')
                elif mode=='unknown-card':c['name']='Unknown'
                elif mode=='changed-card':c['activeEffects']=['unknown']
                elif mode=='player-effect':o['state']['players'][1]['activeEffects']=['unknown']
                elif mode=='future-grant':d['cards']['Body']['evasionSensitive']=True
                elif mode=='unknown-stack':zone(o,'Stack','enemy',spell={'name':'Unknown'})
                elif mode=='changed-stack':zone(o,'Stack','enemy',ability={'abilityIdentity':{'cardDefinitionId':'Untapper','abilityId':'Untapper:1'},'activeEffects':['unknown']})
                elif mode=='missing-owner':c.pop('controllerId')
                self.assertFalse(self.held(o,CardFacts(d)))

    def test_own_turn_attack_requirement_is_held_without_waiting_for_stack_empty(self):
        o,f=self.board(attack=True);zone(o,'Stack','enemy',spell={'name':'Unknown'})
        self.assertTrue(self.held(o,f))
        o['state']['activePlayerId']='enemy';self.assertFalse(self.held(o,f))
        o['state']['activePlayerId']='me';o['state']['players'][0]['teamSharedTurns']=True
        self.assertFalse(self.held(o,f))

    def test_native_correction_and_deck_override_keep_utility_domain(self):
        for attack in [False,True]:
            for mode in ['override','pending','correction','unknown-id','changed-source','no-pass']:
                with self.subTest(attack=attack,mode=mode):
                    o,f=self.board(attack);kwargs={}
                    if mode=='override':kwargs['reaction_timing_exceptions']={'Utility'}
                    elif mode=='pending':o['pendingDecision']={'kind':'Unknown'}
                    elif mode=='correction':o['nativePaymentError']='retry'
                    elif mode=='unknown-id':o['legalActions'][1]['action']['abilityId']='Utility:2'
                    elif mode=='changed-source':o['state']['cards']['source']['activeEffects']=['unknown']
                    elif mode=='no-pass':o['legalActions']=o['legalActions'][1:]
                    self.assertFalse(self.held(o,f,**kwargs))

    def test_authenticated_game_states(self):
        x=json.load(gzip.open(Path(__file__).with_name('fixtures')/'cycle-17-utility.json.gz','rt'))
        self.assertEqual(len(x['rows']),3);f=CardFacts(x['cardFacts'])
        for row in x['rows']:
            with self.subTest(sequence=row['sequence']):
                self.assertTrue(row['evidence']['nativeMaskedMatch'])
                view,_=bounded_observation(row['observation'],f)
                self.assertEqual(not any(a['actionId']==row['baseline']['actionId'] for a in view['legalActions']),row['expectedHeld'])
                from commander_gym.engine_deck_policy import EngineDeckPolicy
                choice=EngineDeckPolicy(f,'alela').try_choose(row['observation']).choice
                self.assertIsNotNone(choice)
                selected=next(a for a in row['observation']['legalActions'] if a['actionId']==choice.action_id)
                self.assertEqual(selected['kind'],'PassPriority')

    def test_unknown_tokens_remain_uncertain(self):
        o,f=self.board();o['state']['cards']['body'].update(name='Unknown Token',isToken=True,oracleText='',abilityFlags=[])
        self.assertFalse(self.held(o,f))

    def test_model_view_keeps_relevant_public_protection(self):
        from commander_gym.bounded_policy_fallback import decision_context
        o,f=self.board();o['state']['cards']['body'].update(protections=['GREEN'],hexproofFromColors=['BLUE'],hexproofFromMulticolored=True)
        v=decision_context(o,frozen=True,policy_context=None,facts=f)
        self.assertEqual(v['cards']['body']['protections'],['GREEN'])
        self.assertEqual(v['cards']['body']['hexproofFromColors'],['BLUE'])
        self.assertIs(v['cards']['body']['hexproofFromMulticolored'],True)
        self.assertNotIn('hexproofFromMonocolored',v['cards']['body'])

    def test_exact_utility_mechanics_are_grounded_in_model_options(self):
        from commander_gym.bounded_policy_fallback import enumerate_choices,_candidate_description
        for attack in [False,True]:
            o,f=self.board(attack)
            wires=enumerate_choices(o)
            descriptions=[_candidate_description(w,o,f) for w in wires]
            effect=next(d['reviewedEffects'] for d in descriptions if d['kind']=='ActivateAbility')
            self.assertEqual(effect['duration'],'end of turn')
            if attack:self.assertTrue(effect['opponentCreaturesAttackIfAble'])
            else:self.assertEqual(effect['opponentCreaturesLose'],['HEXPROOF','SHROUD'])

    def test_fully_projected_plain_tokens_are_known_but_unusual_tokens_defer(self):
        o,f=self.board();c=o['state']['cards']['body']
        c.update(name='Unknown Token',isToken=True,isFaceDown=False,oracleText='',abilityFlags=[],
                 cardFaces=[],isDoubleFaced=False,copyOf=None,keywords=['FLYING'])
        self.assertTrue(self.held(o,f))
        for change in [{'oracleText':'Whenever ...'}, {'keywords':['HEXPROOF']}, {'keywords':['UNKNOWN']},
                       {'isFaceDown':True}, {'cardFaces':[{}]}, {'isDoubleFaced':True},
                       {'copyOf':'known'}, {'abilityFlags':['UNKNOWN']}, {'activeEffects':['unknown']}]:
            other=deepcopy(o);other['state']['cards']['body'].update(change)
            with self.subTest(change=change):self.assertFalse(self.held(other,f))
