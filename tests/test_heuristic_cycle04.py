"""Native recorded targeting/passing counterexamples, plus unknown-contract guards.

Public facts were exported from the matched native engine through the JVM
adapter. Recorded callbacks retain their original native domains.
"""
import gzip,json,unittest
from copy import deepcopy
from pathlib import Path
from commander_gym.heuristic_facts import CardFacts
from commander_gym.engine_deck_policy import EngineDeckPolicy
from commander_gym.policy_action_bounds import bounded_observation,removal_target_value
from commander_gym.bounded_policy_fallback import decision_context,BoundedPolicyFallback
from test_heuristic_policy import observation,offer,zone
from test_lmstudio import config,FakeTransport,completion

class CycleFourTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).parent/'fixtures'
        with gzip.open(root/'cycle-04-regressions.json.gz','rt') as f:cls.rows=json.load(f)
        with gzip.open(root/'cycle-04-public-facts.json.gz','rt') as f:cls.facts=CardFacts(json.load(f))
    def state(self,n):return deepcopy(self.rows[str(n)]['observation'])
    def test_exile_active_mana_engine_instead_of_already_frogged_commander(self):
        o=self.state(3775);before=deepcopy(o);b,r=bounded_observation(o,self.facts)
        self.assertEqual(b['pendingDecision']['legalTargets'],{'0':['e282']})
        self.assertEqual(EngineDeckPolicy(self.facts,'alela').try_choose(o).choice.response['selectedTargets'],{'0':['e282']})
        self.assertEqual(o,before)
    def test_arrest_does_not_remove_a_commanders_triggered_abilities(self):
        o=self.state(3775);frog=removal_target_value(o,'e100',self.facts)
        o['state']['cards']['e380']['name']='Arrest'
        self.assertGreater(removal_target_value(o,'e100',self.facts),frog+20)
        o['state']['cards']['e380']['name']="Faith's Fetters"
        self.assertGreater(removal_target_value(o,'e100',self.facts),frog+20)
    def test_unknown_source_and_complex_target_contracts_remain_unbounded(self):
        for change in ['source','constraint','multiple']:
            o=self.state(3775);p=o['pendingDecision'];original=deepcopy(p['legalTargets'])
            if change=='source':p['context']['sourceName']='Unreviewed exile effect'
            if change=='constraint':p['targetRequirements'][0]['sameOwner']=True
            if change=='multiple':p['targetRequirements'].append({**p['targetRequirements'][0],'index':1});p['legalTargets']['1']=list(p['legalTargets']['0']);original=deepcopy(p['legalTargets'])
            b,_=bounded_observation(o,self.facts)
            self.assertEqual(b['pendingDecision']['legalTargets'],original)
            self.assertIsNone(EngineDeckPolicy(self.facts,'alela').try_choose(o).choice)
    def test_ordinary_bounce_does_not_rebuy_own_bauble_without_a_plan(self):
        o=self.state(6372);b,r=bounded_observation(o,self.facts)
        for a in b['legalActions']:
            if o['state']['cards'].get(a['action'].get('cardId'),{}).get('name')=='Into the Roil':
                self.assertNotIn('e199',a['validTargets'])
        self.assertTrue(any(x['rule']=='opponent-removal-targets' for x in r))
    def test_bounce_can_rescue_own_disabled_commander(self):
        a=offer('CastSpell',1,card='spell');a.update(requiresTargets=True,minTargets=1,targetCount=1,validTargets=['commander','rock','enemy'])
        o=observation(a);zone(o,'Hand','me',spell={'name':'Into the Roil'})
        zone(o,'Battlefield','me',commander={'name':'Talrand, Sky Summoner','isCommander':True,'controllerId':'me','attachments':['frog']},rock={'name':'Arcane Signet','controllerId':'me'})
        zone(o,'Battlefield','enemy',frog={'name':'Amphibian Downpour','controllerId':'enemy'},enemy={'name':'Enemy body','controllerId':'enemy'})
        b,_=bounded_observation(o,self.facts);self.assertIn('commander',b['legalActions'][0]['validTargets']);self.assertNotIn('rock',b['legalActions'][0]['validTargets'])
    def test_quiet_reaction_windows_pass_locally_without_floating_mana(self):
        o=self.state(7373);b,r=bounded_observation(o,self.facts)
        self.assertTrue(any(x['rule']=='hold-reviewed-reaction-for-useful-window' for x in r))
        c=EngineDeckPolicy(self.facts,'sythis').try_choose(o).choice;self.assertIsNotNone(c)
        self.assertEqual(next(a['kind'] for a in o['legalActions'] if a['actionId']==c.action_id),'PassPriority')
    def test_main_end_step_combat_and_live_stack_retain_reaction_choices(self):
        for window in ['main','end','combat','stack','payment']:
            o=self.state(7373);s=o['state']
            if window=='main':s.update(activePlayerId=o['agentToAct'],currentStep='PRECOMBAT_MAIN')
            if window=='end':s['currentStep']='END_STEP'
            if window=='combat':s['currentStep']='DECLARE_ATTACKERS';s['combat']={'attackers':[{'creatureId':'e401'}]}
            if window=='stack':zone(o,'Stack','enemy',threat={'name':'Unknown threat','controllerId':'enemy'})
            if window=='payment':o['nativePaymentError']='Native payment correction'
            b,_=bounded_observation(o,self.facts)
            self.assertTrue(any(o['state']['cards'].get(a['action'].get('cardId'),{}).get('name')=='Stasis Snare' for a in b['legalActions']),window)
    def test_uncovered_competing_nonmana_action_still_reaches_model(self):
        o=self.state(7373);o['legalActions'].append(offer('ActivateAbility',999,card='mystery'))
        self.assertIsNone(EngineDeckPolicy(self.facts,'sythis').try_choose(o).choice)
    def test_deck_timing_override_survives_bounded_fallback(self):
        o=self.state(7373);p=EngineDeckPolicy(self.facts,'sythis');p.reaction_timing_exceptions=frozenset({'Stasis Snare'})
        r=p.try_choose(o);self.assertIsNone(r.choice)
        t=FakeTransport(completion(json.dumps({'option':0})));t.models['models'][0]['loaded_instances'][0]['config']['context_length']=131072
        c=BoundedPolicyFallback(config(max_prompt_bytes=122880,context_window=131072),t,facts=self.facts).choose_with_policy_context(o,[{'metadata':r.metadata}])
        self.assertEqual(c.metadata['boundedFallback']['mode'],'frozen-choices')
        self.assertIn('Stasis Snare',t.calls[-1][1]['messages'][1]['content'])
    def test_prompt_exposes_suppression_and_payable_sources_preserving_payment_context(self):
        o=self.state(3775);v=decision_context(o,frozen=True,policy_context=None,facts=self.facts)
        self.assertTrue(v['cards']['e100']['printedAbilitiesRemoved'])
        o=self.state(7373);before=deepcopy(o);v=decision_context(o,frozen=True,policy_context=None,facts=self.facts)
        self.assertIn('nativePaymentStatus',v);self.assertTrue(v['actorNativeManaSources'])
        actor=next(p for p in v['state']['players'] if p['playerId']==o['agentToAct']);self.assertNotIn('manaPool',actor);self.assertEqual(o,before)
        v=decision_context(o,frozen=False,policy_context=None,facts=self.facts);self.assertIn('manaPool',next(p for p in v['state']['players'] if p['playerId']==o['agentToAct']))
        player=next(p for p in o['state']['players'] if p['playerId']==o['agentToAct']);player['manaPool']['white']=2
        v=decision_context(o,frozen=True,policy_context=None,facts=self.facts);self.assertEqual(next(p for p in v['state']['players'] if p['playerId']==o['agentToAct'])['manaPool']['white'],2)

    def test_recorded_unfunded_attack_declines_and_cancels_retry(self):
        p=EngineDeckPolicy(self.facts,'lathril');o=self.state(7429)
        c=p.try_choose(o).choice;self.assertTrue(c.response['declined']);self.assertFalse(c.response['autoPay'])
        self.assertEqual(p.try_choose(o).choice.response,c.response)
        a=self.state(7407);self.assertEqual(p.try_choose(a).choice.params,{'attackers':{}})
        a['state']['turnNumber']+=4
        c=p.try_choose(a).choice;self.assertIsNotNone(c);self.assertTrue(c.params['attackers'])
        self.assertNotIn('policy-alela',c.params['attackers'].values())
    def test_unproven_unfunded_payment_defers(self):
        for change in ['unknown','source','waterbend','mandatory','floating','restricted']:
            o=self.state(7429);p=o['pendingDecision']
            if change=='unknown':p.pop('availableSources')
            if change=='source':p['availableSources']=[{'entityId':'e30'}]
            if change=='waterbend':p['waterbendPermanents']=['e17']
            if change=='mandatory':p['canDecline']=False
            actor=next(x for x in o['state']['players'] if x['playerId']==o['agentToAct'])
            if change=='floating':actor['manaPool']['green']=4
            if change=='restricted':actor['manaPool']['restrictedMana']=[{'amount':4}]
            c=EngineDeckPolicy(self.facts,'lathril').try_choose(o).choice
            if change=='floating':self.assertTrue(c.response['autoPay']);self.assertFalse(c.response['declined'])
            else:self.assertIsNone(c,change)
    def test_prison_bounds_and_landwalk_use_native_current_domains(self):
        from commander_gym.combat_policies import has_landwalk_lane
        o=self.state(7407);before=deepcopy(o);a=o['legalActions'][0]
        a['attackTargetsByAttacker']={i:list(a['validAttackTargets']) for i in a['validAttackers']}
        b,r=bounded_observation(o,self.facts);self.assertNotIn('policy-alela',b['legalActions'][0]['validAttackTargets'])
        self.assertTrue(all('policy-alela' not in ts for ts in b['legalActions'][0]['attackTargetsByAttacker'].values()))
        self.assertTrue(has_landwalk_lane(o,o['state']['cards']['e97'],'policy-sythis'))
        self.assertFalse(has_landwalk_lane(o,o['state']['cards']['e17'],'policy-sythis'))
        self.assertEqual(o['state'],before['state'])
