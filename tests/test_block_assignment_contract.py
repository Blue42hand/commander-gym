"""Native game25 rejection: an assigned blocker cannot have an empty target list."""
from copy import deepcopy
import gzip,json,unittest
from pathlib import Path
from commander_gym.openai_responses_pilot import _validate_native_action_params,OpenAIResponsesPilotError
from commander_gym.bounded_policy_fallback import BoundedPolicyFallback
from commander_gym.heuristic_facts import CardFacts
from test_lmstudio import config,discovery,completion,FakeTransport

class BlockAssignmentContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).with_name('fixtures')
        cls.row=next(r for r in json.load(gzip.open(root/'cycle-19-late-regressions.json.gz','rt'))['rows'] if r['sequence']==12695)
        cls.facts=CardFacts(json.load(gzip.open(root/'cycle-19-protection-facts.json.gz','rt')))

    def test_actual_rejected_assignment_is_rejected_before_native_execution(self):
        o=self.row['observation'];c=self.row['recordedChoice'];a=next(a for a in o['legalActions'] if a['actionId']==c['actionId'])
        self.assertEqual(c['params'],{'blockers':{'e405':[]}})
        with self.assertRaisesRegex(OpenAIResponsesPilotError,'nonempty distinct'):_validate_native_action_params(c['params'],a)
        _validate_native_action_params({'blockers':{}},a)
        _validate_native_action_params({'blockers':{'e405':['e300']}},a)

    def test_real_input_schema_excludes_empty_assignment_and_retry_remains_bounded(self):
        o=self.row['observation'];c=self.row['recordedChoice'];a=next(a for a in o['legalActions'] if a['actionId']==c['actionId'])
        invalid={'channel':'action','choice':{'semanticId':a['semanticId'],'params':c['params']}}
        valid=deepcopy(invalid);valid['choice']['params']={'blockers':{}}
        models=discovery();models['models'][0]['loaded_instances'][0]['config']['context_length']=131072
        t=FakeTransport(completion(json.dumps(invalid)),completion(json.dumps(valid)),models=models)
        chosen=BoundedPolicyFallback(config(context_window=131072,max_prompt_bytes=122880),t,facts=self.facts).choose_with_policy_context(o,[{'metadata':{'policyLayers':{'status':'deferred'}}}])
        self.assertEqual(chosen.params,{'blockers':{}})
        requests=[body for path,body,_ in t.calls if path=='/v1/chat/completions'];self.assertEqual(len(requests),2)
        def nodes(v):
            if isinstance(v,dict):
                yield v
                for child in v.values():yield from nodes(child)
            elif isinstance(v,list):
                for child in v:yield from nodes(child)
        maps=[n for n in nodes(requests[0]['response_format']['json_schema']['schema']) if n.get('type')=='array' and n.get('items',{}).get('enum')==['e300']]
        self.assertTrue(maps);self.assertTrue(all(n.get('minItems')==n.get('maxItems')==1 for n in maps))

    def test_distinctness_and_native_multiple_block_capacity(self):
        a={'kind':'DeclareBlockers','parameterSpec':{'allowedFields':{'blockers':'ENTITY_ID_ARRAY_MAP'}},'validBlockTargets':{'blocker':['one','two']}}
        for ids in [[],['one','one'],['one','two']]:
            with self.assertRaises(OpenAIResponsesPilotError):_validate_native_action_params({'blockers':{'blocker':ids}},a)
        a['blockerMaxBlockCounts']={'blocker':2}
        _validate_native_action_params({'blockers':{'blocker':['one','two']}},a)
        with self.assertRaisesRegex(OpenAIResponsesPilotError,'nonempty distinct'):_validate_native_action_params({'blockers':{'blocker':['one','one']}},a)
