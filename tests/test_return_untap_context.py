"""Literal complete choices stay readable and never create a new admission failure."""
from copy import deepcopy
from pathlib import Path
import gzip,json,unittest
from commander_gym.bounded_policy_fallback import BoundedPolicyFallback,RETURN_UNTAP_BRIEF_INSTRUCTIONS,_return_untap_options
from commander_gym.heuristic_facts import CardFacts
from test_lmstudio import config,FakeTransport,completion

class ReturnUntapContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).with_name('fixtures')
        cls.o=next(r['observation'] for r in json.load(gzip.open(root/'cycle-24-reactive-bounce.json.gz','rt'))['rows'] if r['sequence']==6857)
        cls.facts=CardFacts(json.load(gzip.open(root/'cycle-26-native-facts.json.gz','rt')))

    def request(self,limit=122880):
        t=FakeTransport(completion(json.dumps({'option':0})))
        t.models['models'][0]['loaded_instances'][0]['config']['context_length']=131072
        BoundedPolicyFallback(config(context_window=131072,max_prompt_bytes=limit),t,facts=self.facts).choose_with_policy_context(self.o,[])
        return next(b for p,b,_ in t.calls if p=='/v1/chat/completions')

    def test_actual_cost_and_target_names_are_literal_and_unambiguous(self):
        before=deepcopy(self.o);r=self.request();view=json.loads(r['messages'][1]['content'])
        rows=view['returnUntapOptions'];self.assertEqual(len(rows),2)
        paid=next(v for v in rows if v['returnCost'])
        self.assertEqual(paid['returnCost'][0]['name'],'Elvish Visionary')
        self.assertEqual(paid['untapTarget']['name'],'Wirewood Symbiote')
        self.assertEqual(paid['untapTarget']['id'],'e98');self.assertTrue(paid['untapTarget']['tapped'])
        self.assertNotIn('$v',json.dumps(rows));self.assertIn('Prefer Pass',r['messages'][0]['content'])
        self.assertEqual(self.o,before)

    def test_optional_literal_context_is_omitted_when_original_request_just_fits(self):
        r=self.request();base=deepcopy(r);view=json.loads(base['messages'][1]['content'])
        view.pop('returnUntapOptions');base['messages'][1]['content']=json.dumps(view,separators=(',',':'))
        base['messages'][0]['content']=base['messages'][0]['content'].replace(RETURN_UNTAP_BRIEF_INSTRUCTIONS,'')
        limit=len(json.dumps(base,ensure_ascii=False).encode())
        low=self.request(limit)
        self.assertNotIn('returnUntapOptions',json.loads(low['messages'][1]['content']))
        self.assertLessEqual(len(json.dumps(low,ensure_ascii=False).encode()),limit)

    def test_large_menus_do_not_receive_partial_literal_summaries(self):
        a=next(a for a in self.o['legalActions'] if a.get('action',{}).get('abilityId')=='Wirewood Symbiote:1')
        wire={'channel':'action','choice':{'semanticId':a['semanticId'],'params':{'targets':['e98'],'bouncedPermanents':['e71']}}}
        self.assertEqual(len(_return_untap_options([wire],self.o,self.facts)),1)
        self.assertIsNone(_return_untap_options([wire]*9,self.o,self.facts))

    def test_return_cost_can_be_a_land_in_another_native_untap_family(self):
        o=deepcopy(self.o)
        a=next(a for a in o['legalActions'] if a.get('action',{}).get('abilityId')=='Wirewood Symbiote:1')
        o['state']['cards']['e98']['name']='Quirion Ranger';a['action']['abilityId']='Quirion Ranger:1'
        o['state']['cards']['e71'].update(name='Forest',cardTypes=['LAND'],power=None,toughness=None)
        wire={'channel':'action','choice':{'semanticId':a['semanticId'],'params':{'targets':['e98'],'bouncedPermanents':['e71']}}}
        rows=_return_untap_options([wire],o,self.facts)
        self.assertEqual(rows[0]['returnCost'][0]['name'],'Forest')
        self.assertEqual(rows[0]['untapTarget']['name'],'Quirion Ranger')
        self.assertIn('returned permanent',RETURN_UNTAP_BRIEF_INSTRUCTIONS)
