"""Synthetic masked callbacks only; no game recordings or live provider calls."""
from copy import deepcopy
import hashlib
import json
import unittest

from commander_gym.captured_choices import CALLBACK_SCHEMA_HASH, SCHEMA_HASH
from commander_gym.game_journal import JournalError
from commander_gym.game_server_binding_openai_sidecar import (
    BUILTIN_ALL_UNAFFORDABLE_CYCLE_PASS_COMPONENT_REF,
    BUILTIN_FORGE_EXPLICIT_WAIT_COMPONENT_REF,
    BUILTIN_NATIVE_CERTIFIED_FORCED_PASS_COMPONENT_REF,
    OpenAIBindingPilotComponentResolver,
)
from commander_gym.game_server_openai_sidecar import OpenAIGameServerSidecarConfig
from commander_gym.game_server_seat import GameServerSeatAdapter, GameServerSeatError
from commander_gym.identity import Pilot
from commander_gym.native_forced_pass import NativeForcedPassSubsystem
from commander_gym.pilot_composition import compose_pilot_runtime, PilotSubsystemSpec
from commander_gym.deck_package import ArtifactRef
from commander_gym.pilot import PilotContractError


def offer(kind, index, *, affordable=False, mana=False):
    return {
        'actionType': kind, 'semanticId': f'fixture-action-{index}',
        'isAffordable': affordable, 'isManaAbility': mana,
        'parameterSpec': {'allowedFields': {}},
        'action': {'type': kind, 'playerId': 'seat'},
    }


def fixture():
    state = {
        'viewingPlayerId': 'seat', 'priorityPlayerId': 'seat', 'isGameOver': False,
        'zones': [{'zoneId': {'ownerId': 'seat', 'zoneType': 'Graveyard'},
                   'cardIds': ['graveyard-card']}],
    }
    flashback = offer('CastWithFlashback', 1)
    flashback.update(sourceZone='GRAVEYARD', manaCostString='{2}{U}')
    flashback['action'] = {
        'type': 'CastSpell', 'playerId': 'seat', 'cardId': 'graveyard-card',
        'useAlternativeCost': True, 'alternativeCostType': 'FLASHBACK',
    }
    return state, [offer('PassPriority', 0, affordable=True), flashback,
                   offer('ActivateAbility', 2, mana=True)]


def evidence(state, actions, *, version=2, pending=None):
    # Independent construction of the native wire aliases, matching Argentum's
    # AiCallbackEvidence. The test's entire menu is certified, never a shortlist.
    legal = []
    for i, action in enumerate(actions):
        item = dict(deepcopy(action), actionId=i, kind=action['actionType'])
        if type(item.get('isAffordable')) is bool:
            item.setdefault('affordable', item['isAffordable'])
        legal.append(item)
    body = dict(type='GameServerSeat', state=state, legalActions=legal,
                pendingDecision=pending, recentGameLog=[], perspectivePlayerId='seat',
                agentToAct='seat', terminated=False)
    if version == 2:
        body['callbackKind'] = 'chooseAction'
    raw = json.dumps(body, separators=(',', ':'))
    return dict(version=version, correlationId='synthetic-callback', observationBody=raw,
                schemaHash=SCHEMA_HASH if version == 1 else CALLBACK_SCHEMA_HASH,
                stateDigest=hashlib.sha256(raw.encode()).hexdigest())


class FakeClient:
    def __init__(self, semantic_id='fixture-action-0', params=None):
        self.responses = self
        self.calls = 0
        self.semantic_id, self.params = semantic_id, params or {}

    def create(self, **request):
        self.calls += 1
        return {'status': 'completed', 'output_text': json.dumps({
            'channel': 'action', 'choice': {
                'semanticId': self.semantic_id, 'params': self.params,
            },
        })}


def runtime(client, component=BUILTIN_NATIVE_CERTIFIED_FORCED_PASS_COMPONENT_REF):
    resolver = OpenAIBindingPilotComponentResolver(
        config=OpenAIGameServerSidecarConfig(token='fixture', api_key='fixture', port=12345),
        client=client, manual_uncapped=True,
    )
    pilot = Pilot(pilot_id='synthetic-forced-pass', revision='r1',
                  deterministic_policy=component,
                  escalation_provider=BUILTIN_FORGE_EXPLICIT_WAIT_COMPONENT_REF)
    return compose_pilot_runtime(pilot, resolver)


