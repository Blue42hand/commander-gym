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
        grammar=t.calls[1][1]['response_format']['json_schema']['schema']
        evidence=grammar['properties']['explanation']['properties']['evidenceIds']['items']['enum']
        self.assertIn('spell',evidence);self.assertNotIn('d0',evidence);self.assertNotIn('policy.objectives',evidence)
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
            self.assertTrue(any(x['rule'] in {'opponent-removal-targets','development-waits-for-main','let-own-stack-resolve'} for x in r))
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

class RecordedImprovementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gzip
        from pathlib import Path
        from commander_gym.heuristic_facts import CardFacts
        root=Path(__file__).parent/'fixtures'
        cls.rows=json.load(gzip.open(root/'policy-improvements-recorded.json.gz','rt'))
        cls.facts=CardFacts.load(root/'competence-public-facts.json')

    def state(self,n):return deepcopy(self.rows[str(n)]['observation'])

    def test_upkeep_drain_and_unblocked_lethal(self):
        from commander_gym.lathril_policy import LathrilDeckSubsystem
        p=LathrilDeckSubsystem(self.facts);o=self.state(6076);r=p.try_choose(o)
        a=next(a for a in o['legalActions'] if a['actionId']==r.choice.action_id)
        paid=r.choice.params['tappedPermanents']
        self.assertEqual(a['action']['abilityId'],'Lathril, Blade of the Elves:2')
        self.assertEqual(len(set(paid)),10)
        self.assertTrue(set(paid)<=set(a['additionalCostInfo']['validTapTargets']))
        sick={i for i in a['additionalCostInfo']['validTapTargets'] if o['state']['cards'][i].get('hasSummoningSickness')}
        self.assertTrue(sick<=set(paid))
        o=self.state(7847);r=p.try_choose(o);attackers=r.choice.params['attackers']
        self.assertEqual(set(attackers.values()),{'policy-talrand'})
        self.assertGreaterEqual(sum(o['state']['cards'][i]['power'] for i in attackers),2)
        self.assertLessEqual(len(attackers),2)

    def test_ranger_search_and_discard_keep_development(self):
        from commander_gym.lathril_policy import LathrilDeckSubsystem
        from commander_gym.engine_deck_policy import EngineDeckPolicy
        o=self.state(7773);r=LathrilDeckSubsystem(self.facts).try_choose(o)
        self.assertEqual(o['pendingDecision']['cardInfo'][r.choice.response['selectedCards'][0]]['name'],'Quirion Ranger')
        o=self.state(3857);r=EngineDeckPolicy(self.facts,'talrand').try_choose(o)
        self.assertEqual(set(r.choice.response['selectedCards']),{'e105','e108'})

    def test_free_equip_and_upkeep_preserve_mana(self):
        from commander_gym.engine_deck_policy import EngineDeckPolicy
        o=self.state(7378);r=EngineDeckPolicy(self.facts,'alela').try_choose(o)
        a=next(a for a in o['legalActions'] if a['actionId']==r.choice.action_id)
        self.assertEqual(a['action']['abilityId'],'Lightning Greaves:1')
        self.assertEqual(r.choice.params,{'targets':['e300']})
        o=self.state(1722);r=EngineDeckPolicy(self.facts,'sythis').try_choose(o)
        self.assertEqual(next(a['kind'] for a in o['legalActions'] if a['actionId']==r.choice.action_id),'PassPriority')

    def test_removal_prefers_commander_and_beneficial_aura_stays_friendly(self):
        o=self.state(4431);b,_=bounded_observation(o,self.facts)
        a=next(a for a in b['legalActions'] if o['state']['cards'].get(a['action'].get('cardId'),{}).get('name')=='Darksteel Mutation')
        self.assertIn('e0',a['validTargets']);self.assertNotIn('e406',a['validTargets'])
        o=self.state(679);b,_=bounded_observation(o,self.facts)
        auras=[a for a in b['legalActions'] if o['state']['cards'].get(a['action'].get('cardId'),{}).get('name')=='Flickering Ward']
        self.assertTrue(all(o['state']['cards'][i]['controllerId']==o['agentToAct'] for a in auras for i in a['validTargets']))
        self.assertTrue(any(x['rule']=='beneficial-effect-own-creatures' for x in bounded_observation(o,self.facts)[1]))

    def test_generated_tribe_with_empty_board(self):
        from commander_gym.engine_deck_policy import EngineDeckPolicy
        o=decision('ChooseOptionDecision','OptionChosenResponse',{'optionIndex':'INTEGER'},options=['Human','Elf','Faerie'],prompt='Choose creature type',context={'sourceName':'Renewed Solidarity'})
        self.assertEqual(EngineDeckPolicy(self.facts,'alela').try_choose(o).choice.response['optionIndex'],2)

    def test_original_overflow_fits_with_explanations_and_numbered_choices(self):
        from test_lmstudio import discovery
        from test_engine_deck_policy import expand
        o=self.state('overflow');before=deepcopy(o)
        models=discovery();models['models'][0]['loaded_instances'][0]['config']['context_length']=131072
        summary={'goal':'Contract test','keyFacts':[],'evidenceIds':[],'uncertainty':'high','missingInformation':[]}
        t=FakeTransport(completion(json.dumps({'decision':{'option':0},'explanation':summary})),models=models)
        BoundedPolicyFallback(config(context_window=131072,max_prompt_bytes=32768,decision_explanations=True),t,facts=self.facts).choose_with_policy_context(o,[{'metadata':{'policyLayers':{'status':'deferred'}}}])
        req=t.calls[1][1]
        self.assertLess(len(json.dumps(req,ensure_ascii=False).encode()),30000)
        choices=expand(json.loads(req['messages'][1]['content']))['choices']
        self.assertEqual([c['option'] for c in choices],list(range(len(choices))))
        self.assertEqual(o,before)

