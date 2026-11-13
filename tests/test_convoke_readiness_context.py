"""Current convoke readiness stays literal instead of inheriting compressed defaults."""
from copy import deepcopy
import gzip,json,unittest
from pathlib import Path
from commander_gym.bounded_policy_fallback import BoundedPolicyFallback
from commander_gym.heuristic_facts import CardFacts
from commander_gym.convoke_plans import convoke_plans
from test_lmstudio import config,FakeTransport,completion

class ConvokeReadinessContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).parent/'fixtures'
        cls.input=json.load(gzip.open(root/'cycle-18-nullable-schema-failure.json.gz','rt'))['observation']
        cls.facts=CardFacts(json.load(gzip.open(root/'cycle-22-resource-facts.json.gz','rt')))
    def test_native_creature_and_payment_facts_are_uncompressed_and_do_not_mutate_input(self):
        o=deepcopy(self.input);before=deepcopy(o);passed=next(a for a in o['legalActions'] if a['kind']=='PassPriority')
        wire={'channel':'action','choice':{'semanticId':passed['semanticId'],'params':{}}}
        t=FakeTransport(completion(json.dumps(wire)));t.models['models'][0]['loaded_instances'][0]['config']['context_length']=131072
        BoundedPolicyFallback(config(context_window=131072,max_prompt_bytes=122880),t,facts=self.facts).choose_with_policy_context(o,[])
        request=next(body for path,body,_ in t.calls if path=='/v1/chat/completions');v=json.loads(request['messages'][1]['content'])
        ready=v['convokeReadiness'][0];a=next(a for a in o['legalActions'] if a.get('hasConvoke'))
        self.assertEqual(ready['spell'],'Chord of Calling');self.assertTrue(ready['nativeOfferBasePayable'])
        self.assertEqual({r['id'] for r in ready['eligibleUntappedConvokeCreatures']},{r['entityId'] for r in a['validConvokeCreatures']})
        self.assertEqual(len(ready['eligibleUntappedConvokeCreatures']),9)
        self.assertIn(2,ready['harnessPlannedXValues']);self.assertNotIn('$v',json.dumps(ready));self.assertEqual(o,before)
    def test_unpriced_additional_costs_leave_payment_with_inference(self):
        for field in ['manaCostByTargetCount','manaCostPerExtraTarget','optionalManaCostString']:
            o=deepcopy(self.input);a=next(a for a in o['legalActions'] if a.get('hasConvoke'));a[field]={'unknown':True}
            self.assertIsNone(convoke_plans(o,a,require_complete=False),field)
