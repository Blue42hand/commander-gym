"""Masked synthetic contracts; real capture checks are a separate private qualification."""
from copy import deepcopy
from dataclasses import replace
import json
import hashlib
from pathlib import Path
import tempfile
import threading
import unittest

from commander_gym.heuristic_facts import CardFacts, heuristic_component_ref
from commander_gym.heuristic_policy import MaskedHeuristicSubsystem
from commander_gym.pilot import ArgentumActionChoice, PilotContractError
from commander_gym.local_callback_context import current_cancel
from commander_gym.lmstudio_pilot import LMStudioPilotError
from commander_gym.lmstudio_provider import LMStudioError
from commander_gym.pilot_composition import compose_pilot_runtime
from commander_gym.game_server_binding_lmstudio_sidecar import LocalBindingComponentResolver
from commander_gym.local_catalog import prepare_local_catalog, validate_local_catalog
from commander_gym.game_server_binding_lmstudio_sidecar import LocalBindingConfig, build_binding_lmstudio_sidecar, local_component_ref
from commander_gym.game_server_sidecar import GameServerSidecarConfig
from commander_gym.identity import Pilot
from commander_gym.heuristic_review import review_callbacks
from commander_gym.captured_choices import CALLBACK_SCHEMA_HASH
from test_game_server_binding_openai_sidecar import synthetic_catalog, free_port
from test_lmstudio import config, FakeTransport, completion

ENGINE = '12ab2f0ed5bd018334521dc52497e6444f26f5a9'


def fact(**changes):
    return dict(manaCost='{2}', types=['CREATURE'], subtypes=[], keywords=[], tags=[], spellPolarity=[],
                manaColors=[], simpleMana=False, simplePermanent=True, unconditionalUntapped=True,
                staticValue=1., repeatable=False, affectsOpponent=False, entersTapped=False,
                feeds=[], rewards=[], tribesRewarded=[], freeOptionalDraws={}, drawSensitive=False) | changes


def facts():
    cards = {'Forest': fact(manaCost='', types=['LAND'], subtypes=['Forest'], manaColors=['G'], simpleMana=True),
             'Swamp': fact(manaCost='', types=['LAND'], subtypes=['Swamp'], manaColors=['B'], simpleMana=True),
             'Plains': fact(manaCost='', types=['LAND'], subtypes=['Plains'], manaColors=['W'], simpleMana=True),
             'Elf': fact(manaCost='{G}', subtypes=['Elf']),
             'Expensive': fact(manaCost='{6}{G}', staticValue=2),
             'Banner': fact(types=['ARTIFACT'], tags=['ANTHEM', 'DRAW'], repeatable=True, simplePermanent=False),
             'Mana Rock': fact(types=['ARTIFACT'], simpleMana=True, manaColors=['C']),
             'Removal': fact(types=['INSTANT'], tags=['REMOVAL'], affectsOpponent=True),
             'Commander': fact(manaCost='{1}{G}', tags=['DRAW'], repeatable=True, simplePermanent=False)}
    return CardFacts(dict(schemaVersion=1, engineRevision=ENGINE, creatureTypes=['Elf', 'Archon'], cards=cards))


def offer(kind, index, *, card=None, mana=False):
    return dict(kind=kind, actionType=kind, actionId=index, semanticId=f'fixture:{index}',
                affordable=True, isAffordable=True, isManaAbility=mana, requiresTargets=False,
                parameterSpec={'allowedFields': {}}, action={'type': kind, 'playerId': 'me', **({'cardId': card} if card else {})})


def observation(*actions):
    return dict(type='GameServerSeat', agentToAct='me', perspectivePlayerId='me', terminated=False,
                legalActions=list(actions), pendingDecision=None, state=dict(
                    activePlayerId='me', currentStep='PRECOMBAT_MAIN', cards={}, zones=[],
                    players=[{'playerId': 'me', 'life': 40}, {'playerId': 'enemy', 'life': 40}]))


