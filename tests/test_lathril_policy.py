from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from commander_gym.bounded_deck_advisor import BoundedDeckAdvisor
from commander_gym.heuristic_facts import CardFacts, heuristic_component_ref
from commander_gym.lathril_policy import LathrilDeckSubsystem, lathril_component_ref, policy_document, validate_lathril_binding
from commander_gym.local_callback_context import current_cancel
from commander_gym.lmstudio_pilot import LMStudioPilotError
from commander_gym.pilot import ArgentumActionChoice, PilotContractError
from commander_gym.game_server_binding_lmstudio_sidecar import LocalBindingComponentResolver, local_component_ref
from commander_gym.identity import Pilot, IdentityRef
from commander_gym.pilot_composition import compose_pilot_runtime
from test_heuristic_policy import facts, fact, offer, observation, zone, decision
from test_lmstudio import config, FakeTransport, completion


def policy():
    doc = facts().document()
    for name, role in policy_document()['roles'].items():
        doc['cards'][name] = fact(manaCost='{1}{G}', subtypes=['Elf'] if role != 'utility' else [])
    doc['cards']['Forest'] = facts().document()['cards']['Forest']
    doc['cards']['Swamp'] = facts().document()['cards']['Swamp']
    return LathrilDeckSubsystem(CardFacts(doc))


def creature(name, **fields):
    return dict(name=name, cardTypes=['CREATURE'], subtypes=['Elf'], controllerId='me', power=2,
                toughness=2, manaCost='{1}{G}', isTapped=False, hasSummoningSickness=False, **fields)