class SelfSacrificeFallbackTests(unittest.TestCase):
    def test_recorded_seal_keeps_native_target_and_cost_out_of_model_output(self):
        from pathlib import Path
        from test_lmstudio import discovery
        from commander_gym.heuristic_facts import CardFacts
        from commander_gym.bounded_policy_fallback import enumerate_choices
        root=Path(__file__).parent/'fixtures';o=json.load(open(root/'seal-sacrifice-fallback.json'))
        f=CardFacts.load(root/'competence-public-facts.json');b,_=bounded_observation(o,f)
        wires=enumerate_choices(b)
        chosen=next(w for w in wires if w.get('choice',{}).get('params',{}).get('targets')==['e107'])
        self.assertEqual(chosen['choice']['params'],{'targets':['e107'],'sacrificedPermanents':['e251']})
        models=discovery();models['models'][0]['loaded_instances'][0]['config']['context_length']=131072
        t=FakeTransport(completion(json.dumps({'option':wires.index(chosen)})),models=models)
        r=BoundedPolicyFallback(config(context_window=131072,max_prompt_bytes=32768),t,facts=f).choose_with_policy_context(o,[{'metadata':{'policyLayers':{'status':'deferred'}}}])
        self.assertEqual(r.params,chosen['choice']['params'])
        self.assertEqual(r.metadata['boundedFallback']['mode'],'frozen-choices')
        a=next(a for a in b['legalActions'] if a.get('action',{}).get('abilityId')=='Seal of Primordium:1')
        a['additionalCostInfo']['alsoRequired']=[{'costType':'Discard'}]
        self.assertIsNone(enumerate_choices(b))

    def test_native_commander_recovery_default_can_be_overridden(self):
        from pathlib import Path
        from commander_gym.heuristic_facts import CardFacts
        root=Path(__file__).parent/'fixtures';o=json.load(open(root/'commander-return-fallback.json'))
        f=CardFacts.load(root/'competence-public-facts.json')
        self.assertTrue(DefaultPolicySubsystem(f).try_choose(o).choice.response['choice'])
        class GraveyardPlan(DefaultPolicySubsystem):
            def _deck_pending(self,o,p):return self._decision(p,'YesNoResponse','reviewed-graveyard-plan',choice=False)
        self.assertFalse(GraveyardPlan(f).try_choose(o).choice.response['choice'])
        o['pendingDecision']['context']['phase']='RESOLUTION'
        self.assertIsNone(DefaultPolicySubsystem(f).try_choose(o).choice)

