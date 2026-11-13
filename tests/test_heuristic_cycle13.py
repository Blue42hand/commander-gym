"""Schema-directed retry feedback must not echo invalid model text or grow history."""
import gzip,json,unittest
from copy import deepcopy
from pathlib import Path
from commander_gym.lmstudio_pilot import LMStudioPilot,_missing_required_paths,_retry_input
from test_lmstudio import FakeTransport,completion,config

class RetryDiagnosticsThirteenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).parent/'fixtures'
        cls.row=json.load(gzip.open(root/'cycle-13-regressions.json.gz','rt'))
        cls.observation=json.load(gzip.open(root/'cycle-12-regressions.json.gz','rt'))['game18-failed-syncopate']['observation']
    def test_actual_failed_syncopate_schema_identifies_required_x_without_echoing_explanation(self):
        paths=_missing_required_paths(self.row['schema']['schema'],self.row['invalidOutput'])
        self.assertEqual(paths,['decision.choice.params.xValue'])
        invalid=deepcopy(self.row['invalidOutput']);invalid['explanation']['goal']='private synthetic reasoning and opponent-secret'
        valid=deepcopy(invalid);valid['decision']['choice']['params']['xValue']=1
        t=FakeTransport(completion(json.dumps(invalid)),completion(json.dumps(valid)))
        t.models['models'][0]['loaded_instances'][0]['config']['context_length']=131072
        cfg=config(context_window=131072,max_prompt_bytes=122880)
        o=deepcopy(self.observation);before=deepcopy(o)
        c=LMStudioPilot(cfg,transport=t)._choose(o,response_format=self.row['schema'],model_view=self.row['modelView'],output_decoder=lambda p:json.dumps(p['decision']))
        self.assertEqual(c.params['xValue'],1);self.assertEqual(c.metadata['retryCount'],1);self.assertEqual(o,before)
        requests=[body for path,body,_ in t.calls if path=='/v1/chat/completions']
        self.assertEqual(len(requests),2)
        self.assertNotIn('Missing required fields',requests[0]['messages'][1]['content'])
        retry=requests[1]['messages'][1]['content'];self.assertIn('Missing required fields: decision.choice.params.xValue.',retry)
        self.assertNotIn('private synthetic reasoning',retry);self.assertNotIn('opponent-secret',retry)
        self.assertEqual(retry.count('Previous output was invalid'),1)
    def test_ambiguous_or_mismatched_union_does_not_invent_missing_branch_fields(self):
        schema={'anyOf':[{'type':'object','properties':{'kind':{'const':'A'}},'required':['a']},
                         {'type':'object','properties':{'kind':{'const':'B'}},'required':['b']}]}
        self.assertEqual(_missing_required_paths(schema,{'kind':'A'}),['a'])
        for item in [{},{'kind':'unknown'},None]:self.assertEqual(_missing_required_paths(schema,item),[])
    def test_retries_replace_the_note_and_never_drop_current_context_or_exceed_budget(self):
        schema={'type':'object','properties':{'requiredField':{'type':'integer'}},'required':['requiredField']}
        request={'messages':[{'role':'system','content':'policy'},{'role':'user','content':'current legal domain'}]}
        original=deepcopy(request);base=request['messages'][1]['content']
        _retry_input(request,base,schema,{},10000);first=deepcopy(request)
        _retry_input(request,base,schema,{},10000);self.assertEqual(request,first)
        self.assertEqual(request['messages'][1]['content'].count('requiredField'),1)
        limit=len(json.dumps(original,ensure_ascii=False).encode())
        _retry_input(request,base,schema,{},limit);self.assertEqual(request,original)
        self.assertLessEqual(len(json.dumps(request,ensure_ascii=False).encode()),limit)
    def test_missing_field_diagnostics_are_bounded_for_large_schema_and_input(self):
        schema={'type':'object','properties':{},'required':['field'+str(i) for i in range(2000)]}
        paths=_missing_required_paths(schema,{})
        self.assertLessEqual(len(paths),8)
        self.assertTrue(all(len(p)<=160 for p in paths))

class CompleteModalPlanThirteenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).parent/'fixtures';cls.rows=json.load(gzip.open(root/'cycle-13-regressions.json.gz','rt'))['modalLoop']
        from commander_gym.heuristic_facts import CardFacts
        cls.facts=CardFacts(json.load(gzip.open(root/'cycle-12-public-facts.json.gz','rt')))
    def state(self):return deepcopy(self.rows['8564']['observation'])
    def action(self,o):return next(a for a in o['legalActions'] if a.get('kind')=='CastSpellModal')
    def test_real_loop_is_replaced_by_complete_commander_restoration_plan_for_all_decks(self):
        from commander_gym.default_policies import DefaultPolicySubsystem
        from commander_gym.engine_deck_policy import EngineDeckPolicy
        for p in [DefaultPolicySubsystem(self.facts),EngineDeckPolicy(self.facts,'talrand')]:
            o=self.state();before=deepcopy(o);r=p.try_choose(o)
            self.assertEqual(r.choice.action_id,1);self.assertEqual(r.choice.params,{})
            self.assertEqual(r.choice.metadata['heuristic']['reason'],'shared-modal-complete-commander-restoration-plan')
            self.assertEqual(o,before)
            for seq in ['8571','8578']:
                pending=deepcopy(self.rows[seq]['observation']);choice=p.try_choose(pending).choice
                self.assertEqual(pending['pendingDecision']['options'][choice.response['optionIndex']],"Return target permanent to its owner's hand")
    def test_frozen_card_choice_displays_both_native_modes_and_exact_target(self):
        from commander_gym.bounded_policy_fallback import enumerate_choices,_candidate_description
        from commander_gym.policy_action_bounds import bounded_observation
        o=self.state();b,_=bounded_observation(o,self.facts);wires=enumerate_choices(b);self.assertIsNotNone(wires)
        w=next(w for w in wires if w['choice']['semanticId']==self.action(o)['semanticId'])
        self.assertEqual(w['choice']['params'],{})
        details=_candidate_description(w,b,self.facts);self.assertEqual(len(details['modePlan']),2)
        self.assertEqual(details['modePlan'][0]['target']['entity'],'e356')
        self.assertEqual(details['modePlan'][1]['effect'],'Draw a card')
    def test_target_execution_rechecks_the_current_native_slot_and_source(self):
        from commander_gym.engine_deck_policy import EngineDeckPolicy
        from commander_gym.modal_policies import modal_pending
        p=EngineDeckPolicy(self.facts,'talrand');p.try_choose(self.state())
        o=deepcopy(self.rows['8578']['observation']);pending={'kind':'ChooseTargetsDecision','context':{'sourceId':'e136','sourceName':'Cryptic Command','phase':'CASTING','effectHint':"Return target permanent to its owner's hand"},'responseSpec':{'responseType':'TargetsResponse'},'targetRequirements':[{'index':0,'minTargets':1,'maxTargets':1}],'legalTargets':{'0':['e356']},'id':'native-target','decisionId':'native-target'}
        choice=modal_pending(p,o,pending);self.assertEqual(choice.choice.response['selectedTargets'],{'0':['e356']})
        for variant in ['changed-target','source','phase']:
            changed=deepcopy(pending)
            if variant=='changed-target':changed['legalTargets']['0']=[]
            elif variant=='source':changed['context']['sourceId']='another-card'
            else:changed['context']['phase']='RESOLUTION'
            self.assertIsNone(modal_pending(p,o,changed),variant)
    def test_unknown_modes_costs_repeat_and_draw_context_retain_native_fallback(self):
        from commander_gym.modal_policies import value_modal_contract,value_modal_plan
        from commander_gym.policy_action_bounds import bounded_observation
        for variant in ['unknown-mode','extra-cost','repeat','no-preview','near-deck-out','dynamic-player-effect']:
            o=self.state();a=self.action(o)
            if variant=='unknown-mode':a['modalEnumeration']['modes'][2]['description']='Unknown effect'
            elif variant=='extra-cost':a['additionalCostInfo']={'costType':'Unknown'}
            elif variant=='repeat':a['modalEnumeration']['allowRepeat']=True
            elif variant=='no-preview':a['modalEnumeration']['modes'][1]['targetRequirements'][0]['validTargets']=None
            elif variant=='near-deck-out':next(p for p in o['state']['players'] if p['playerId']==o['agentToAct'])['librarySize']=1
            else:o['state']['players'][0]['activeEffects']=['unknown effect']
            self.assertIsNone(value_modal_contract(o,a),variant);self.assertIsNone(value_modal_plan(o,a,self.facts),variant)
            b,_=bounded_observation(o,self.facts);self.assertTrue(any(x['semanticId']==a['semanticId'] for x in b['legalActions']),variant)
    def test_live_material_counter_is_prioritized_over_quiet_commander_repair(self):
        from commander_gym.modal_policies import value_modal_plan
        o=self.state();a=self.action(o);s=o['state'];s['cards']['threat']={'name':'Unknown material spell','controllerId':'policy-sythis','manaValue':9}
        next(z for z in s['zones'] if z['zoneId']['zoneType']=='Stack')['cardIds']=['threat']
        counter=a['modalEnumeration']['modes'][0];counter['available']=True;counter['targetRequirements'][0]['validTargets']=['threat']
        a['modalEnumeration']['unavailableIndices']=[];p=value_modal_plan(o,a,self.facts)
        self.assertEqual(p['purpose'],'counter-material-spell-and-draw');self.assertEqual(p['modes'][0]['target'],'threat')
    def test_deck_override_can_keep_an_unreviewed_modal_plan_with_model(self):
        from commander_gym.default_policies import DefaultPolicySubsystem
        from commander_gym.policy_action_bounds import bounded_observation
        class Override(DefaultPolicySubsystem):self_target_exceptions=frozenset({'Cryptic Command'})
        o=self.state();r=Override(self.facts).try_choose(o);self.assertIsNone(r.choice)
        b,_=bounded_observation(o,self.facts,allow_self_targets={'Cryptic Command'})
        self.assertFalse(b.get('policyModalPlans'));self.assertTrue(any(a['kind']=='CastSpellModal' for a in b['legalActions']))
    def test_reviewed_no_value_pair_is_held_without_blocking_other_native_actions(self):
        from commander_gym.policy_action_bounds import bounded_observation
        o=self.state();o['state']['currentStep']='POSTCOMBAT_MAIN';a=self.action(o)
        a['modalEnumeration']['modes'][1]['available']=False;a['modalEnumeration']['unavailableIndices']=[0,1]
        b,restrictions=bounded_observation(o,self.facts)
        self.assertFalse(any(a['kind']=='CastSpellModal' for a in b['legalActions']))
        self.assertTrue(any(a['kind']=='PassPriority' for a in b['legalActions']))
        self.assertIn('hold-reviewed-modal-without-useful-complete-plan',{r['rule'] for r in restrictions})
    def test_exact_native_questions_from_full_precast_branch_complete_with_shared_policy(self):
        from commander_gym.engine_deck_policy import EngineDeckPolicy
        root=Path(__file__).parent/'fixtures';fixture=json.load(gzip.open(root/'cycle-13-regressions.json.gz','rt'))
        p=EngineDeckPolicy(self.facts,'talrand');p.try_choose(self.state())
        for question,response in zip(fixture['nativeModalQuestions'],fixture['nativeModalResponses']):
            o=self.state();pending=deepcopy(question);kind=pending['type'];pending['kind']=kind;pending['decisionId']=pending['id'];pending['requiresStructuredResponse']=True
            rt='OptionChosenResponse' if kind=='ChooseOptionDecision' else 'TargetsResponse'
            fields={'optionIndex':'INTEGER'} if rt=='OptionChosenResponse' else {'selectedTargets': 'MAP'}
            for slot in pending.get('targetRequirements',[]):slot.setdefault('minTargets',1);slot.setdefault('maxTargets',1)
            pending['responseSpec']={'responseType':rt,'requiredFields':fields,'cancelAllowed':pending.get('canCancel',False)}
            o['pendingDecision']=pending;o['legalActions']=[]
            r=p.try_choose(o);self.assertIsNotNone(r.choice);self.assertEqual(dict(r.choice.response),response)
