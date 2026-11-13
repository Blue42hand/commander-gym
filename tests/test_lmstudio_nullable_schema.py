"""Faithful native nullable unions; unsupported constraints still fail closed."""
from copy import deepcopy
from pathlib import Path
import gzip,json,unittest
from commander_gym.lmstudio_pilot import _schema_check,_local_native_format
from commander_gym.lmstudio_provider import LMStudioError
from commander_gym.bounded_policy_fallback import BoundedPolicyFallback
from commander_gym.heuristic_facts import CardFacts
from commander_gym.lathril_policy import LathrilDeckSubsystem
from test_lmstudio import config,discovery,completion,FakeTransport

class NullableSchemaTests(unittest.TestCase):
    def test_nullable_integer_preserves_bounds_enums_and_boolean_distinction(self):
        schema={'type':['integer','null'],'minimum':0,'maximum':3,'enum':[None,0,3]};before=deepcopy(schema)
        for value in [None,0,3]:_schema_check(schema,value,validate=True)
        for value in [-1,1,4,False,True,'0',[]]:
            with self.subTest(value=value),self.assertRaisesRegex(LMStudioError,'output_schema_mismatch'):_schema_check(schema,value,validate=True)
        self.assertEqual(schema,before)

    def test_nested_nullable_objects_and_arrays_keep_required_and_item_limits(self):
        schema={'type':['object','null'],'properties':{'values':{'type':['array','null'],'minItems':1,'maxItems':2,'items':{'type':['integer','null']}}},'required':['values'],'additionalProperties':False}
        for value in [None,{'values':None},{'values':[1,None]}]:_schema_check(schema,value,validate=True)
        for value in [{},{'values':[]},{'values':[1,2,3]},{'values':[True]},{'values':['bad']},{'values':None,'extra':1}]:
            with self.subTest(value=value),self.assertRaisesRegex(LMStudioError,'output_schema_mismatch'):_schema_check(schema,value,validate=True)

    def test_malformed_types_and_unsupported_constraints_fail_with_typed_error(self):
        for kind in [[],['integer','integer'],['other','null'],[[],'null'],True,1,{},None]:
            with self.subTest(kind=kind),self.assertRaisesRegex(LMStudioError,'unsupported_json_schema'):_schema_check({'type':kind})
        with self.assertRaisesRegex(LMStudioError,'unsupported_json_schema'):
            _schema_check({'type':['object','null'],'properties':{'x':{'type':'string','pattern':'.*'}}})

    def saved(self):
        root=Path(__file__).with_name('fixtures');row=json.load(gzip.open(root/'cycle-18-nullable-schema-failure.json.gz','rt'))
        facts=CardFacts(json.load(gzip.open(root/'cycle-18-entry-facts.json.gz','rt')))
        return row,facts

    def test_authentic_failed_input_now_reaches_model_with_native_unions_intact(self):
        row,facts=self.saved();o=deepcopy(row['observation']);before=deepcopy(o)
        self.assertTrue(row['evidence']['nativeMaskedMatch']);self.assertFalse(row['evidence']['nativeResultObserved'])
        result=LathrilDeckSubsystem(facts).try_choose(o);self.assertIsNone(result.choice)
        passed=next(a for a in o['legalActions'] if a['kind']=='PassPriority')
        wire={'channel':'action','choice':{'semanticId':passed['semanticId'],'params':{}}}
        text={'decision':wire,'explanation':{'goal':'Contract check only','keyFacts':[],'evidenceIds':[],'uncertainty':'high','missingInformation':[]}}
        models=discovery();models['models'][0]['loaded_instances'][0]['config']['context_length']=131072
        transport=FakeTransport(completion(json.dumps(text)),models=models)
        cfg=config(context_window=131072,max_prompt_bytes=122880,decision_explanations=True)
        choice=BoundedPolicyFallback(cfg,transport,facts=facts).choose_with_policy_context(o,[{'metadata':result.metadata}])
        self.assertEqual(choice.action_id,passed['actionId']);self.assertEqual(dict(choice.params),{})
        self.assertEqual([c[0] for c in transport.calls],['/api/v1/models','/v1/chat/completions'])
        schema=transport.calls[-1][1]['response_format']['json_schema']['schema']
        def unions(value):
            if isinstance(value,dict):return int(isinstance(value.get('type'),list))+sum(unions(v) for v in value.values())
            if isinstance(value,list):return sum(unions(v) for v in value)
            return 0
        self.assertGreater(unions(schema),0);self.assertEqual(o,before)

    def test_saved_native_menu_still_rejects_unknown_semantics_and_fields(self):
        row,_=self.saved();o=row['observation'];schema=_local_native_format(o)['schema']
        passed=next(a for a in o['legalActions'] if a['kind']=='PassPriority')
        for wire in [{'channel':'action','choice':{'semanticId':'unknown','params':{}}},
                     {'channel':'action','choice':{'semanticId':passed['semanticId'],'params':{'forbidden':1}}}]:
            with self.subTest(wire=wire),self.assertRaises(LMStudioError):_schema_check(schema,wire,validate=True)