def zone(o, kind, owner, **cards):
    o['state']['cards'].update(cards)
    o['state']['zones'].append(dict(zoneId=dict(ownerId=owner, zoneType=kind), isVisible=True,
                                  cardIds=list(cards), size=len(cards)))


def decision(kind, rt, fields, **values):
    o = observation()
    o['pendingDecision'] = dict(kind=kind, decisionId='current', requiresStructuredResponse=True,
                                responseSpec=dict(responseType=rt, requiredFields=fields), **values)
    return o


class HeuristicPolicyTests(unittest.TestCase):
    def setUp(self):
        self.facts = facts()
        self.policy = MaskedHeuristicSubsystem(self.facts)

    def test_play_missing_color_land_before_passing_or_floating_mana(self):
        o = observation(offer('PassPriority', 0), offer('PlayLand', 1, card='g'), offer('PlayLand', 2, card='b'))
        zone(o, 'Hand', 'me', g={'name': 'Forest'}, b={'name': 'Swamp'}, e={'name': 'Elf', 'manaCost': '{G}'})
        before = deepcopy(o)
        r = self.policy.try_choose(o)
        self.assertEqual(r.choice.action_id, 1)
        self.assertEqual(r.metadata['reason'], 'land-before-development')
        self.assertEqual(before, o)

    def test_action_ids_are_rebound_on_each_fresh_offer(self):
        o = observation(offer('PassPriority', 0), offer('PlayLand', 1, card='g'))
        zone(o, 'Hand', 'me', g={'name': 'Forest'})
        self.assertEqual(self.policy.try_choose(o).choice.action_id, 1)
        o['legalActions'][1].update(actionId=8, semanticId='fixture:new')
        self.assertEqual(self.policy.try_choose(o).choice.action_id, 8)
        o['legalActions'].pop()
        self.assertEqual(self.policy.try_choose(o).choice.action_id, 0)

    def test_missing_color_basic_is_preferred_over_known_always_tapped_land(self):
        doc = self.facts.document()
        doc['cards']['Tapped Land'] = fact(types=['LAND'], entersTapped=True,
                                          unconditionalUntapped=False, simplePermanent=False)
        policy = MaskedHeuristicSubsystem(CardFacts(doc))
        o = observation(offer('PlayLand', 1, card='g'), offer('PlayLand', 2, card='t'))
        zone(o, 'Hand', 'me', g={'name': 'Forest'}, t={'name': 'Tapped Land'},
             e={'name': 'Elf', 'manaCost': '{G}'})
        self.assertEqual(policy.try_choose(o).choice.action_id, 1)
        o['state']['cards'].pop('e')
        o['state']['zones'][0]['cardIds'].remove('e')
        self.assertEqual(policy.try_choose(o).reason, 'heuristic-land-utility-needs-assessment')

    def test_mana_color_choice_uses_own_pips_and_defers_for_unread_purpose(self):
        doc = self.facts.document()
        doc['cards']['Mana Rock']['tags'] = ['RAMP']
        policy = MaskedHeuristicSubsystem(CardFacts(doc))
        o = decision('ChooseColorDecision', 'ColorChosenResponse', {'color': 'STRING'},
                     availableColors=['WHITE', 'GREEN'], context={'sourceName': 'Mana Rock'})
        o['knownDeck'] = {'cards': {'Elf': 10}}
        self.assertEqual(policy.try_choose(o).choice.response['color'], 'GREEN')
        o['pendingDecision']['context']['sourceName'] = 'Commander'
        self.assertEqual(policy.try_choose(o).reason, 'heuristic-color-purpose-unknown')

    def test_late_game_land_drop_is_used_even_after_all_mana_was_spent(self):
        # Remainder sequences 3739/3821 and 5053/5135 passed with a land drop.
        o = observation(offer('PassPriority', 0), offer('PlayLand', 1, card='b'),
                        offer('ActivateAbility', 2, mana=True))
        o['state']['turnNumber'] = 23
        o['legalActions'][2].update(affordable=False, isAffordable=False)
        zone(o, 'Battlefield', 'me', g={'name': 'Forest', 'isTapped': True})
        zone(o, 'Hand', 'me', b={'name': 'Swamp'}, need={'name': 'Spell', 'manaCost': '{B}'})
        for step in ['PRECOMBAT_MAIN', 'POSTCOMBAT_MAIN']:
            o['state']['currentStep'] = step
            self.assertEqual(self.policy.try_choose(o).choice.action_id, 1)

    def test_optional_free_draws_are_recognized_by_native_ability_not_prompt_text(self):
        # Remainder sequences 4367..4741 each paid ~100s for a free draw.
        doc = self.facts.document()
        doc['cards']['Draw Engine'] = fact(types=['CREATURE'], tags=['DRAW'], simplePermanent=False,
                                          freeOptionalDraws={'Draw Engine:1': 1})
        policy = MaskedHeuristicSubsystem(CardFacts(doc))
        o = decision('YesNoDecision', 'YesNoResponse', {'choice': 'BOOLEAN'}, yesAvailable=True,
                     prompt='You may draw a card', context=dict(sourceName='Draw Engine', phase='RESOLUTION',
                                                               abilityIdentity={'abilityId': 'Draw Engine:1'}))
        o['state']['players'][0]['librarySize'] = 40
        zone(o, 'Battlefield', 'me', engine={'name': 'Draw Engine'})
        self.assertTrue(policy.try_choose(o).choice.response['choice'])
        for change in ['low-library', 'different-ability', 'punishment', 'replacement', 'commander-return']:
            c = deepcopy(o)
            if change == 'low-library': c['state']['players'][0]['librarySize'] = 1
            if change == 'different-ability': c['pendingDecision']['context']['abilityIdentity']['abilityId'] = 'Draw Engine:paid'
            if change == 'punishment':
                doc['cards']['Enemy Draw Trap'] = fact(drawSensitive=True)
                zone(c, 'Battlefield', 'enemy', trap={'name': 'Enemy Draw Trap'})
            if change == 'replacement': c['state']['players'][0]['activeEffects'] = ['draw replacement']
            if change == 'commander-return':
                c['pendingDecision']['context'] = {'sourceName': 'Commander', 'phase': 'STATE_BASED'}
                c['pendingDecision']['prompt'] = 'Command zone or exile?'
            self.assertIsNone(MaskedHeuristicSubsystem(CardFacts(doc)).try_choose(c).choice, change)

    def test_nonmana_utility_ability_remains_a_model_decision(self):
        # Arcane Lighthouse was activated at remainder sequence 3863.
        o = observation(offer('PassPriority', 0), offer('ActivateAbility', 1))
        o['legalActions'][1]['action']['sourceId'] = 'utility'
        zone(o, 'Battlefield', 'me', utility={'name': 'Unknown utility land'})
        self.assertIsNone(self.policy.try_choose(o).choice)

    def test_targeting_ranks_only_native_legal_targets_using_structural_polarity(self):
        doc = self.facts.document()
        doc['cards']['Removal']['spellPolarity'] = ['HARMFUL']
        policy = MaskedHeuristicSubsystem(CardFacts(doc))
        a = offer('CastSpell', 1, card='r')
        a.update(requiresTargets=True, targetCount=1, minTargets=1, validTargets=['ours', 'theirs'],
                 parameterSpec={'allowedFields': {'targets': 'ENTITY_ID_ARRAY'}})
        o = observation(a)
        zone(o, 'Hand', 'me', r={'name': 'Removal'})
        zone(o, 'Battlefield', 'me', ours={'name': 'Commander', 'controllerId': 'me', 'power': 8})
        zone(o, 'Battlefield', 'enemy', theirs={'name': 'Commander', 'controllerId': 'enemy', 'power': 5})
        self.assertEqual(policy.try_choose(o).choice.params['targets'], ['theirs'])
        a['validTargets'] = ['ours']
        self.assertIsNone(policy.try_choose(o).choice)
        a['validTargets'] = ['theirs']; doc['cards']['Removal']['spellPolarity'] = ['UNKNOWN']
        self.assertIsNone(MaskedHeuristicSubsystem(CardFacts(doc)).try_choose(o).choice)

    def test_develop_creature_instead_of_tapping_mana_or_passing(self):
        o = observation(offer('PassPriority', 0), offer('CastSpell', 1, card='e'), offer('ActivateAbility', 2, mana=True))
        zone(o, 'Hand', 'me', e={'name': 'Elf', 'manaCost': '{G}'})
        r = self.policy.try_choose(o)
        self.assertEqual(r.choice.action_id, 1)
        self.assertEqual(r.choice.params, {})

    def test_casting_competes_with_interaction_and_defers(self):
        o = observation(offer('CastSpell', 1, card='e'), offer('CastSpell', 2, card='r'))
        zone(o, 'Hand', 'me', e={'name': 'Elf', 'manaCost': '{G}'}, r={'name': 'Removal'})
        self.assertEqual(self.policy.try_choose(o).reason, 'heuristic-competing-special-action')

    def test_disabled_entries_do_not_block_development_but_conflicting_flags_defer(self):
        o = observation(offer('CastSpell', 1, card='e'), offer('CastSpell', 2, card='r'))
        zone(o, 'Hand', 'me', e={'name': 'Elf', 'manaCost': '{G}'}, r={'name': 'Removal'})
        o['legalActions'][1].update(affordable=False, isAffordable=False)
        self.assertEqual(self.policy.try_choose(o).choice.action_id, 1)
        o['legalActions'][1]['affordable'] = True
        self.assertEqual(self.policy.try_choose(o).reason, 'heuristic-unknown-affordability')

    def test_plain_mana_pass_and_trigger_board_escalation(self):
        o = observation(offer('PassPriority', 0), offer('ActivateAbility', 1, mana=True))
        o['legalActions'][1]['action']['sourceId'] = 'g'
        zone(o, 'Battlefield', 'me', g={'name': 'Forest'})
        self.assertEqual(self.policy.try_choose(o).metadata['reason'], 'no-use-for-plain-mana')
        zone(o, 'Battlefield', 'enemy', c={'name': 'Commander'})
        self.assertEqual(self.policy.try_choose(o).reason, 'heuristic-mana-side-effects-or-resources')

    def test_unknown_source_and_enchanted_source_are_not_plain_mana(self):
        for name, extra in [('Unknown', {}), ('Forest', {'attachments': ['aura']})]:
            o = observation(offer('PassPriority', 0), offer('ActivateAbility', 1, mana=True))
            o['legalActions'][1]['action']['sourceId'] = 'g'
            zone(o, 'Battlefield', 'me', g={'name': name, **extra})
            self.assertIsNone(self.policy.try_choose(o).choice)

    def test_stack_and_opponent_priority_escalate(self):
        o = observation(offer('CastSpell', 1, card='e'))
        zone(o, 'Hand', 'me', e={'name': 'Elf', 'manaCost': '{G}'})
        o['state']['activePlayerId'] = 'enemy'
        self.assertEqual(self.policy.try_choose(o).reason, 'heuristic-strategic-priority-window')
        zone(o, 'Stack', 'enemy', spell={'name': 'Removal'})
        self.assertEqual(self.policy.try_choose(o).reason, 'heuristic-stack-interaction')

    def test_tribal_choice_uses_labels_and_registered_own_deck(self):
        o = decision('ChooseOptionDecision', 'OptionChosenResponse', {'optionIndex': 'INTEGER'},
                     options=['Archon', 'Elf'], context={'sourceName': 'Banner'})
        o['knownDeck'] = {'cards': {'Elf': 20}}
        self.assertEqual(self.policy.try_choose(o).choice.response['optionIndex'], 1)
        o['pendingDecision']['options'].reverse()
        self.assertEqual(self.policy.try_choose(o).choice.response['optionIndex'], 0)
        o['pendingDecision']['context']['sourceName'] = 'Unknown'
        self.assertEqual(self.policy.try_choose(o).reason, 'heuristic-choice-purpose-unknown')

    def test_tribal_prior_does_not_read_hidden_opponent_hand(self):
        o = decision('ChooseOptionDecision', 'OptionChosenResponse', {'optionIndex': 'INTEGER'},
                     options=['Archon', 'Elf'], context={'sourceName': 'Banner'})
        o['knownDeck'] = {'cards': {'Elf': 20}}
        o['state']['zones'] = [dict(zoneId=dict(ownerId='enemy', zoneType='Hand'), isVisible=False, cardIds=[], size=20)]
        before = self.policy.try_choose(o).choice
        o['state']['cards']['not-in-a-visible-zone'] = {'name': 'Secret', 'subtypes': ['Archon']}
        self.assertEqual(self.policy.try_choose(o).choice.response, before.response)

    def test_forced_decisions_and_cancellation_are_distinct(self):
        cases = [decision('ChooseOptionDecision', 'OptionChosenResponse', {'optionIndex': 'INTEGER'}, options=['only']),
                 decision('ChooseNumberDecision', 'NumberChosenResponse', {'number': 'INTEGER'}, minValue=2, maxValue=2),
                 decision('YesNoDecision', 'YesNoResponse', {'choice': 'BOOLEAN'}, yesAvailable=False),
                 decision('OrderObjectsDecision', 'OrderedResponse', {'orderedObjects': 'ENTITY_ID_ARRAY'}, objects=['one'])]
        for o in cases:
            with self.subTest(kind=o['pendingDecision']['kind']):
                self.assertIsNotNone(self.policy.try_choose(o).choice)
                o['pendingDecision']['canCancel'] = True
                self.assertIsNone(self.policy.try_choose(o).choice)

    def test_selection_constraints_and_ordering_prevent_false_forced_answer(self):
        o = decision('SelectCardsDecision', 'CardsSelectedResponse', {'selectedCards': 'ENTITY_ID_ARRAY'},
                     options=['a', 'b'], minSelections=2, maxSelections=2)
        self.assertEqual(self.policy.try_choose(o).choice.response['selectedCards'], ['a', 'b'])
        for extra in [{'ordered': True}, {'selectionLimits': {'a': 2}}, {'onePerColor': True}, {'nonSelectableOptions': ['a']}]:
            changed = deepcopy(o)
            changed['pendingDecision'].update(extra)
            self.assertIsNone(self.policy.try_choose(changed).choice)

    def test_payment_is_never_misclassified_as_forced(self):
        o = decision('SelectManaSourcesDecision', 'ManaSourcesSelectedResponse',
                     {'autoPay': 'BOOLEAN', 'selectedSources': 'ENTITY_ID_ARRAY'},
                     canAutoPayNow=True, availableSources=[{'entityId': 'g'}])
        self.assertEqual(self.policy.try_choose(o).reason, 'heuristic-payment-is-strategic')
        o['nativePaymentError'] = 'failed'
        self.assertEqual(self.policy.try_choose(o).reason, 'heuristic-payment-correction')

    def test_mulligan_wrong_colors_and_bounded_repeated_mulligans(self):
        o = observation(offer('KeepHand', 0), offer('TakeMulligan', 1))
        o['state'] = {'mulligan': dict(hand=['a', 'b', 'c'], mulliganCount=0, cardsToPutOnBottom=0,
                                     cards={'a': {'name': 'Forest', 'typeLine': 'Land'},
                                            'b': {'name': 'Forest', 'typeLine': 'Land'},
                                            'c': {'name': 'Elf', 'typeLine': 'Creature', 'manaCost': '{G}'}})}
        # Small retained hands are protected, just as after repeated London mulligans.
        self.assertEqual(self.policy.try_choose(o).choice.action_id, 0)
        m = o['state']['mulligan']
        m['hand'] += ['d', 'e', 'f', 'h']
        m['cards'].update({i: {'name': 'Wrong Color', 'typeLine': 'Creature', 'manaCost': '{W}'} for i in ['c', 'd', 'e', 'f', 'h']})
        self.assertEqual(self.policy.try_choose(o).choice.action_id, 1)
        m['mulliganCount'] = 2
        self.assertEqual(self.policy.try_choose(o).choice.action_id, 0)

    def test_bottom_preserves_two_lands_and_early_spell(self):
        cards = {'a': {'name': 'Forest', 'typeLine': 'Land'}, 'b': {'name': 'Swamp', 'typeLine': 'Land'},
                 'c': {'name': 'Plains', 'typeLine': 'Land'}, 'd': {'name': 'Elf', 'typeLine': 'Creature', 'manaCost': '{G}'},
                 'e': {'name': 'Expensive', 'typeLine': 'Creature', 'manaCost': '{6}{G}'}}
        o = decision('BottomCards', 'CardsSelectedResponse', {'selectedCards': 'ENTITY_ID_ARRAY'},
                     hand=list(cards), cards=cards, cardsToPutOnBottom=2)
        selected = self.policy.try_choose(o).choice.response['selectedCards']
        self.assertEqual(selected, ['c', 'e'])

    def test_complex_combat_escalates_simple_uncontested_lethal_does_not(self):
        a = offer('DeclareAttackers', 0)
        a.update(validAttackers=['e'], validAttackTargets=['enemy'], parameterSpec={'allowedFields': {'attackers': 'ENTITY_ID_MAP'}})
        o = observation(a)
        zone(o, 'Battlefield', 'me', e={'name': 'Elf', 'power': 2, 'toughness': 2, 'cardTypes': ['CREATURE']})
        o['state']['players'][1]['life'] = 2
        self.assertEqual(self.policy.try_choose(o).choice.params['attackers'], {'e': 'enemy'})
        o['state']['players'].append({'playerId': 'third', 'life': 40})
        self.assertEqual(self.policy.try_choose(o).reason, 'heuristic-complex-or-multiplayer-combat')

    def test_cancelled_callback_never_returns_a_heuristic_choice(self):
        cancel = threading.Event(); cancel.set()
        token = current_cancel.set(cancel)
        try:
            with self.assertRaisesRegex(LMStudioPilotError, 'cancelled'):
                self.policy.try_choose(observation(offer('PassPriority', 0)))
        finally:
            current_cancel.reset(token)

    def test_facts_are_immutable_and_changes_change_component_identity(self):
        original = heuristic_component_ref(self.facts)
        document = self.facts.document()
        document['cards']['Elf']['staticValue'] += 1
        self.assertNotEqual(original, heuristic_component_ref(CardFacts(document)))
        document = self.facts.document()
        document['cards']['Elf']['subtypes'].append('Archon')
        self.assertEqual(original, heuristic_component_ref(self.facts))
        with self.assertRaises(ValueError):
            CardFacts({**document, 'executable': '/tmp/untrusted'})


