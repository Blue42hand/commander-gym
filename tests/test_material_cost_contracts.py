from copy import deepcopy
import unittest
import gzip
import json
from pathlib import Path
from commander_gym.action_contracts import constrain_material_cost_params,validate_material_cost_params


class MaterialCostContractsTests(unittest.TestCase):
    def test_saved_drum_choice_retries_unoffered_source_before_native_delivery(self):
        from commander_gym.bounded_policy_fallback import BoundedPolicyFallback
        from test_lmstudio import config,discovery,completion,FakeTransport
        fixture=json.load(gzip.open(Path(__file__).with_name('fixtures')/'cycle-15-tap-cost.json.gz','rt'))
        self.assertEqual(fixture['sequence'],8980);self.assertTrue(fixture['evidence']['nativeMaskedMatch'])
        o=fixture['observation'];before=deepcopy(o);bad=fixture['baseline']
        action=next(a for a in o['legalActions'] if a['actionId']==bad['actionId'])
        with self.assertRaisesRegex(ValueError,'outside offered domain'):
            validate_material_cost_params(bad['params'],action)
        wire=lambda params:json.dumps({'channel':'action','choice':{'semanticId':action['semanticId'],'params':params}})
        models=discovery();models['models'][0]['loaded_instances'][0]['config']['context_length']=131072
        transport=FakeTransport(completion(wire(bad['params'])),completion(wire({'tappedPermanents':['e320']})),models=models)
        c=BoundedPolicyFallback(config(context_window=131072,max_prompt_bytes=122880),transport).choose(o)
        self.assertEqual(c.action_id,bad['actionId']);self.assertEqual(c.params['tappedPermanents'],['e320'])
        calls=[body for path,body,_ in transport.calls if path=='/v1/chat/completions'];self.assertEqual(len(calls),2)
        arrays=[]
        def visit(node):
            if isinstance(node,dict):
                if 'tappedPermanents' in node.get('properties',{}):arrays.append(node['properties']['tappedPermanents'])
                for v in node.values():visit(v)
            elif isinstance(node,list):
                for v in node:visit(v)
        visit(calls[0]['response_format']['json_schema']['schema'])
        self.assertEqual(len(arrays),1)
        self.assertEqual(set(arrays[0]['items']['enum']),set(action['additionalCostInfo']['validTapTargets']))
        self.assertEqual(arrays[0]['minItems'],1);self.assertEqual(arrays[0]['maxItems'],1)
        self.assertEqual(o,before)

    def ability(self):
        return {'action':{'type':'ActivateAbility','repeatCount':1},'additionalCostInfo':{
            'costType':'TapPermanents','tapCount':1,'validTapTargets':['creature-a','creature-b']}}

    def test_tap_cost_uses_only_offered_creatures_and_requires_one(self):
        a=self.ability()
        for p in [{'tappedPermanents':['drum']},{'tap':['drum']},{},{'tappedPermanents':[]},
                  {'tap':['creature-a','creature-a']},{'tap':['creature-a','creature-b']},
                  {'tap':['creature-a'],'tappedPermanents':['creature-b']}]:
            with self.subTest(params=p),self.assertRaises(ValueError):validate_material_cost_params(p,a)
        for p in [{'tap':['creature-a']},{'tappedPermanents':['creature-b']}]:validate_material_cost_params(p,a)
        schema={'type':'object','properties':{'tap':{},'tappedPermanents':{}}};before=deepcopy(schema)
        out=constrain_material_cost_params(schema,a)
        self.assertEqual(out['required'],['tappedPermanents']);self.assertNotIn('tap',out['properties'])
        self.assertEqual(out['properties']['tappedPermanents'],{'type':'array','items':{'type':'string','enum':['creature-a','creature-b']},'minItems':1,'maxItems':1})
        self.assertEqual(schema,before)

    def test_preselected_native_payment_does_not_require_another_selection(self):
        a=self.ability();a['action']['costPayment']={'tappedPermanents':['creature-a']}
        validate_material_cost_params({},a)
        out=constrain_material_cost_params({'properties':{'tappedPermanents':{}}},a)
        self.assertNotIn('required',out)

    def test_existing_stronger_cost_bounds_and_domain_are_preserved(self):
        a={'additionalCostInfo':{'validExileTargets':['a','b']}}
        schema={'properties':{'exiledCards':{'type':'array','items':{'type':'string','enum':['a']},'minItems':1,'maxItems':1}}}
        self.assertEqual(constrain_material_cost_params(schema,a),schema)

    def test_material_domains_include_all_additional_cost_entries_and_aliases(self):
        a={'additionalCostInfo':{'validSacrificeTargets':['a'],'validExileTargets':['x'],
            'alsoRequired':[{'validSacrificeTargets':['b'],'validDiscardTargets':['hand'],'validCraftMaterials':['craft']}]}}
        for p in [{'sacrifice':['a','b']},{'exile':['x','craft']},{'discardedCards':['hand']}]:validate_material_cost_params(p,a)
        for p in [{'sacrifice':['unoffered']},{'exile':['unoffered']},{'discard':['unoffered']}]:
            with self.assertRaises(ValueError):validate_material_cost_params(p,a)

    def test_unknown_metadata_does_not_invent_a_cost_domain(self):
        validate_material_cost_params({'tap':['unknown']},{})
        validate_material_cost_params({'tap':['unknown']},{'additionalCostInfo':{'validTapTargets':'unknown'}})

    def test_composite_repeated_and_cast_cost_cardinality_stays_native(self):
        for mode in ['composite','repeated','cast']:
            a=self.ability()
            if mode=='composite':a['additionalCostInfo']['alsoRequired']=[{'validTapTargets':['other']}]
            elif mode=='repeated':a['action']['repeatCount']=2
            else:a['action']['type']='CastSpell'
            validate_material_cost_params({},a)
