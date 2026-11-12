"""Primer execution, conservative resource reservation and lossless prompt regression."""
from copy import deepcopy
from types import SimpleNamespace
from pathlib import Path
import json
import unittest
from commander_gym.default_policies import DefaultPolicySubsystem
from commander_gym.engine_deck_policy import EngineDeckPolicy,deck_profile,engine_deck_component_ref
from commander_gym.deck_policy_registry import select_deck_policy,resolve_policy_component
from commander_gym.heuristic_facts import CardFacts
from commander_gym.bounded_policy_fallback import compact_decision_context,compact_action_schema,decision_context,constrain_native_format,BoundedPolicyFallback
from commander_gym.lmstudio_pilot import _schema_check
from test_heuristic_policy import facts,fact,offer,observation,zone,decision
from test_lmstudio import config,FakeTransport,completion,discovery


def expand(view):
    view=deepcopy(view);pool=view.pop('sharedValues',{})
    def resolve(value):
        if isinstance(value,dict) and set(value)=={'$v'}:return resolve(pool[str(value['$v'])])
        if isinstance(value,dict):return {k:resolve(v) for k,v in value.items()}
        if isinstance(value,list):return [resolve(v) for v in value]
        return value
    view=resolve(view)
    def rows(table):return {key:{k:v for k,v in zip(table['columns'],row) if v is not None} for key,row in table['rows'].items()}
    for key in ['cards','cardDefinitions']:
        if key in view:view[key]=rows(view[key])
    definitions=view.pop('cardDefinitions',{})
    for card in view.get('cards',{}).values():
        ref=card.pop('definition',None)
        if ref is not None:
            full={**definitions[ref],**card};card.clear();card.update(full)
    if isinstance(view.get('legalActions'),dict):
        defaults=view.pop('actionDefaults',{});raw=view.pop('rawActionDefaults',{})
        actions=[]
        for row in rows(view['legalActions']).values():
            a={**defaults,**row}
            if 'action' in a:a['action']={**raw,**a['action']}
            actions.append(a)
        view['legalActions']=actions
    return view


def policy_facts():
    doc=facts().document()
    for key in ['talrand','sythis','alela']:
        p=deck_profile(key)
        for name,role in p['roles'].items():
            doc['cards'][name]=fact(manaCost='{2}',types=['ENCHANTMENT'] if role=='draw_engine' else ['CREATURE'])
    doc['cards'].update({'Island':fact(manaCost='',types=['LAND'],subtypes=['Island'],manaColors=['U'],simpleMana=True),
        'Talrand, Sky Summoner':fact(manaCost='{2}{U}{U}'),
        'Counterspell':fact(manaCost='{U}{U}',types=['INSTANT'],tags=['COUNTERSPELL']),
        'Opt':fact(manaCost='{U}',types=['INSTANT'],tags=['DRAW']),
        'Fertile Ground':fact(manaCost='{1}{G}',types=['ENCHANTMENT'],tags=['RAMP']),
        'Myriad Landscape':fact(manaCost='',types=['LAND'],manaColors=['C'],entersTapped=True,unconditionalUntapped=False),
        'Sunpetal Grove':fact(manaCost='',types=['LAND'],manaColors=['W','G'],unconditionalUntapped=False,untappedIfLandSubtypes=['Forest','Plains']),
        "Serra's Sanctum":fact(manaCost='',types=['LAND'],manaColors=['W'],variableMana=True)})
    return CardFacts(doc)


def mana(o,n,color='Island'):
    zone(o,'Battlefield','me',**{'land'+str(i):{'name':color,'cardTypes':['LAND'],'subtypes':[color]} for i in range(n)})


