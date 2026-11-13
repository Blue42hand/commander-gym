"""Two real game26 answers failed only because diagnostic text was too long."""
from copy import deepcopy
import gzip,json,unittest
from pathlib import Path
from commander_gym.bounded_policy_fallback import BoundedPolicyFallback,bounded_diagnostic_explanation
from commander_gym.heuristic_facts import CardFacts
from commander_gym.lmstudio_pilot import LMStudioPilotError
from test_lmstudio import config,discovery,completion,FakeTransport

class DiagnosticExplanationLimitsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).with_name('fixtures')
        cls.fixture=json.load(gzip.open(root/'cycle-20-order-explanation-failure.json.gz','rt'))
        cls.facts=CardFacts(json.load(gzip.open(root/'cycle-19-protection-facts.json.gz','rt')))

    def model(self,output,max_attempts=2):
        models=discovery();models['models'][0]['loaded_instances'][0]['config']['context_length']=131072
        transport=FakeTransport(completion(output),models=models)
        fallback=BoundedPolicyFallback(config(context_window=131072,max_prompt_bytes=122880,decision_explanations=True,max_attempts=max_attempts),transport,facts=self.facts)
        return fallback,transport

    def test_both_actual_answers_keep_exact_order_and_preserve_raw_diagnostics(self):
        for output in self.fixture['recordedOutputs']:
            with self.subTest(output=output[:90]):
                o=self.fixture['observation'];before=deepcopy(o);fallback,t=self.model(output)
                c=fallback.choose_with_policy_context(o,[{'metadata':{'policyLayers':{'status':'deferred'}}}])
                parsed=json.loads(output)
                self.assertEqual(c.response['orderedObjects'],parsed['decision']['response']['orderedObjects'])
                self.assertEqual(c.response['decisionId'],o['pendingDecision']['id'])
                b=c.metadata['boundedFallback'];summary=b['decisionExplanation'];normal=b['decisionExplanationNormalization']
                self.assertTrue(normal['truncated']);self.assertGreater(normal['originalTextChars'],normal['retainedTextChars'])
                texts=[summary['goal'],*summary['keyFacts'],*summary['missingInformation']]
                self.assertTrue(all(len(s)<=180 for s in texts));self.assertLessEqual(sum(map(len,texts)),720)
                self.assertEqual(c.metadata['modelIo']['attempts'][0]['response']['outputText'],output)
                self.assertEqual(sum(path=='/v1/chat/completions' for path,_,_ in t.calls),1)
                self.assertEqual(o,before)

    def test_long_diagnostic_never_makes_invalid_native_order_acceptable(self):
        parsed=json.loads(self.fixture['recordedOutputs'][0]);ids=parsed['decision']['response']['orderedObjects'];ids[1]=ids[0]
        fallback,_=self.model(json.dumps(parsed),max_attempts=1)
        with self.assertRaises(LMStudioPilotError) as raised:
            fallback.choose_with_policy_context(self.fixture['observation'],[{'metadata':{'policyLayers':{'status':'deferred'}}}])
        error=raised.exception.model_io['attempts'][0]['response']['validationError']
        self.assertIn('order-must-be-native-permutation',error)

    def test_individual_and_total_storage_limits_without_input_mutation(self):
        short={'goal':'Preserve the native decision','keyFacts':['Known public fact'],'evidenceIds':[],'uncertainty':'high','missingInformation':[]}
        normalized,n=bounded_diagnostic_explanation(short)
        self.assertEqual(normalized,short);self.assertFalse(n['truncated'])
        long={**short,'goal':'g'*200,'keyFacts':['f'*200]*3,'missingInformation':['m'*200]*3};before=deepcopy(long)
        normalized,n=bounded_diagnostic_explanation(long)
        texts=[normalized['goal'],*normalized['keyFacts'],*normalized['missingInformation']]
        self.assertTrue(n['truncated']);self.assertEqual(n['originalTextChars'],1400)
        self.assertLessEqual(sum(map(len,texts)),720);self.assertTrue(all(len(t)<=180 for t in texts));self.assertEqual(long,before)