class LathrilPolicyTests(unittest.TestCase):
    def test_colored_land_precedes_utility_and_action_is_fresh(self):
        p = policy();o = observation(offer('PlayLand', 1, card='lodge'), offer('PlayLand', 2, card='forest'))
        zone(o, 'Hand', 'me', lodge={'name':'Wirewood Lodge'}, forest={'name':'Forest'})
        before = deepcopy(o)
        self.assertEqual(p.try_choose(o).choice.action_id,2)
        self.assertEqual(o,before)
        o['legalActions'][1].update(actionId=8,semanticId='fresh')
        self.assertEqual(p.try_choose(o).choice.action_id,8)

    def test_recorded_mirror_opening_and_land_regressions(self):
        fixtures = json.loads((Path(__file__).parent / 'fixtures/lathril_mirror_regressions.json').read_text())
        p = policy()
        for seq, expected in [('558', 'Swamp'), ('1050', 'Forest')]:
            with self.subTest(sequence=seq):
                o = fixtures[seq]; c = p.try_choose(o).choice
                a = next(a for a in o['legalActions'] if a['actionId'] == c.action_id)
                self.assertEqual(o['state']['cards'][a['action']['cardId']]['name'], expected)
        for seq, expected in [('16', "Patriarch's Bidding"), ('77', 'Swamp')]:
            with self.subTest(sequence=seq):
                o = fixtures[seq]; c = p.try_choose(o).choice
                self.assertEqual([o['pendingDecision']['cards'][i]['name'] for i in c.response['selectedCards']], [expected])
        o = fixtures['73']; c = p.try_choose(o).choice
        self.assertEqual(next(a for a in o['legalActions'] if a['actionId'] == c.action_id)['kind'], 'KeepHand')

    def test_recorded_scry_uses_offered_card_and_current_land_needs(self):
        o = json.loads((Path(__file__).parent / 'fixtures/lathril_mirror_regressions.json').read_text())['scry']
        self.assertEqual(policy().try_choose(o).choice.response['selectedCards'], o['pendingDecision']['options'])
        o['state']['zones'] = []
        self.assertEqual(policy().try_choose(o).choice.response['selectedCards'], [])
        o['pendingDecision']['prompt'] = 'Choose a card to discard'
        self.assertIsNone(policy().try_choose(o).choice)

    def test_recorded_hand_limit_discards_utility_and_rejects_other_effects(self):
        o = json.loads((Path(__file__).parent / 'fixtures/lathril_mirror_regressions.json').read_text())['discard']
        c = policy().try_choose(o).choice
        self.assertEqual([o['state']['cards'][i]['name'] for i in c.response['selectedCards']], ['Staff of Domination'])
        o['pendingDecision']['prompt'] = 'Choose a creature to sacrifice'
        self.assertIsNone(policy().try_choose(o).choice)
        o['pendingDecision']['prompt'] = 'Discard down to 7 cards (choose 1 to discard)'
        o['pendingDecision']['nonSelectableOptions'] = ['e159']
        self.assertIsNone(policy().try_choose(o).choice)

    def test_recorded_shock_land_pays_life_only_for_development(self):
        o = json.loads((Path(__file__).parent / 'fixtures/lathril_mirror_regressions.json').read_text())['tomb']
        self.assertTrue(policy().try_choose(o).choice.response['choice'])
        me = next(x for x in o['state']['players'] if x['playerId'] == o['agentToAct'])
        me['life'] = 4
        self.assertFalse(policy().try_choose(o).choice.response['choice'])
        me['life'] = 40
        o['state']['zones'] = []
        self.assertFalse(policy().try_choose(o).choice.response['choice'])

    def test_recorded_rites_selects_only_offered_creature_and_handles_miss(self):
        o = json.loads((Path(__file__).parent / 'fixtures/lathril_mirror_regressions.json').read_text())['rites']
        self.assertEqual(policy().try_choose(o).choice.response['selectedCards'], ['e186'])
        o['pendingDecision']['options'] = []
        self.assertEqual(policy().try_choose(o).choice.response['selectedCards'], [])

    def test_library_search_scores_revealed_native_card_info(self):
        o = decision('SearchLibraryDecision','CardsSelectedResponse',{'selectedCards':'ENTITY_ID_ARRAY'},
                     context={'sourceName':'Demonic Tutor'},minSelections=1,maxSelections=1,
                     options=['a','b'],cardInfo={'a':{'name':'Staff of Domination'},'b':{'name':'Llanowar Elves'}})
        self.assertEqual(policy().try_choose(o).choice.response['selectedCards'], ['b'])

    def test_recorded_tutor_optional_search_takes_offered_acceleration(self):
        o = json.loads((Path(__file__).parent / 'fixtures/lathril_mirror_regressions.json').read_text())['tutor']
        chosen = policy().try_choose(o).choice.response['selectedCards']
        self.assertEqual(len(chosen), 1)
        self.assertIn(chosen[0], o['pendingDecision']['options'])
        self.assertEqual(policy_document()['roles'][o['pendingDecision']['cardInfo'][chosen[0]]['name']], 'mana_elf')
        o['pendingDecision']['options'] = []
        self.assertEqual(policy().try_choose(o).choice.response['selectedCards'], [])

    def test_recorded_free_tokens_require_exact_source_and_ability(self):
        o = json.loads((Path(__file__).parent / 'fixtures/lathril_mirror_regressions.json').read_text())['token']
        self.assertTrue(policy().try_choose(o).choice.response['choice'])
        p = o['pendingDecision']
        p['context']['sourceName'] = 'Lys Alana Huntmaster'
        p['context']['abilityIdentity']['abilityId'] = 'Lys Alana Huntmaster:1'
        p['prompt'] = 'you may create a 1/1 green Elf Warrior creature token.'
        self.assertTrue(policy().try_choose(o).choice.response['choice'])
        p['context']['abilityIdentity']['abilityId'] = 'other:1'
        self.assertIsNone(policy().try_choose(o).choice)

    def test_horn_native_reveal_contract_takes_offered_elf(self):
        o = decision('SelectCardsDecision','CardsSelectedResponse',{'selectedCards':'ENTITY_ID_ARRAY'},
                     context={'sourceName':"Herald's Horn"},minSelections=0,maxSelections=1,options=['elf'],
                     selectedLabel='Reveal and put into your hand',remainderLabel='Leave on top of your library',
                     cardInfo={'elf':{'name':'Llanowar Elves','typeLine':'Creature — Elf Druid','manaCost':'{G}'}})
        self.assertEqual(policy().try_choose(o).choice.response['selectedCards'], ['elf'])

    def test_cemetery_untaps_after_basic_and_tutor_has_execution_path(self):
        p = policy()
        self.assertTrue(p._enters_tapped({'name':'Woodland Cemetery'}, []))
        self.assertFalse(p._enters_tapped({'name':'Woodland Cemetery'}, [{'name':'Swamp'}]))
        o = observation(offer('PassPriority',0), offer('CastSpell',1,card='tutor'))
        zone(o,'Hand','me',tutor={'name':'Demonic Tutor','manaCost':'{1}{B}'})
        self.assertEqual(p.try_choose(o).choice.action_id,1)

    def test_choose_elf_by_exact_label_even_if_indices_change(self):
        p=policy()
        for options in [['Archon','Artificer','Elf'],['Elf','Archon']]:
            o=decision('ChooseOptionDecision','OptionChosenResponse',{'optionIndex':'INTEGER'},options=options,context={'sourceName':"Herald's Horn"})
            self.assertEqual(p.try_choose(o).choice.response['optionIndex'],options.index('Elf'))

    def test_unrepresented_choice_defers_to_fallback(self):
        p=policy();o=decision('ChooseOptionDecision','OptionChosenResponse',{'optionIndex':'INTEGER'},options=['foo','bar'],context={'sourceName':'Unknown'})
        self.assertIsNone(p.try_choose(o).choice)

    def test_development_is_not_vetoed_by_available_utility_or_mana(self):
        p=policy();o=observation(offer('PassPriority',0),offer('CastSpell',1,card='engine'),offer('CastSpell',2,card='staff'),offer('ActivateAbility',3,mana=True))
        zone(o,'Hand','me',engine=creature('Leaf-Crowned Visionary'),staff={'name':'Staff of Domination','manaCost':'{3}'})
        self.assertEqual(p.try_choose(o).choice.action_id,1)

    def test_do_not_float_mana_without_a_plan(self):
        p=policy();o=observation(offer('PassPriority',0),offer('ActivateAbility',1,mana=True))
        self.assertEqual(p.try_choose(o).choice.action_id,0)

    def test_mana_elf_before_lord_on_empty_board(self):
        p=policy();o=observation(offer('CastSpell',1,card='elf'),offer('CastSpell',2,card='lord'))
        zone(o,'Hand','me',elf=creature('Llanowar Elves'),lord=creature('Elvish Champion'))
        self.assertEqual(p.try_choose(o).choice.action_id,1)

    def test_unsupported_special_cast_is_not_invented_or_sent_to_model(self):
        o=observation(offer('CastSpell',1,card='elf'));o['legalActions'][0]['hasXCost']=True
        zone(o,'Hand','me',elf=creature('Llanowar Elves'))
        self.assertIsNone(policy().try_choose(o).choice)

    def test_dont_spend_recovery_or_sweeper_on_empty_board(self):
        o=observation(offer('PassPriority',0),offer('CastSpell',1,card='recovery'),offer('CastSpell',2,card='wrath'))
        zone(o,'Hand','me',recovery={'name':"Patriarch's Bidding"},wrath={'name':'Damnation'})
        self.assertEqual(policy().try_choose(o).choice.action_id,0)

    def test_opening_with_only_colorless_land_is_mulligan(self):
        o=observation(offer('KeepHand',0),offer('TakeMulligan',1));o['callbackKind']='decideMulligan'
        o['state']={'mulligan':{'mulliganCount':0,'hand':['a','b','c'],'cards':{'a':{'name':'Wirewood Lodge','typeLine':'Land','manaCost':''},'b':{'name':'Wirewood Lodge','typeLine':'Land','manaCost':''},'c':{'name':'Llanowar Elves','typeLine':'Creature','manaCost':'{G}'}}}}
        self.assertEqual(policy().try_choose(o).choice.action_id,1)
        o['state']['mulligan']['mulliganCount']=2
        self.assertEqual(policy().try_choose(o).choice.action_id,0)

    def test_opening_does_not_count_rite_as_acceleration_without_elves(self):
        o=observation(offer('KeepHand',0),offer('TakeMulligan',1));o['callbackKind']='decideMulligan'
        o['state']={'mulligan':{'mulliganCount':0,'hand':['g','c','rite','elf'],'cards':{
            'g':{'name':'Forest','typeLine':'Land','manaCost':''},'c':{'name':'Wirewood Lodge','typeLine':'Land','manaCost':''},
            'rite':{'name':'Cryptolith Rite','typeLine':'Enchantment','manaCost':'{1}{G}'},
            'elf':{'name':'Leaf-Crowned Visionary','typeLine':'Creature','manaCost':'{G}{G}'}}}}
        self.assertEqual(policy().try_choose(o).choice.action_id,1)
        o['state']['mulligan']['cards']['c']['name']='Forest'
        self.assertEqual(policy().try_choose(o).choice.action_id,0)

    def test_uncovered_choice_reaches_only_bounded_model(self):
        p=policy();cfg=config();transport=FakeTransport(completion('{"option":1}'))
        pilot=Pilot(pilot_id='test-lathril',revision='1',generalist_base=local_component_ref(cfg),skill_set=lathril_component_ref(p.facts))
        composed=compose_pilot_runtime(pilot,LocalBindingComponentResolver(cfg,transport,card_facts=p.facts))
        o=decision('ChooseOptionDecision','OptionChosenResponse',{'optionIndex':'INTEGER'},options=['unknown','other'],context={'sourceName':'Unknown'})
        c=composed.choose(o)
        self.assertEqual(c.response['optionIndex'],1)
        self.assertEqual(c.metadata['boundedFallback']['mode'],'frozen-choices')
        self.assertEqual(len(transport.calls),2)

    def test_policy_cannot_be_assigned_to_a_different_deck_or_primer(self):
        from types import SimpleNamespace
        doc=policy_document()
        wrong=SimpleNamespace(deck=SimpleNamespace(fingerprint='wrong'),deck_knowledge=SimpleNamespace(fingerprint=doc['primer_fingerprint']))
        with self.assertRaisesRegex(PilotContractError,'exact reviewed deck'):
            validate_lathril_binding(wrong)
        wrong.deck.fingerprint=doc['deck_fingerprint'];wrong.deck_knowledge.fingerprint='wrong'
        with self.assertRaisesRegex(PilotContractError,'exact reviewed deck'):
            validate_lathril_binding(wrong)

    def test_cancel_prevents_all_work(self):
        event=threading.Event();event.set();token=current_cancel.set(event)
        try:
            with self.assertRaisesRegex(LMStudioPilotError,'cancelled'):
                policy().try_choose(observation(offer('PassPriority',0)))
        finally:current_cancel.reset(token)

    def test_payment_only_uses_native_available_suggestion(self):
        o=decision('SelectManaSourcesDecision','ManaSourcesSelectedResponse',{'autoPay':'BOOLEAN','declined':'BOOLEAN','selectedSources':'ENTITY_ID_ARRAY','waterbendPermanents':'ENTITY_ID_ARRAY'},context={'sourceName':'Leaf-Crowned Visionary'},requiredCost='{G}',autoPaySuggestion=['elf'],availableSources=[{'entityId':'elf','name':'Elf','requiresSacrifice':False,'requiresTappingAnotherPermanent':False}],canDecline=True,waterbendPermanents=[])
        c=policy().try_choose(o).choice.response
        self.assertEqual(c['selectedSources'],['elf']);self.assertFalse(c['autoPay'])
        o['pendingDecision']['autoPaySuggestion']=['not-offered']
        self.assertTrue(policy().try_choose(o).choice.response['declined'])

    def test_lathril_connects_through_one_blocker_with_menace(self):
        attack=offer('DeclareAttackers',0);attack.update(validAttackers=['lathril'],validAttackTargets=['enemy'],parameterSpec={'allowedFields':{'attackers':'ENTITY_ID_MAP'}})
        o=observation(attack);zone(o,'Battlefield','me',lathril=creature('Lathril, Blade of the Elves',keywords=['MENACE']))
        zone(o,'Battlefield','enemy',blocker=creature('Elf'))
        self.assertEqual(policy().try_choose(o).choice.params['attackers'],{'lathril':'enemy'})
        o['legalActions'][0]['attackTargetsByAttacker']={'lathril':[]}
        self.assertEqual(policy().try_choose(o).choice.params['attackers'],{})

    def test_reserve_elves_for_drain_and_do_not_confuse_granted_mana(self):
        drain=offer('ActivateAbility',1);drain['action'].update(sourceId='lathril',abilityId='Lathril, Blade of the Elves:2')
        drain.update(additionalCostInfo={'costType':'TapPermanents','tapCount':10,'validTapTargets':['e'+str(i) for i in range(10)]},parameterSpec={'allowedFields':{'tappedPermanents':'ENTITY_ID_ARRAY'}})
        o=observation(offer('PassPriority',0),drain)
        zone(o,'Battlefield','me',lathril=creature('Lathril, Blade of the Elves'),**{'e'+str(i):creature('Elf') for i in range(10)})
        c=policy().try_choose(o).choice
        self.assertEqual(c.action_id,1);self.assertEqual(len(c.params['tappedPermanents']),10);self.assertNotIn('lathril',c.params['tappedPermanents'])
        o['legalActions'][1]['action']['abilityId']='Cryptolith Rite:1'
        self.assertIsNone(policy().try_choose(o).choice)
        o['legalActions'][1]['action']['abilityId']='Lathril, Blade of the Elves:2'
        o['state']['cards']['e0']['isTapped']=True
        self.assertIsNone(policy().try_choose(o).choice)

    def test_unsupported_blocking_is_explicit(self):
        a=offer('DeclareBlockers',0);a['validBlockers']=['elf']
        r=policy().try_choose(observation(a))
        self.assertIsNone(r.choice)
        self.assertIn('block-requirements',r.reason)

    def test_basic_block_uses_native_pairs_and_cap(self):
        a=offer('DeclareBlockers',0);a.update(validBlockers=['elf'],validBlockTargets={'elf':['attacker']},maxTotalBlockers=1,parameterSpec={'allowedFields':{'blockers':'ENTITY_ID_ARRAY_MAP'}})
        o=observation(a);zone(o,'Battlefield','me',elf=creature('Elf'))
        zone(o,'Battlefield','enemy',attacker=creature('Enemy'))
        self.assertEqual(policy().try_choose(o).choice.params['blockers'],{'elf':['attacker']})
        o['state']['cards']['attacker']['keywords']=['MENACE']
        self.assertEqual(policy().try_choose(o).choice.params['blockers'],{})
        o['state']['cards']['attacker']['keywords']=[];a['maxTotalBlockers']=0
        self.assertEqual(policy().try_choose(o).choice.params['blockers'],{})

    def test_finisher_is_held_until_unblocked_lethal_is_demonstrated(self):
        a=offer('CastSpell',1,card='finish');o=observation(offer('PassPriority',0),a)
        zone(o,'Hand','me',finish={'name':'Overwhelming Stampede'})
        zone(o,'Battlefield','me',elf=creature('Elf'))
        self.assertEqual(policy().try_choose(o).choice.action_id,0)
        o['state']['players'][1]['life']=4
        self.assertEqual(policy().try_choose(o).choice.action_id,1)
        zone(o,'Battlefield','enemy',blocker=creature('Enemy'))
        self.assertEqual(policy().try_choose(o).choice.action_id,0)

    def test_top_threats_defer_to_bounded_fallback_and_exclude_own_targets(self):
        p=policy();t=FakeTransport(completion('{"option":1}',usage={'prompt_tokens':100,'completion_tokens':5,'total_tokens':105}))
        a=offer('CastSpell',1,card='removal');a.update(requiresTargets=True,targetCount=1,minTargets=1,targetRequirements=None,validTargets=['mine','a','b'],parameterSpec={'allowedFields':{'targets':'ENTITY_ID_ARRAY'}})
        o=observation(offer('PassPriority',0),a)
        zone(o,'Hand','me',removal={'name':'Abrupt Decay'})
        zone(o,'Battlefield','me',mine=creature('Beast Whisperer'))
        zone(o,'Battlefield','enemy',a={**creature('Elf'),'power':10,'controllerId':'enemy'},b={**creature('Elf'),'power':10,'controllerId':'enemy'})
        result=p.try_choose(o)
        self.assertIsNone(result.choice)
        self.assertEqual(len(result.metadata['candidateChoices']),2)
        from commander_gym.bounded_policy_fallback import BoundedPolicyFallback
        c=BoundedPolicyFallback(config(max_prompt_bytes=6400,max_output_tokens=768),t,facts=p.facts).choose_with_policy_context(o,[{'metadata':result.metadata}])
        self.assertIn(c.params['targets'][0],['a','b']);self.assertNotIn('mine',c.params['targets'])
        self.assertIn('modelIo',c.metadata)
        self.assertEqual(c.metadata['boundedFallback']['candidateCount'],2)

    def test_hidden_or_administrative_fields_do_not_change_choice(self):
        o=observation(offer('PassPriority',0),offer('ActivateAbility',1,mana=True));p=policy()
        c=p.try_choose(o).choice
        o.update(adminState={'opponentHand':['secret']},hiddenLibraryOrder=['secret'])
        self.assertEqual(p.try_choose(o).choice.action_id,c.action_id)

    def test_skill_identity_is_distinct_and_changes_with_weights(self):
        f=policy().facts;original=lathril_component_ref(f)
        self.assertNotEqual(original,heuristic_component_ref(f))
        doc=policy_document();doc['weights']['elf']+=1
        with patch('commander_gym.lathril_policy.policy_document',return_value=doc):
            self.assertNotEqual(lathril_component_ref(f),original)