class EnginePolicyTests(unittest.TestCase):
    def setUp(self):self.f=policy_facts()

    def test_exact_registration_and_stale_primer_falls_back(self):
        for key in ['talrand','sythis','alela']:
            p=deck_profile(key);b=SimpleNamespace(deck=SimpleNamespace(fingerprint=p['deck_fingerprint']),deck_knowledge=SimpleNamespace(fingerprint=p['primer_fingerprint']))
            reg=select_deck_policy(b);self.assertEqual(reg.policy_id,p['policy_id'])
            runtime=resolve_policy_component(engine_deck_component_ref(self.f,key),self.f)
            self.assertEqual(runtime.key,key)
            b.deck_knowledge.fingerprint='changed';self.assertIsNone(select_deck_policy(b))

    def test_utility_land_does_not_block_island_and_check_land_timing_uses_live_subtypes(self):
        o=observation(offer('PlayLand',1,card='island'),offer('PlayLand',2,card='myriad'))
        zone(o,'Hand','me',island={'name':'Island'},myriad={'name':'Myriad Landscape'},spell={'name':'Opt','manaCost':'{U}'})
        self.assertEqual(DefaultPolicySubsystem(self.f).try_choose(o).choice.action_id,1)
        p=DefaultPolicySubsystem(self.f)
        self.assertTrue(p._enters_tapped({'name':'Sunpetal Grove'},[]))
        self.assertFalse(p._enters_tapped({'name':'Sunpetal Grove'},[{'name':'Typed dual','cardTypes':['LAND'],'subtypes':['Forest','Island']}]))
        self.assertTrue(p._enters_tapped({'name':'Sunpetal Grove'},[{'name':'Forest-colored creature','cardTypes':['CREATURE'],'subtypes':['Forest']}]))

    def test_variable_mana_is_not_counted_as_available_or_a_keepable_opening_land(self):
        p=EngineDeckPolicy(self.f,'sythis');o=observation();mana(o,1,'Forest')
        zone(o,'Battlefield','me',sanctum={'name':"Serra's Sanctum",'cardTypes':['LAND']})
        self.assertEqual(p._mana_sources(o),[{'G'}])
        o['state']['mulligan']={'cards':{'s':{'name':"Serra's Sanctum"}}}
        self.assertIsNone(p.try_choose(o).choice)

    def test_talrand_precedes_cantrip_with_counter_mana_reserved(self):
        o=observation(offer('PassPriority',0),offer('CastSpell',1,card='commander'),offer('CastSpell',2,card='opt'))
        zone(o,'Command','me',commander={'name':'Talrand, Sky Summoner','manaCost':'{2}{U}{U}'})
        zone(o,'Hand','me',opt={'name':'Opt','manaCost':'{U}'},counter={'name':'Counterspell','manaCost':'{U}{U}'})
        mana(o,6);p=EngineDeckPolicy(self.f,'talrand')
        self.assertEqual(p.try_choose(o).choice.action_id,1)
        o['state']['zones'][2]['cardIds']=o['state']['zones'][2]['cardIds'][:4]
        self.assertEqual(p.try_choose(o).choice.action_id,1)

    def test_unknown_utility_is_not_suppressed_when_reserving_mana(self):
        o=observation(offer('PassPriority',0),offer('CastSpell',1,card='commander'),offer('ActivateAbility',2))
        zone(o,'Command','me',commander={'name':'Talrand, Sky Summoner','manaCost':'{2}{U}{U}'})
        mana(o,4)
        self.assertEqual(EngineDeckPolicy(self.f,'talrand').try_choose(o).choice.action_id,1)
        o['legalActions'] = [a for a in o['legalActions'] if a['kind'] != 'CastSpell']
        self.assertIsNone(EngineDeckPolicy(self.f,'talrand').try_choose(o).choice)

    def test_sythis_sequences_mana_aura_and_spreads_land_risk(self):
        a=offer('CastSpell',1,card='aura');a.update(requiresTargets=True,targetCount=1,minTargets=1,validTargets=['f1','f2'],parameterSpec={'allowedFields':{'targets':'ENTITY_ID_ARRAY'}})
        o=observation(offer('PassPriority',0),a,offer('CastSpell',2,card='draw'))
        zone(o,'Hand','me',aura={'name':'Fertile Ground','manaCost':'{1}{G}'},draw={'name':'Mesa Enchantress','manaCost':'{1}{W}{W}'})
        zone(o,'Battlefield','me',f1={'name':'Forest','cardTypes':['LAND'],'attachments':['old']},f2={'name':'Forest','cardTypes':['LAND']})
        c=EngineDeckPolicy(self.f,'sythis').try_choose(o).choice
        self.assertEqual(c.action_id,1);self.assertEqual(c.params['targets'],['f2'])

    def test_alela_draw_engine_before_commander_and_saves_redundant_engines(self):
        o=observation(offer('PassPriority',0),offer('CastSpell',1,card='commander'),offer('CastSpell',2,card='draw'))
        zone(o,'Command','me',commander={'name':'Alela, Artful Provocateur','manaCost':'{1}{W}{U}{B}'})
        zone(o,'Hand','me',draw={'name':'Mesa Enchantress','manaCost':'{1}{W}{W}'})
        mana(o,6,'Plains');p=EngineDeckPolicy(self.f,'alela')
        self.assertEqual(p.try_choose(o).choice.action_id,2)
        o['legalActions']=[offer('PassPriority',0),offer('CastSpell',2,card='draw')]
        zone(o,'Battlefield','me',e1={'name':'Entity Tracker'},e2={'name':"Caretaker's Talent"})
        self.assertIsNone(p.try_choose(o).choice)

    def test_discovery_near_deckout_and_unproven_closing_play_defer(self):
        o=observation(offer('PassPriority',0),offer('CastSpell',1,card='discovery'))
        zone(o,'Hand','me',discovery={'name':'Kindred Discovery','manaCost':'{3}{U}{U}'})
        o['state']['players'][0]['librarySize']=4
        self.assertIsNone(EngineDeckPolicy(self.f,'alela').try_choose(o).choice)
        o=observation(offer('PassPriority',0),offer('CastSpell',1,card='extra'))
        zone(o,'Hand','me',extra={'name':'Time Warp'})
        self.assertIsNone(EngineDeckPolicy(self.f,'talrand').try_choose(o).choice)

    def test_alela_payoff_names_faerie_instead_of_guessing_numeric_index(self):
        o=decision('ChooseOptionDecision','OptionChosenResponse',{'optionIndex':'INTEGER'},options=['Archon','Elf','Faerie'],context={'sourceName':'Kindred Discovery'})
        self.assertEqual(EngineDeckPolicy(self.f,'alela').try_choose(o).choice.response['optionIndex'],2)

    def test_aura_payoff_does_not_stack_on_already_loaded_attacker(self):
        p=EngineDeckPolicy(self.f,'sythis');o=observation();zone(o,'Battlefield','me',engine={'name':'Mesa Enchantress'},a={'name':'Elf','cardTypes':['CREATURE'],'power':5,'attachments':['one','two']},b={'name':'Elf','cardTypes':['CREATURE'],'power':2})
        a=offer('CastSpell',1);a.update(requiresTargets=True,targetCount=1,minTargets=1,validTargets=['a','b'])
        self.assertEqual(p._cast_params(o,a,{'name':'All That Glitters'},'aura_payoff'),{'targets':['b']})

    def test_pressure_shortlist_keeps_native_pairs_and_a_defensive_plan(self):
        a=offer('DeclareAttackers',0);a.update(validAttackers=['t1','t2'],validAttackTargets=['enemy'],attackTargetsByAttacker={'t1':['enemy'],'t2':['enemy']},parameterSpec={'allowedFields':{'attackers':'ENTITY_ID_MAP'}})
        o=observation(a);zone(o,'Battlefield','me',**{i:{'name':'Faerie','cardTypes':['CREATURE'],'keywords':['FLYING'],'isToken':True,'power':1} for i in ['t1','t2']})
        r=EngineDeckPolicy(self.f,'alela').try_choose(o)
        self.assertIsNone(r.choice);plans=r.metadata['candidateChoices']
        self.assertEqual(len(plans),3);self.assertEqual(plans[0]['params']['attackers'],{})
        self.assertEqual(len(plans[1]['params']['attackers']),2)
        self.assertEqual(len(plans[2]['params']['attackers']),1)
        self.assertTrue(all(t=='enemy' for p in plans for t in p['params']['attackers'].values()))
        a['mandatoryAttackers']=['t1'];r=EngineDeckPolicy(self.f,'alela').try_choose(o)
        self.assertNotIn('candidateChoices',r.metadata)

    def test_unknown_opening_plan_defers_and_repeated_mulligans_stop(self):
        o=observation(offer('KeepHand',0),offer('TakeMulligan',1));cards={'f':{'name':'Forest','typeLine':'Basic Land — Forest'},'f2':{'name':'Forest','typeLine':'Basic Land — Forest'},'p':{'name':'Plains','typeLine':'Basic Land — Plains'},'e':{'name':'Elf','typeLine':'Creature','manaCost':'{G}'}}
        o['state']['mulligan']={'hand':list(cards),'cards':cards,'mulliganCount':0,'cardsToPutOnBottom':0}
        p=EngineDeckPolicy(self.f,'sythis');self.assertIsNone(p.try_choose(o).choice)
        o['state']['mulligan']['mulliganCount']=2
        self.assertEqual(p.try_choose(o).choice.action_id,0)