class NativeForcedPassTests(unittest.TestCase):
    def test_binding_knowledge_preserves_certificate_without_recertifying_mutation(self):
        from commander_gym.binding_resolver import _KnowledgeBoundPilot, BindingResolutionError
        from commander_gym.native_forced_pass import _VerifiedNativeMenu
        state, actions = fixture()
        client = FakeClient()
        adapter = GameServerSeatAdapter(_KnowledgeBoundPilot(runtime(client), 'Synthetic primer'), 'seat')
        result = adapter.choose_action(state, actions, None, decision_evidence=evidence(state, actions))
        self.assertEqual(client.calls, 0)
        self.assertEqual(result.metadata['routing']['attempts'][0]['metadata']['reason'], 'native-only-affordable-pass')

        observation = _VerifiedNativeMenu(json.loads(evidence(state, actions)['observationBody']))
        enriched = observation.with_deck_knowledge('Synthetic primer')
        self.assertEqual({k: v for k, v in enriched.items() if k != 'deckKnowledge'}, observation)
        self.assertNotIn('deckKnowledge', observation)
        enriched['deckKnowledge'] = 'Changed primer'
        self.assertEqual(NativeForcedPassSubsystem().try_choose(enriched).reason, 'native-menu-evidence-mismatch')
        for mutate in (lambda o: o['legalActions'].pop(),
                       lambda o: o['state'].update(priorityPlayerId='other')):
            changed = deepcopy(observation)
            mutate(changed)
            with self.assertRaisesRegex(BindingResolutionError, 'native-menu-evidence-mismatch'):
                _KnowledgeBoundPilot(runtime(client), 'Synthetic primer').choose(changed)
        with self.assertRaisesRegex(ValueError, 'already contains deckKnowledge'):
            observation.with_deck_knowledge('Synthetic primer').with_deck_knowledge('Duplicate')

        # A normal mapping cannot acquire native authority through enrichment.
        plain = json.loads(json.dumps(observation))
        class Inspect:
            def choose(self, value):
                return value
        restored = _KnowledgeBoundPilot(Inspect(), 'Synthetic primer').choose(plain)
        self.assertEqual(NativeForcedPassSubsystem().try_choose(restored).reason, 'native-menu-evidence-missing')

    def run_case(self, state, actions, *, certified=True, client=None, version=2):
        client = client or FakeClient()
        records = []
        adapter = GameServerSeatAdapter(runtime(client), 'seat', provenance_sink=records.append)
        result = adapter.choose_action(state, actions, None,
            decision_evidence=evidence(state, actions, version=version) if certified else None)
        return result, client, records

    def test_unavailable_flashback_reproduces_old_wake_and_new_zero_provider_calls(self):
        state, actions = fixture()
        old_client = FakeClient()
        old = GameServerSeatAdapter(runtime(old_client, BUILTIN_ALL_UNAFFORDABLE_CYCLE_PASS_COMPONENT_REF), 'seat')
        old.choose_action(state, actions, None, decision_evidence=evidence(state, actions))
        self.assertEqual(old_client.calls, 1)
        for version in (1, 2):
            with self.subTest(native_evidence_version=version):
                result, client, records = self.run_case(state, actions, version=version)
                self.assertEqual(client.calls, 0)
                self.assertEqual(result.action, actions[0]['action'])
                self.assertEqual(result.params, {})
                routing = result.metadata['routing']
                self.assertFalse(routing['escalated'])
                self.assertEqual(routing['handledBy']['component']['artifactId'], 'native-certified-forced-pass')
                self.assertEqual(routing['attempts'][0]['metadata']['reason'], 'native-only-affordable-pass')
                self.assertEqual(routing['attempts'][0]['metadata']['nativeStateDigest'],
                                 records[0].decision_evidence['stateDigest'])
                # The process-local certificate adds no serialized model fields.
                self.assertNotIn('verified_digest', json.dumps(records[0].observation))

    def test_pass_only_is_certified_and_each_new_callback_is_checked(self):
        state, actions = fixture()
        client = FakeClient('fixture-action-2')
        adapter = GameServerSeatAdapter(runtime(client), 'seat')
        for _ in range(20):
            adapter.choose_action(state, actions, None, decision_evidence=evidence(state, actions))
        self.assertEqual(client.calls, 0)
        actions[2]['isAffordable'] = True
        result = adapter.choose_action(state, actions, None, decision_evidence=evidence(state, actions))
        self.assertEqual(client.calls, 1)
        self.assertEqual(result.action, actions[2]['action'])
        result, client, _ = self.run_case(state, actions[:1])
        self.assertEqual(client.calls, 0)

    def test_every_affordable_alternative_keeps_provider_choice(self):
        for kind in ('CastWithFlashback', 'CastWithAlternativeCost', 'CastSpell', 'ActivateAbility'):
            with self.subTest(kind=kind):
                state, actions = fixture()
                actions[1]['actionType'] = kind
                actions[1]['action']['type'] = 'ActivateAbility' if kind == 'ActivateAbility' else 'CastSpell'
                actions[1]['isAffordable'] = True
                client = FakeClient('fixture-action-1')
                result, client, _ = self.run_case(state, actions, client=client)
                self.assertEqual(client.calls, 1)
                self.assertEqual(result.action, actions[1]['action'])
                self.assertEqual(result.metadata['routing']['escalationReasons'], ('affordable-non-pass-action',))

    def test_incomplete_or_unknown_evidence_defers_without_guessing_from_params(self):
        mutations = [
            lambda s, a: a[1].update(isAffordable=None),
            lambda s, a: a[1].pop('isAffordable'),
            lambda s, a: a[1].update(affordable=True),
            lambda s, a: a[1].update(actionType='UnknownAction'),
            lambda s, a: a[1]['action'].update(alternativeCostType='ESCAPE'),
            lambda s, a: a[1].update(sourceZone='EXILE'),
            lambda s, a: s['zones'][0].update(cardIds=[]),
            lambda s, a: s.update(priorityPlayerId='other'),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                state, actions = fixture()
                mutate(state, actions)
                # Unavailable/empty parameters never prove unaffordability.
                client = FakeClient()
                adapter = GameServerSeatAdapter(runtime(client), 'seat')
                # Native aliases must preserve explicit contradictory values.
                ev = evidence(state, actions) if 'isAffordable' in actions[1] else None
                if 'affordable' in actions[1]:
                    ev = None
                result = adapter.choose_action(state, actions, None, decision_evidence=ev)
                self.assertEqual(client.calls, 1)
                self.assertTrue(result.metadata['routing']['escalated'])
        state, actions = fixture()
        result, client, _ = self.run_case(state, actions, certified=False)
        self.assertEqual(client.calls, 1)
        self.assertEqual(result.metadata['routing']['escalationReasons'], ('native-menu-evidence-missing',))
        # Even a pass-only subset must not acquire completeness merely by its size.
        _, client, _ = self.run_case(state, actions[:1], certified=False)
        self.assertEqual(client.calls, 1)

    def test_affordable_alternate_cost_is_not_hidden_by_unpayable_base_cost(self):
        state, actions = fixture()
        alternate = offer('CastWithAlternativeCost', 3, affordable=True)
        alternate.update(parameterSpec={'allowedFields': {'exiledCards': 'ENTITY_ID_ARRAY'}})
        alternate['action'] = {'type': 'CastSpell', 'playerId': 'seat',
                               'cardId': 'hand-card', 'useAlternativeCost': True}
        actions.append(alternate)
        client = FakeClient('fixture-action-3', {'exiledCards': ['blue-card']})
        result, client, _ = self.run_case(state, actions, client=client)
        self.assertEqual(client.calls, 1)
        self.assertEqual(result.action, alternate['action'])
        self.assertEqual(result.params, {'exiledCards': ['blue-card']})

    def test_missing_parameter_spec_does_not_override_native_affordability(self):
        state, actions = fixture()
        actions[1].pop('parameterSpec')
        # Explicit native false is sufficient; parameters are not the proof.
        _, client, _ = self.run_case(state, actions)
        self.assertEqual(client.calls, 0)
        actions[1]['isAffordable'] = True
        _, client, _ = self.run_case(state, actions)
        self.assertEqual(client.calls, 1)
        actions[1]['isAffordable'] = None
        _, client, _ = self.run_case(state, actions)
        self.assertEqual(client.calls, 1)

    def test_truncated_callback_stale_evidence_and_wrong_seat_are_rejected(self):
        state, actions = fixture()
        ev = evidence(state, actions)
        for changed_state, changed_actions, changed_evidence in (
            (state, actions[:1], ev),
            ({**state, 'priorityPlayerId': 'other'}, actions, ev),
            ({**state, 'viewingPlayerId': 'other'}, actions, ev),
            (state, actions, {**ev, 'stateDigest': '0' * 64}),
        ):
            with self.subTest(state=changed_state, actions=changed_actions):
                client = FakeClient()
                with self.assertRaises((GameServerSeatError, JournalError)):
                    GameServerSeatAdapter(runtime(client), 'seat').choose_action(
                        changed_state, changed_actions, None, decision_evidence=changed_evidence)
                self.assertEqual(client.calls, 0)

    def test_mutation_or_serialization_loses_certificate(self):
        state, actions = fixture()
        from unittest.mock import patch
        captured = []
        original = NativeForcedPassSubsystem.try_choose
        def capture(subsystem, observation):
            captured.append(deepcopy(observation))
            return original(subsystem, observation)
        with patch.object(NativeForcedPassSubsystem, 'try_choose', capture):
            self.run_case(state, actions)
        observation = captured[0]
        handler = NativeForcedPassSubsystem()
        self.assertIsNotNone(handler.try_choose(observation).choice)
        restored = json.loads(json.dumps(observation))
        self.assertEqual(handler.try_choose(restored).reason, 'native-menu-evidence-missing')
        for mutate in (lambda o: o['legalActions'].pop(),
                       lambda o: o['state'].update(priorityPlayerId='other')):
            changed = deepcopy(observation)
            mutate(changed)
            self.assertEqual(handler.try_choose(changed).reason, 'native-menu-evidence-mismatch')

    def test_component_requires_exact_identity_and_deterministic_role(self):
        resolver = OpenAIBindingPilotComponentResolver(
            config=OpenAIGameServerSidecarConfig(token='fixture', api_key='fixture', port=12345), client=FakeClient())
        ref = BUILTIN_NATIVE_CERTIFIED_FORCED_PASS_COMPONENT_REF
        for role, changed in (
            ('frontier_escalation', ref),
            ('deterministic', ArtifactRef(kind=ref.kind, artifact_id=ref.artifact_id,
                                         version=ref.version, digest='sha256:' + '0' * 64)),
        ):
            with self.assertRaises(PilotContractError):
                resolver.resolve(PilotSubsystemSpec(role=role, ordinal=0, ref=changed))