class BoundedAdvisorTests(unittest.TestCase):
    def choices(self):
        return [('remove threat A',ArgentumActionChoice(3,{'targets':['a']})),('remove threat B',ArgentumActionChoice(7,{'targets':['b']}))]

    def test_model_cannot_construct_actions_and_choice_is_exact(self):
        transport=FakeTransport(completion('{"option":1}',usage={'prompt_tokens':100,'completion_tokens':5,'total_tokens':105}))
        c=BoundedDeckAdvisor(config(),transport).choose('Remove which?',self.choices(),{'objective':'interaction'})
        self.assertEqual(c.action_id,7);self.assertEqual(c.params,{'targets':['b']})
        req=transport.calls[-1][1];self.assertLess(len(json.dumps(req).encode()),8192)
        self.assertNotIn('semanticId',json.dumps(req));self.assertNotIn('deckKnowledge',json.dumps(req))

    def test_model_invented_action_invalid_option_and_invalid_usage_fail(self):
        for content in ['{"option":2}','{"option":true}','{"option":0,"targets":["invented"]}']:
            with self.subTest(content=content),self.assertRaisesRegex(LMStudioPilotError,'outside_shortlist'):
                BoundedDeckAdvisor(config(),FakeTransport(completion(content))).choose('q',self.choices(),{})
        with self.assertRaisesRegex(LMStudioPilotError,'invalid_usage'):
            BoundedDeckAdvisor(config(),FakeTransport(completion('{"option":0}'))).choose('q',self.choices(),{})

    def test_large_context_fails_before_any_network_operation(self):
        t=FakeTransport()
        with self.assertRaisesRegex(LMStudioPilotError,'prompt_limit'):
            BoundedDeckAdvisor(config(),t).choose('q',self.choices(),{'text':'x'*9000})
        self.assertEqual(t.calls,[])

    def test_provider_failure_does_not_choose_first_or_open_generalist(self):
        t=FakeTransport(RuntimeError('transport stopped'))
        with self.assertRaises(RuntimeError):
            BoundedDeckAdvisor(config(),t).choose('q',self.choices(),{})


if __name__=='__main__':unittest.main()