class CompactPromptTests(unittest.TestCase):
    def test_factorization_round_trips_all_view_values_and_does_not_mutate(self):
        o=observation(*[offer('ActivateAbility',i) for i in range(20)])
        zone(o,'Battlefield','me',**{str(i):{'name':'Island','manaCost':'','typeLine':'Basic Land — Island','oracleText':'A long rules description '+('x'*100),'zone':{'zoneType':'Battlefield','ownerId':'me'}} for i in range(20)})
        view=decision_context(o,frozen=False,policy_context={'plan':'test'},facts=facts());before=deepcopy(view)
        compact=compact_decision_context(view)
        self.assertEqual(expand(compact),before);self.assertEqual(view,before)
        self.assertLess(len(json.dumps(compact)),len(json.dumps(view)))

    def test_schema_merges_only_identical_parameter_domains(self):
        actions=[offer('ActivateAbility',i) for i in range(3)]
        actions[2]['parameterSpec']={'allowedFields':{'xValue':'INTEGER'}}
        o=observation(*actions);original=constrain_native_format(o);compact=compact_action_schema(original)
        self.assertEqual(len(compact['schema']['properties']['choice']['anyOf']),2)
        for a in actions:
            for params in [{},{'xValue':3},{'targets':['invented']}]:
                value={'channel':'action','choice':{'semanticId':a['semanticId'],'params':params}}
                def accepted(schema):
                    try:_schema_check(schema,value,validate=True);return True
                    except Exception:return False
                self.assertEqual(accepted(original['schema']),accepted(compact['schema']))

    def test_recorded_large_alela_request_fits_after_autopay_policy_bounds(self):
        o=json.loads((Path(__file__).parent/'fixtures/alela_large_fallback.json').read_text());before=deepcopy(o)
        # Native-schema mode uses the recorded legal pass only as a test answer.
        a=next(a for a in o['legalActions'] if a['kind']=='PassPriority')
        response={'channel':'action','choice':{'semanticId':a['semanticId'],'params':{}}}
        models=discovery();models['models'][0]['loaded_instances'][0]['config']['context_length']=131072
        t=FakeTransport(completion(json.dumps({'option':0})),models=models)
        f=BoundedPolicyFallback(config(context_window=131072,max_prompt_bytes=32768),t)
        f.choose_with_policy_context(o,[{'metadata':{'policyLayers':{'status':'deferred'}}}]);req=t.calls[1][1]
        self.assertLessEqual(len(json.dumps(req,ensure_ascii=False).encode()),32768)
        view=expand(json.loads(req['messages'][1]['content']))
        self.assertEqual(len(view['choices']),16)
        self.assertFalse(any(c.get('ability','') and 'intrinsic_mana' in c['ability'] for c in view['choices']))
        self.assertEqual(o,before)
