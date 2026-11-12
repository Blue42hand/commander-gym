"""Regression contracts for costly actions, target ownership and diagnostic summaries."""
from copy import deepcopy
import json
import unittest
from commander_gym.action_contracts import validate_target_params,constrain_target_params
from commander_gym.policy_action_bounds import bounded_observation
from commander_gym.bounded_policy_fallback import BoundedPolicyFallback
from commander_gym.default_policies import DefaultPolicySubsystem
from commander_gym.lmstudio_pilot import LMStudioPilotError
from test_heuristic_policy import observation,offer,zone,facts,decision
from test_lmstudio import config,FakeTransport,completion


class CompetenceBoundsTests(unittest.TestCase):
    def test_opening_explanation_can_reference_masked_mulligan_cards(self):
        o=observation(offer('KeepHand',0),offer('TakeMulligan',1))
        o['state']['mulligan']={'hand':['opening-card'],'cards':{'opening-card':{'name':'Forest','typeLine':'Basic Land — Forest'}},'mulliganCount':0,'cardsToPutOnBottom':0,'isOnThePlay':False}
        summary={'goal':'Improve opening mana','keyFacts':['Only one land is available.'],'evidenceIds':['opening-card'],'uncertainty':'low','missingInformation':[]}
        t=FakeTransport(completion(json.dumps({'decision':{'option':1},'explanation':summary})))
        result=BoundedPolicyFallback(config(decision_explanations=True),t,facts=facts()).choose_with_policy_context(o,[{'metadata':{'policyLayers':{'status':'deferred'}}}])
        self.assertEqual(result.action_id,1)
        self.assertEqual(result.metadata['boundedFallback']['decisionExplanation']['evidenceIds'],['opening-card'])
        self.assertEqual(sum(path=='/v1/chat/completions' for path,_,_ in t.calls),1)

    def test_target_cost_does_not_satisfy_required_target(self):
        a={'requiresTargets':True,'minTargets':1,'targetCount':1,'validTargets':['target']}
        for params in [{'bouncedPermanents':['elf']},{'targets':[]},{'targets':['other']},{'targets':['target','target']}, {'targets':['target'],'targetGroups':[['target']]}]:
            with self.assertRaises(ValueError):validate_target_params(params,a)
        for params in [{'targets':['target'],'bouncedPermanents':['elf']},{'targetGroups':[['target']]}]:validate_target_params(params,a)
        a['minTargets']=0;validate_target_params({},a)
        schema=constrain_target_params({'type':'object','properties':{'targets':{}}},a)
        self.assertNotIn('anyOf',schema)

    def test_removal_bounds_keep_enemy_and_unknown_ownership(self):
        a=offer('CastSpell',1,card='spell');a.update(requiresTargets=True,targetCount=1,minTargets=1,validTargets=['own','enemy','unknown'])
        o=observation(a);zone(o,'Hand','me',spell={'name':'Reality Shift'})
        o['state']['cards'].update(own={'controllerId':'me'},enemy={'controllerId':'enemy'})
        before=deepcopy(o);bounded,r=bounded_observation(o,facts())
        self.assertEqual(bounded['legalActions'][0]['validTargets'],['enemy','unknown']);self.assertTrue(r);self.assertEqual(o,before)
        o['state']['cards']['spell']['name']='Unreviewed effect'
        self.assertEqual(bounded_observation(o,facts())[0]['legalActions'][0]['validTargets'],a['validTargets'])

    def test_autopay_keeps_enabling_mana_without_affordable_cast(self):
        a=offer('CastSpell',1,card='spell');a['action']['paymentStrategy']={'type':'AutoPay'}
        m=offer('ActivateAbility',2,mana=True);o=observation(a,m)
        self.assertEqual(len(bounded_observation(o,facts())[0]['legalActions']),1)
        a.update(affordable=False,isAffordable=False)
        self.assertEqual(len(bounded_observation(o,facts())[0]['legalActions']),2)

    def test_forced_empty_blocking_is_local(self):
        a=offer('DeclareBlockers',1);a.update(validBlockers=[],validBlockTargets={});a['parameterSpec']['allowedFields']={'blockers':'ENTITY_ID_ARRAY_MAP'}
        r=DefaultPolicySubsystem(facts()).try_choose(observation(a))
        self.assertEqual(r.choice.params,{'blockers':{}})
        a['mandatoryBlockerAssignments']={'x':['y']}
        self.assertIsNone(DefaultPolicySubsystem(facts()).try_choose(observation(a)).choice)

    def test_brief_explanation_is_metadata_only_and_unknown_evidence_repairs(self):
        a=offer('CastSpell',1,card='spell');o=observation(a);zone(o,'Hand','me',spell={'name':'Unknown spell'})
        summary={'goal':'Develop a resource','keyFacts':['The spell is affordable.'],'evidenceIds':['spell'],'uncertainty':'medium','missingInformation':['Whether opponents can respond']}
        bad=deepcopy(summary);bad['evidenceIds']=['invented']
        t=FakeTransport(completion(json.dumps({'decision':{'option':0},'explanation':bad})),completion(json.dumps({'decision':{'option':0},'explanation':summary})))
        p=BoundedPolicyFallback(config(decision_explanations=True),t,facts=facts())
        result=p.choose_with_policy_context(o,[{'metadata':{'policyLayers':{'status':'deferred'}}}])
        self.assertEqual(result.action_id,1);self.assertEqual(result.params,{})
        self.assertEqual(result.metadata['boundedFallback']['decisionExplanation'],summary)
        self.assertEqual(sum(path=='/v1/chat/completions' for path,_,_ in t.calls),2)
        self.assertNotIn('Develop a resource',t.calls[-1][1]['messages'][1]['content'])
        summary['goal']='x'*181
        t=FakeTransport(completion(json.dumps({'decision':{'option':0},'explanation':summary})))
        with self.assertRaises(LMStudioPilotError):BoundedPolicyFallback(config(decision_explanations=True,max_attempts=1),t,facts=facts()).choose_with_policy_context(o,[{'metadata':{'policyLayers':{'status':'deferred'}}}])


class RecordedCompetenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gzip
        from pathlib import Path
        from commander_gym.heuristic_facts import CardFacts
        root=Path(__file__).parent/'fixtures'
        cls.rows={r['sequence']:r for r in map(json.loads,gzip.open(root/'competence-recorded-states.jsonl.gz','rt'))}
        cls.facts=CardFacts.load(root/'competence-public-facts.json')

    def test_original_failed_activation_requires_target_independently_of_bounce(self):
        from commander_gym.openai_responses_pilot import _validate_native_action_params,OpenAIResponsesPilotError
        o=self.rows[6273]['observation'];a=next(a for a in o['legalActions'] if a.get('action',{}).get('abilityId')=='Wirewood Symbiote:1')
        with self.assertRaises(OpenAIResponsesPilotError):_validate_native_action_params({'bouncedPermanents':['e0']},a)
        _validate_native_action_params({'bouncedPermanents':['e34'],'targets':['e0']},a)
        b,r=bounded_observation(o,self.facts)
        self.assertFalse(any(x['semanticId']==a['semanticId'] for x in b['legalActions']))
        o=deepcopy(o);zone(o,'Stack','enemy',threat={'name':'Unknown threat'})
        b,_=bounded_observation(o,self.facts)
        self.assertTrue(any(x['semanticId']==a['semanticId'] for x in b['legalActions']))

    def test_recorded_removal_and_autopay_actions_are_bounded(self):
        for n,target in [(4138,'e152'),(5237,'e287')]:
            o=self.rows[n]['observation'];b,r=bounded_observation(o,self.facts)
            self.assertTrue(any(x['rule']=='opponent-removal-targets' for x in r))
            for a in b['legalActions']:
                if a.get('requiresTargets') and a.get('targetRequirements') is None:
                    name=o['state']['cards'].get(a['action'].get('cardId',a['action'].get('sourceId')),{}).get('name')
                    if name in {'Reality Shift','Seal of Primordium'}:self.assertNotIn(target,a['validTargets'])
        o=self.rows[3025]['observation'];b,r=bounded_observation(o,self.facts)
        self.assertTrue(any(x['rule']=='native-autopay-before-manual-mana' for x in r))
        self.assertFalse(any(a.get('isManaAbility') for a in b['legalActions']))

    def test_empty_zenith_and_planned_grove_search(self):
        o=self.rows[169]['observation'];b,r=bounded_observation(o,self.facts)
        self.assertTrue(any(x['rule']=='search-without-eligible-payoff' for x in r))
        from commander_gym.engine_deck_policy import EngineDeckPolicy
        o=self.rows[5062]['observation'];r=EngineDeckPolicy(self.facts,'sythis').try_choose(o)
        plan=r.metadata['utilityPlans'][0]
        self.assertEqual(plan['preferredCard'],"Enchantress's Presence")
        from commander_gym.engine_deck_policy import EngineDeckPolicy
        o=self.rows[5097]['observation'];result=EngineDeckPolicy(self.facts,'sythis').try_choose(o)
        selected=result.choice.response['selectedCards'];self.assertEqual(len(selected),1)
        self.assertEqual(o['pendingDecision']['cardInfo'][selected[0]]['name'],plan['preferredCard'])

    def test_landfall_sequence_and_empty_blocks_stay_local(self):
        from commander_gym.engine_deck_policy import EngineDeckPolicy
        p=EngineDeckPolicy(self.facts,'sythis')
        o=self.rows[3732]['observation'];r=p.try_choose(o);self.assertIsNotNone(r.choice)
        a=next(a for a in o['legalActions'] if a['actionId']==r.choice.action_id)
        self.assertEqual(o['state']['cards'][a['action']['cardId']]['name'],'Felidar Retreat')
        o=self.rows[3795]['observation'];self.assertIsNotNone(p.try_choose(o).choice)
        r=p.try_choose(self.rows[2346]['observation']);self.assertEqual(r.choice.params,{'blockers':{}})
        r=p.try_choose(self.rows[3802]['observation']);self.assertEqual(r.choice.response['optionIndex'],0)

    def test_commander_and_painland_mana_use_owner_and_life(self):
        p=DefaultPolicySubsystem(self.facts);o=observation()
        o['state']['cards']['commander']={'name':'Alela, Artful Provocateur','isCommander':True,'ownerId':'me'}
        self.assertEqual(p._land_colors({'name':'Command Tower'},o),set('WUB'))
        o['state']['cards']['commander']['ownerId']='enemy'
        self.assertEqual(p._land_colors({'name':'Command Tower'},o),set())
        self.assertEqual(p._land_colors({'name':'Adarkar Wastes'},o),set('WUC'))
        o['state']['players'][0]['life']=2
        self.assertEqual(p._land_colors({'name':'Adarkar Wastes'},o),{'C'})