class HeuristicRoutingTests(unittest.TestCase):
    def test_offline_review_checks_native_seat_digest_before_counting_coverage(self):
        o = observation(offer('PassPriority', 0))
        o.update(callbackKind='chooseAction', recentGameLog=[])
        o['state']['viewingPlayerId'] = 'me'
        raw = json.dumps(o)
        evidence = dict(version=2, correlationId='callback', schemaHash=CALLBACK_SCHEMA_HASH,
                        observationBody=raw, stateDigest=hashlib.sha256(raw.encode()).hexdigest())
        row = dict(kind='seat_callback', sequence=1, payload=dict(observation=o, decision_evidence=evidence,
                                                                choice={'metadata': {}}))
        result = review_callbacks([row], facts(), ENGINE)
        self.assertEqual(result['counts']['handled'], 1)
        self.assertEqual(result['scope'], 'counterfactual-position-coverage-only')
        row['payload']['decision_evidence']['stateDigest'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            review_callbacks([row], facts(), ENGINE)

    def test_large_late_game_observation_bypasses_model_only_when_policy_handles_it(self):
        # The final callback exceeded the model's prompt fence before dispatch.
        transport = FakeTransport(completion('{"option":0}'))
        pilot = Pilot(pilot_id='hybrid', revision='1', skill_set=heuristic_component_ref(facts()),
                      generalist_base=local_component_ref(config()))
        runtime = compose_pilot_runtime(pilot, LocalBindingComponentResolver(config(), transport, card_facts=facts()))
        o = observation(offer('PassPriority', 0), offer('PlayLand', 1, card='g'))
        zone(o, 'Hand', 'me', g={'name': 'Forest'})
        o['state']['gameLog'] = ['retained evidence ' * 1000]
        original = deepcopy(o)
        self.assertEqual(runtime.choose(o).action_id, 1)
        self.assertEqual(transport.calls, [])
        o['state']['activePlayerId'] = 'enemy'
        runtime.choose(o)
        self.assertEqual(len(transport.calls), 2)
        self.assertNotIn('retained evidence', json.dumps(transport.calls[1][1]))
        self.assertEqual(original['state']['gameLog'], o['state']['gameLog'])

    def test_model_escalation_records_reason_and_failure_does_not_fabricate_an_action(self):
        transport = FakeTransport(completion('{"option":0}'))
        pilot = Pilot(pilot_id='hybrid', revision='1', skill_set=heuristic_component_ref(facts()),
                      generalist_base=local_component_ref(config()))
        runtime = compose_pilot_runtime(pilot, LocalBindingComponentResolver(config(), transport, card_facts=facts()))
        o = observation(offer('PassPriority', 0), offer('ActivateAbility', 1))
        o['legalActions'][0]['semanticId'] = 'argentum-action-v1:pass'
        o['state']['activePlayerId'] = 'enemy'
        result = runtime.choose(o)
        routing = result.metadata['routing']
        self.assertEqual(result.action_id, 0)
        self.assertEqual(routing['handledBy']['role'], 'local_generalist')
        self.assertIn('heuristic-strategic-priority-window', routing['escalationReasons'])
        self.assertEqual(len(transport.calls), 2)
        request = transport.calls[1][1]
        self.assertIn('option', request['response_format']['json_schema']['schema']['properties'])
        self.assertEqual(result.metadata['boundedFallback']['mode'], 'frozen-choices')
        broken = FakeTransport(LMStudioError('deadline_exceeded'))
        runtime = compose_pilot_runtime(pilot, LocalBindingComponentResolver(config(), broken, card_facts=facts()))
        with self.assertRaisesRegex(LMStudioPilotError, 'deadline_exceeded'):
            runtime.choose(o)

    def test_catalog_opt_in_pins_skill_and_routes_with_no_provider_call(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'source'; source.mkdir()
            catalog = synthetic_catalog(source)
            fp = root / 'facts.json'; fp.write_text(json.dumps(facts().document()))
            target = root / 'hybrid'
            registry = prepare_local_catalog(catalog, source, target, config(), card_facts=fp)
            transport = FakeTransport()
            server = build_binding_lmstudio_sidecar(LocalBindingConfig(
                GameServerSidecarConfig('fixture', port=free_port()), config(), target/'catalog.json', target,
                engine_revision=ENGINE), transport=transport)
            try:
                seat = server.resolve_seat('me', server.controller_profiles[0]['id'], 'game')
                o = observation(offer('PassPriority', 0), offer('PlayLand', 1, card='g'))
                zone(o, 'Hand', 'me', g={'name': 'Forest'})
                result = seat._pilot.choose(o)
                self.assertEqual(result.action_id, 1)
                self.assertEqual(result.metadata['routing']['handledBy']['role'], 'skill')
                self.assertEqual(transport.calls, [])
                self.assertEqual(registry.binding_fingerprints, validate_local_catalog(target/'catalog.json', target, config()).binding_fingerprints)
            finally:
                server.server_close()
            with self.assertRaisesRegex(PilotContractError, 'native engine revision'):
                build_binding_lmstudio_sidecar(LocalBindingConfig(
                    GameServerSidecarConfig('fixture', port=free_port()), config(), target/'catalog.json', target,
                    engine_revision='0'*40))
            changed = facts().document(); changed['cards']['Elf']['staticValue'] += 1
            (target/'local/card-facts.json').write_text(json.dumps(changed))
            with self.assertRaises((PilotContractError, ValueError)):
                validate_local_catalog(target/'catalog.json', target, config())


if __name__ == '__main__':
    unittest.main()