class SparseTablePromptTests(unittest.TestCase):
    def test_recorded_turn26_fits_without_removing_cards_or_constraints(self):
        from pathlib import Path
        from test_lmstudio import discovery
        from test_engine_deck_policy import expand
        from commander_gym.heuristic_facts import CardFacts
        from commander_gym.bounded_policy_fallback import compact_decision_context
        root=Path(__file__).parent/'fixtures';o=json.load(open(root/'alela-overflow-sparse-tables.json'));f=CardFacts.load(root/'competence-public-facts.json')
        models=discovery();models['models'][0]['loaded_instances'][0]['config']['context_length']=131072
        summary={'goal':'Contract test','keyFacts':[],'evidenceIds':[],'uncertainty':'high','missingInformation':[]}
        t=FakeTransport(completion(json.dumps({'decision':{'option':0},'explanation':summary})),models=models)
        BoundedPolicyFallback(config(context_window=131072,max_prompt_bytes=32768,decision_explanations=True),t,facts=f).choose_with_policy_context(o,[{'metadata':{'policyLayers':{'status':'deferred'}}}])
        request=t.calls[1][1]
        self.assertLess(len(json.dumps(request,ensure_ascii=False).encode()),31000)
        visible={i for z in o['state']['zones'] if z.get('isVisible') and z['zoneId']['zoneType']!='Library' for i in z['cardIds']}
        self.assertEqual(set(expand(json.loads(request['messages'][1]['content']))['cards']),visible)
        view={'cards':{'one':{'name':'One','zone':'Hand','power':2},'two':{'name':'Two','zone':'Hand'},'three':{'name':'Three','zone':'Command','isCommander':True}}}
        self.assertEqual(expand(compact_decision_context(view)),view)

    def test_recorded_overflow_survives_when_compaction_cannot_meet_target(self):
        from pathlib import Path
        from unittest.mock import patch
        from test_lmstudio import discovery
        from commander_gym.heuristic_facts import CardFacts
        root=Path(__file__).parent/'fixtures';o=json.load(open(root/'alela-overflow-sparse-tables.json'));before=deepcopy(o)
        models=discovery();models['models'][0]['loaded_instances'][0]['config']['context_length']=131072
        summary={'goal':'Contract test','keyFacts':[],'evidenceIds':[],'uncertainty':'high','missingInformation':[]}
        t=FakeTransport(completion(json.dumps({'decision':{'option':0},'explanation':summary})),models=models)
        # Simulate a future state whose complete representation cannot compact
        # below the target. Preserve the real recorded cards and offered choices.
        with patch('commander_gym.bounded_policy_fallback.compact_decision_context',side_effect=lambda view:view):
            r=BoundedPolicyFallback(config(context_window=131072,max_prompt_bytes=122880,decision_explanations=True),t,facts=CardFacts.load(root/'competence-public-facts.json')).choose_with_policy_context(o,[{'metadata':{'policyLayers':{'status':'deferred'}}}])
        meta=r.metadata['boundedFallback']
        self.assertGreater(meta['promptBytes'],32768);self.assertTrue(meta['promptTargetExceeded'])
        self.assertEqual(meta['maxPromptBytes'],122880);self.assertEqual(o,before)
        self.assertEqual(sum(path=='/v1/chat/completions' for path,_,_ in t.calls),1)

class CurrentTargetRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gzip
        from pathlib import Path
        from commander_gym.heuristic_facts import CardFacts
        root=Path(__file__).parent/'fixtures';cls.states=json.load(gzip.open(root/'policy-target-mistakes.json.gz','rt'));cls.facts=CardFacts.load(root/'competence-public-facts.json')

    def test_sage_targets_opponents_and_declines_forced_self_destruction(self):
        o=deepcopy(self.states['7763']['observation']);b,_=bounded_observation(o,self.facts)
        self.assertNotIn('e71',b['pendingDecision']['legalTargets']['0'])
        self.assertTrue(b['pendingDecision']['legalTargets']['0'])
        o['pendingDecision']['legalTargets']['0']=['e71']
        self.assertEqual(bounded_observation(o,self.facts)[0]['pendingDecision']['legalTargets']['0'],['e71'])
        self.assertFalse(DefaultPolicySubsystem(self.facts).try_choose(self.states['7798']['observation']).choice.response['choice'])

    def test_timberwatch_uses_own_targets(self):
        o=next(r['observation'] for r in self.states.values() if any(a.get('action',{}).get('abilityId')=='Timberwatch Elf:1' for a in r['observation']['legalActions']))
        o=deepcopy(o);o['state']['currentStep']='PRECOMBAT_MAIN'
        b,_=bounded_observation(o,self.facts);a=next(a for a in b['legalActions'] if a.get('action',{}).get('abilityId')=='Timberwatch Elf:1')
        self.assertNotIn('e264',a['validTargets']);self.assertTrue(a['validTargets'])
        self.assertTrue(all(o['state']['cards'][t].get('controllerId')==o['agentToAct'] for t in a['validTargets']))

    def test_inactive_history_is_summarized_but_candidates_keep_rules(self):
        from commander_gym.bounded_policy_fallback import decision_context
        a=offer('CastSpell',1,card='spell');a.update(requiresTargets=True,targetCount=1,minTargets=1,validTargets=['candidate']);o=observation(a)
        zone(o,'Hand','me',spell={'name':'Unknown','oracleText':'Current choice rules'})
        zone(o,'Battlefield','enemy',live={'name':'Engine','oracleText':'Live board rules'})
        zone(o,'Graveyard','enemy',inactive={'name':'Old','oracleText':'Inactive rules'},candidate={'name':'Target','oracleText':'Candidate rules'})
        before=deepcopy(o);v=decision_context(o,frozen=True,policy_context=None,facts=self.facts)
        self.assertNotIn('oracleText',v['cards']['inactive']);self.assertEqual(v['summarizedInactiveCards'],['inactive'])
        for i in ['spell','live','candidate']:self.assertIn('oracleText',v['cards'][i])
        self.assertEqual(o,before)
