"""Cross-deck defaults, overlay precedence and bounded inference integration."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from commander_gym.default_policies import DefaultPolicySubsystem, ProfiledDeckPolicy
from commander_gym.deck_policy_registry import DeckPolicyRegistration, select_deck_policy, profile_component_ref
from commander_gym.bounded_policy_fallback import BoundedPolicyFallback, enumerate_choices, constrain_native_format
from commander_gym.heuristic_facts import CardFacts, heuristic_component_ref
from commander_gym.lathril_policy import LathrilDeckSubsystem, policy_document, lathril_component_ref
from commander_gym.game_server_binding_lmstudio_sidecar import LocalBindingComponentResolver, local_component_ref
from commander_gym.identity import Pilot
from commander_gym.pilot_composition import compose_pilot_runtime, SubsystemDecision
from commander_gym.pilot import PilotContractError
from commander_gym.local_callback_context import current_cancel
from commander_gym.lmstudio_pilot import LMStudioPilotError
from commander_gym.lmstudio_provider import LMStudioError
from test_heuristic_policy import facts, fact, offer, observation, zone, decision
from test_lmstudio import config, FakeTransport, completion, discovery


def rank_transport(option=0):
    return FakeTransport(completion(json.dumps({'option':option}),usage={'prompt_tokens':100,'completion_tokens':5,'total_tokens':105}))


def runtime(*, deck=False, transport=None, f=None):
    f=f or facts();cfg=config()
    pilot=Pilot(pilot_id='layers',revision='1',generalist_base=local_component_ref(cfg),
                skill_set=lathril_component_ref(f) if deck else heuristic_component_ref(f))
    return compose_pilot_runtime(pilot,LocalBindingComponentResolver(cfg,transport,card_facts=f))


class PolicyLayerTests(unittest.TestCase):
    def test_defaults_handle_basic_lands_without_facts_or_deck_registration(self):
        o=observation(offer('PassPriority',0),offer('PlayLand',1,card='land'))
        zone(o,'Hand','me',land={'name':'Island','typeLine':'Basic Land — Island'})
        p=BoundedPolicyFallback(config(),FakeTransport())
        c=p.choose(o)
        self.assertEqual(c.action_id,1)
        self.assertEqual(p.model.transport.calls,[])
        self.assertIsNone(c.metadata['policyLayers']['deck'])

    def test_non_green_deck_uses_blue_mana_and_shared_scry(self):
        doc=facts().document();doc['cards']['Island']=fact(manaCost='',types=['LAND'],manaColors=['U'],simpleMana=True)
        doc['cards']['Blue Creature']=fact(manaCost='{U}')
        p=DefaultPolicySubsystem(CardFacts(doc))
        o=observation(offer('PlayLand',1,card='forest'),offer('PlayLand',2,card='island'))
        zone(o,'Hand','me',forest={'name':'Forest'},island={'name':'Island'},spell={'name':'Blue Creature','manaCost':'{U}'})
        self.assertEqual(p.try_choose(o).choice.action_id,2)
        o=decision('SelectCardsDecision','CardsSelectedResponse',{'selectedCards':'ENTITY_ID_ARRAY'},
                   prompt='Scry 1: choose cards to put on the bottom of your library',options=['island'],minSelections=0,maxSelections=1,
                   selectedLabel='Put on bottom',remainderLabel='Put on top',cardInfo={'island':{'name':'Island','typeLine':'Basic Land — Island'}})
        self.assertEqual(p.try_choose(o).choice.response['selectedCards'],[])

    def test_shared_cleanup_uses_actual_hand_limit_not_lathril_identity(self):
        o=json.loads((Path(__file__).parent/'fixtures/lathril_mirror_regressions.json').read_text())['discard']
        p=DefaultPolicySubsystem(facts());r=p.try_choose(o)
        self.assertIsNotNone(r.choice)
        self.assertEqual(len(r.choice.response['selectedCards']),1)
        self.assertIsNone(r.metadata['policyLayers']['deck'])

    def test_defaults_preserve_resources_on_complex_board_without_a_mana_plan(self):
        o=observation(offer('PassPriority',0),offer('ActivateAbility',1,mana=True))
        zone(o,'Battlefield','enemy',complex={'name':'Unreviewed creature','activeEffects':['unrepresented']})
        self.assertEqual(DefaultPolicySubsystem(facts()).try_choose(o).choice.action_id,0)

    def test_unknown_card_cost_still_informs_basic_land_selection(self):
        o=observation(offer('PlayLand',1,card='forest'),offer('PlayLand',2,card='island'))
        zone(o,'Hand','me',forest={'name':'Forest'},island={'name':'Island'},spell={'name':'Unreviewed blue spell','manaCost':'{U}'})
        self.assertEqual(DefaultPolicySubsystem().try_choose(o).choice.action_id,2)

    def test_profile_modifies_development_without_copying_rule_code(self):
        f=facts();o=observation(offer('CastSpell',1,card='elf'),offer('CastSpell',2,card='big'))
        zone(o,'Hand','me',elf={'name':'Elf','manaCost':'{G}'},big={'name':'Expensive','manaCost':'{6}{G}'})
        p=DefaultPolicySubsystem(f)
        self.assertEqual(p.try_choose(o).choice.action_id,1)
        profile={'policy_id':'toy-control-v1','roles':{'Expensive':'priority'},'weights':{'priority':200}}
        modified=ProfiledDeckPolicy(f,profile)
        self.assertEqual(modified.try_choose(o).choice.action_id,2)
        self.assertEqual(modified.try_choose(o).metadata['policyLayers']['deck'],'toy-control-v1')
        with self.assertRaises(PilotContractError): ProfiledDeckPolicy(f,{'policy_id':'bad','weights':{'priority':float('nan')}})

    def test_extension_supersedes_shared_rule_and_none_inherits(self):
        class Custom(DefaultPolicySubsystem):
            def _deck_pending(self,o,p):
                if p.get('prompt')=='custom choice':
                    return self._decision(p,p['responseSpec']['responseType'],'custom-rule',choice=False)
                return None
        p=Custom(facts());o=decision('YesNoDecision','YesNoResponse',{'choice':'BOOLEAN'},prompt='custom choice',yesAvailable=True)
        self.assertFalse(p.try_choose(o).choice.response['choice'])
        o['pendingDecision'].update(prompt='otherwise',yesAvailable=False)
        self.assertFalse(p.try_choose(o).choice.response['choice'])

    def test_exact_deck_and_primer_match_required_for_automatic_overlay(self):
        doc=policy_document();b=SimpleNamespace(deck=SimpleNamespace(fingerprint=doc['deck_fingerprint']),
                deck_knowledge=SimpleNamespace(fingerprint=doc['primer_fingerprint']))
        self.assertEqual(select_deck_policy(b).policy_id,doc['policy_id'])
        b.deck_knowledge.fingerprint='different'
        self.assertIsNone(select_deck_policy(b))
        b.deck.fingerprint='other';b.deck_knowledge=None
        self.assertIsNone(select_deck_policy(b))

    def test_explicit_wrong_overlay_and_unpinned_facts_are_rejected(self):
        from commander_gym.deck_policy_registry import validate_policy_binding
        b=SimpleNamespace(deck=SimpleNamespace(fingerprint='other'),deck_knowledge=None)
        with self.assertRaisesRegex(PilotContractError,'exact reviewed'):
            validate_policy_binding(lathril_component_ref(facts()),b,facts())
        with self.assertRaisesRegex(PilotContractError,'pinned policy'):
            validate_policy_binding(None,b,facts())
        validate_policy_binding(heuristic_component_ref(facts()),b,facts())

    def test_new_data_profile_can_be_registered_without_router_changes(self):
        profile={'policy_id':'toy-profile','version':'1','weights':{'creature':77}}
        reg=DeckPolicyRegistration('toy-profile','deck','primer',lambda f:ProfiledDeckPolicy(f,profile),lambda f:profile_component_ref(f,profile))
        b=SimpleNamespace(deck=SimpleNamespace(fingerprint='deck'),deck_knowledge=SimpleNamespace(fingerprint='primer'))
        with patch('commander_gym.deck_policy_registry.registered_deck_policies',return_value=(reg,)):
            from commander_gym.deck_policy_registry import resolve_policy_component
            self.assertIs(select_deck_policy(b),reg)
            self.assertEqual(resolve_policy_component(reg.component_ref(facts()),facts()).document['weights']['creature'],77)
        changed=deepcopy(profile);changed['weights']['creature']=78
        self.assertNotEqual(profile_component_ref(facts(),profile),profile_component_ref(facts(),changed))

    def test_unknown_action_is_not_suppressed_by_deck_pass(self):
        o=observation(offer('PassPriority',0),offer('ActivateAbility',1))
        self.assertIsNone(LathrilDeckSubsystem(facts()).try_choose(o).choice)
        t=rank_transport();c=runtime(deck=True,transport=t).choose(o)
        self.assertEqual(c.action_id,0)
        self.assertEqual(len(t.calls),2)
        self.assertEqual(c.metadata['routing']['handledBy']['role'],'local_generalist')

    def test_unknown_decision_reaches_bounded_model_for_any_deck(self):
        for deck in [False,True]:
            with self.subTest(deck=deck):
                t=rank_transport(1);o=decision('ChooseOptionDecision','OptionChosenResponse',{'optionIndex':'INTEGER'},options=['A','B'],context={'sourceName':'Unreviewed card'})
                c=runtime(deck=deck,transport=t).choose(o)
                self.assertEqual(c.response['optionIndex'],1)
                self.assertEqual(c.metadata['boundedFallback']['candidateCount'],2)
                self.assertTrue(c.metadata['routing']['escalated'])

    def test_contract_bug_and_cancel_are_not_hidden_by_fallback(self):
        class Buggy(DefaultPolicySubsystem):
            def _choose_actions(self,o):return self._action(offer('PassPriority',999),'bug')
        with self.assertRaises((PilotContractError,StopIteration)):
            Buggy(facts()).try_choose(observation(offer('PassPriority',0)))
        event=threading.Event();event.set();token=current_cancel.set(event);t=rank_transport()
        try:
            with self.assertRaisesRegex(LMStudioPilotError,'cancelled'):
                runtime(transport=t).choose(decision('ChooseOptionDecision','OptionChosenResponse',{'optionIndex':'INTEGER'},options=['A','B']))
        finally:current_cancel.reset(token)
        self.assertEqual(t.calls,[])


class BoundedFallbackTests(unittest.TestCase):
    def test_recorded_bojuka_callback_now_reaches_model_with_four_complete_choices(self):
        path=Path(__file__).parent/'fixtures/bojuka_bog_pending.json'
        o=json.loads(path.read_text());before=deepcopy(o)
        cfg=config(context_window=131072,max_prompt_bytes=32768)
        models=discovery();models['models'][0]['loaded_instances'][0]['config']['context_length']=131072
        t=rank_transport(1);t.models=models
        f=BoundedPolicyFallback(cfg,t,facts=facts())
        c=f.choose_with_policy_context(o,[{'metadata':{'policyLayers':{'defaults':'default-policies-v2','deck':'lathril-primer-v2'},'policyContext':{'objectives':['develop mana and Elves']}}}])
        self.assertIn(c.response['selectedTargets']['0'][0],o['pendingDecision']['legalTargets']['0'])
        self.assertEqual(c.metadata['boundedFallback']['candidateCount'],4)
        self.assertEqual(o,before)
        req=t.calls[1][1]
        self.assertLess(len(json.dumps(req).encode()),32768)
        self.assertEqual(req['response_format']['json_schema']['schema']['required'],['option'])

    def test_recorded_activation_menu_stays_under_prompt_budget(self):
        o=json.loads((Path(__file__).parent/'fixtures/activation_fallback.json').read_text())
        cfg=config(context_window=131072,max_prompt_bytes=32768)
        models=discovery();models['models'][0]['loaded_instances'][0]['config']['context_length']=131072
        t=rank_transport();t.models=models
        c=BoundedPolicyFallback(cfg,t,facts=facts()).choose(o)
        self.assertEqual(c.metadata['boundedFallback']['mode'],'frozen-choices')
        self.assertEqual(c.metadata['boundedFallback']['candidateCount'],2)
        self.assertLess(len(json.dumps(t.calls[1][1],ensure_ascii=False).encode()),20000)

    def test_history_primer_and_administrative_fields_never_reach_model(self):
        o=decision('ChooseOptionDecision','OptionChosenResponse',{'optionIndex':'INTEGER'},options=['A','B'])
        o['state']['gameLog']=['DO_NOT_SEND_HISTORY'*10000];o['recentGameLog']=['DO_NOT_SEND_RECENT']
        o['deckKnowledge']='DO_NOT_SEND_PRIMER'*10000;o['adminState']={'opponentHand':['DO_NOT_SEND_SECRET']}
        o['state']['adminState']={'hiddenLibrary':['DO_NOT_SEND_STATE_SECRET']}
        before=deepcopy(o);t=rank_transport();BoundedPolicyFallback(config(),t).choose(o)
        wire=json.dumps(t.calls)
        for text in ['DO_NOT_SEND_HISTORY','DO_NOT_SEND_RECENT','DO_NOT_SEND_PRIMER','DO_NOT_SEND_SECRET','DO_NOT_SEND_STATE_SECRET']:
            self.assertNotIn(text,wire)
        self.assertEqual(o,before)

    def test_callback_bindings_are_request_local_under_concurrency(self):
        from concurrent.futures import ThreadPoolExecutor
        class RankFirst:
            def request(self,path,body,**kwargs):
                return discovery() if path=='/api/v1/models' else completion('{"option":0}')
        fallback=BoundedPolicyFallback(config(),RankFirst())
        observations=[]
        for n in range(8):
            o=decision('ChooseOptionDecision','OptionChosenResponse',{'optionIndex':'INTEGER'},options=['A','B'])
            o['pendingDecision']['decisionId']='callback-'+str(n);observations.append(o)
        with ThreadPoolExecutor(max_workers=4) as pool:
            choices=list(pool.map(fallback.choose,observations))
        self.assertEqual([c.response['decisionId'] for c in choices],['callback-'+str(n) for n in range(8)])
        self.assertTrue(all(c.metadata['boundedFallback']['selectedOption']==0 for c in choices))

    def test_stale_policy_shortlist_is_rejected_before_dispatch(self):
        o=observation(offer('PassPriority',0),offer('ActivateAbility',1));t=rank_transport()
        context=[{'metadata':{'policyLayers':{'defaults':'default-policies-v2'},'candidateChoices':[{'actionId':999,'params':{}}]}}]
        with self.assertRaisesRegex(PilotContractError,'stale'):
            BoundedPolicyFallback(config(),t).choose_with_policy_context(o,context)
        self.assertEqual(t.calls,[])

    def test_invalid_index_does_not_fall_back_to_first_choice(self):
        t=FakeTransport(completion('{"option":99}'),completion('{"option":99}'))
        o=decision('ChooseOptionDecision','OptionChosenResponse',{'optionIndex':'INTEGER'},options=['A','B'])
        with self.assertRaises(LMStudioPilotError):BoundedPolicyFallback(config(),t).choose(o)
        self.assertEqual(sum(path=='/v1/chat/completions' for path,_,_ in t.calls),2)

    def test_provider_failure_never_replaces_choice_with_pass(self):
        o=decision('ChooseOptionDecision','OptionChosenResponse',{'optionIndex':'INTEGER'},options=['A','B'])
        with self.assertRaisesRegex(LMStudioPilotError,'deadline_exceeded'):
            BoundedPolicyFallback(config(),FakeTransport(LMStudioError('deadline_exceeded'))).choose(o)

    def test_combinatorial_selection_keeps_native_grammar_and_checks_duplicates(self):
        options=['c'+str(i) for i in range(10)]
        o=decision('SelectCardsDecision','CardsSelectedResponse',{'selectedCards':'ENTITY_ID_ARRAY'},options=options,minSelections=5,maxSelections=5)
        self.assertIsNone(enumerate_choices(o))
        response={'channel':'decision','response':{'type':'CardsSelectedResponse','selectedCards':options[:5]}}
        t=FakeTransport(completion(json.dumps(response)))
        c=BoundedPolicyFallback(config(),t).choose(o)
        self.assertEqual(c.response['selectedCards'],options[:5]);self.assertEqual(c.metadata['boundedFallback']['mode'],'native-schema')
        response['response']['selectedCards']=['c0']*5
        t=FakeTransport(completion(json.dumps(response)),completion(json.dumps(response)))
        with self.assertRaises(LMStudioPilotError):BoundedPolicyFallback(config(),t).choose(o)

    def test_constraints_are_kept_in_native_grammar_not_pruned_into_fake_legal_choices(self):
        o=decision('SelectCardsDecision','CardsSelectedResponse',{'selectedCards':'ENTITY_ID_ARRAY'},options=['a','b'],minSelections=1,maxSelections=1,onePerCardType=True)
        self.assertIsNone(enumerate_choices(o))
        fmt=constrain_native_format(o)
        self.assertEqual(fmt['schema']['properties']['response']['properties']['selectedCards']['minItems'],1)

    def test_large_option_domain_remains_available_in_native_schema(self):
        o=decision('ChooseOptionDecision','OptionChosenResponse',{'optionIndex':'INTEGER'},options=['type'+str(i) for i in range(140)])
        self.assertIsNone(enumerate_choices(o))
        t=FakeTransport(completion('{"channel":"decision","response":{"type":"OptionChosenResponse","optionIndex":139}}'))
        c=BoundedPolicyFallback(config(max_prompt_bytes=6144),t).choose(o)
        self.assertEqual(c.response['optionIndex'],139)

    def test_budgets_fail_before_network_without_discarding_current_choices(self):
        o=decision('ChooseOptionDecision','OptionChosenResponse',{'optionIndex':'INTEGER'},options=['x'*10000,'B'])
        t=rank_transport()
        with self.assertRaisesRegex(LMStudioPilotError,'prompt_byte_limit'):
            BoundedPolicyFallback(config(),t).choose(o)
        self.assertEqual(t.calls,[])

    def test_library_reordering_preserves_native_cards_and_offered_order_domain(self):
        o=decision('ReorderLibraryDecision','OrderedResponse',{'orderedObjects':'ENTITY_ID_ARRAY'},
                   cards=['a','b'],cardInfo={'a':{'name':'Forest'},'b':{'name':'Elf'}})
        t=FakeTransport(completion('{"channel":"decision","response":{"type":"OrderedResponse","orderedObjects":["b","a"]}}'))
        c=BoundedPolicyFallback(config(),t).choose(o)
        self.assertEqual(c.response['orderedObjects'],['b','a'])
        view=json.loads(t.calls[1][1]['messages'][1]['content'])
        self.assertEqual(view['decision']['cards'],['a','b'])
        from test_engine_deck_policy import expand
        self.assertEqual(expand(view)['cards']['a']['name'],'Forest')

    def test_trigger_order_fallback_uses_native_index_permutation(self):
        o=decision('OrderTriggersDecision','TriggerOrderResponse',{'resolutionOrder':'INTEGER_ARRAY'},
                   triggers=[{'description':'draw a card'},{'description':'create a token'}])
        t=FakeTransport(completion('{"channel":"decision","response":{"type":"TriggerOrderResponse","resolutionOrder":[1,0]}}'))
        c=BoundedPolicyFallback(config(),t).choose(o)
        self.assertEqual(c.response['resolutionOrder'],[1,0])
        invalid=completion('{"channel":"decision","response":{"type":"TriggerOrderResponse","resolutionOrder":[0,0]}}')
        with self.assertRaises(LMStudioPilotError):
            BoundedPolicyFallback(config(),FakeTransport(invalid,invalid)).choose(o)

    def test_large_bottom_choice_keeps_exact_hand_domain(self):
        hand=['card'+str(i) for i in range(10)]
        o=decision('BottomCards','CardsSelectedResponse',{'selectedCards':'ENTITY_ID_ARRAY'},hand=hand,cardsToPutOnBottom=5)
        self.assertIsNone(enumerate_choices(o))
        schema=constrain_native_format(o)['schema']['properties']['response']['properties']['selectedCards']
        self.assertEqual(schema['items']['enum'],hand)
        self.assertEqual(schema['minItems'],5);self.assertEqual(schema['maxItems'],5)

    def test_optional_targets_are_preserved_instead_of_freezing_only_no_target_cast(self):
        a=offer('CastSpell',1,card='spell')
        a.update(validTargets=['target'],minTargets=0,targetCount=1,parameterSpec={'allowedFields':{'targets':'ENTITY_ID_ARRAY'}})
        o=observation(a);zone(o,'Hand','me',spell={'name':'Unreviewed spell','manaCost':'{G}'})
        zone(o,'Battlefield','enemy',target={'name':'Elf','controllerId':'enemy'})
        self.assertIsNone(enumerate_choices(o))
        answer={'channel':'action','choice':{'semanticId':a['semanticId'],'params':{'targets':['target']}}}
        c=BoundedPolicyFallback(config(),FakeTransport(completion(json.dumps(answer)))).choose(o)
        self.assertEqual(c.params['targets'],['target'])

    def test_mixed_payment_retains_native_action_and_decision_branches(self):
        o=decision('SelectManaSourcesDecision','ManaSourcesSelectedResponse',{'autoPay':'BOOLEAN','declined':'BOOLEAN','selectedSources':'ENTITY_ID_ARRAY','waterbendPermanents':'ENTITY_ID_ARRAY'},
                   availableSources=[{'entityId':'land'}],canAutoPayNow=False,canDecline=False)
        a=offer('ActivateAbility',1,mana=True);o['legalActions']=[a]
        self.assertIsNone(enumerate_choices(o))
        fmt=constrain_native_format(o)
        self.assertEqual(len(fmt['schema']['anyOf']),2)


if __name__=='__main__':unittest.main()
