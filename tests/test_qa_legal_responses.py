import copy
import json
import unittest
from unittest.mock import patch

from commander_gym.qa_legal_responses import NativeLegalFixtureClient, CALLBACK_SCHEMA_HASH, PREFIX
from commander_gym.openai_responses_pilot import OpenAIResponsesPilot, _native_action_format, _native_decision_format
from commander_gym.cache_friendly_input import cache_friendly_observation_input
from commander_gym.observation_projection import compact_seat_observation


def offer(kind, semantic):
    return {"actionType": kind, "kind": kind, "action": {"type": kind, "playerId": "seat"},
            "semanticId": semantic, "parameterSpec": {"allowedFields": {}}}


def view(kind="chooseAction"):
    return {"type": "GameServerSeat", "callbackKind": kind,
            "schemaHash": CALLBACK_SCHEMA_HASH, "stateDigest": "a" * 64,
            "state": {"viewingPlayerId": "seat"}, "pendingDecision": None,
            "legalActions": [offer("PlayLand", "tempting"), offer("PassPriority", "current-pass")]}


def request(observation):
    return {"input": PREFIX + json.dumps(observation), "text": {"format":
            _native_decision_format(observation) or _native_action_format(observation)}}


class NativeLegalFixtureTests(unittest.TestCase):
    def test_chooses_current_pass_even_when_first_variant_needs_parameters(self):
        obs = view(); obs['legalActions'][0]['parameterSpec']['allowedFields'] = {'targets': 'ENTITY_ID_ARRAY'}
        result = json.loads(NativeLegalFixtureClient().create(**request(obs)).output_text)
        self.assertEqual(result, {'channel': 'action', 'choice': {'semanticId': 'current-pass', 'params': {}}})
        # Every call resolves a fresh current semantic identity, without replaying previous choices.
        obs['legalActions'][1]['semanticId'] = 'fresh-pass'
        result = json.loads(NativeLegalFixtureClient().create(**request(obs)).output_text)
        self.assertEqual(result['choice']['semanticId'], 'fresh-pass')

    def test_keeps_hand_instead_of_boolean_minimum_mulligan(self):
        obs = view('decideMulligan');obs['legalActions'] = [offer('TakeMulligan', 'take'), offer('KeepHand', 'keep')]
        self.assertEqual(json.loads(NativeLegalFixtureClient().create(**request(obs)).output_text)['choice']['semanticId'], 'keep')

    def test_bottom_is_exact_distinct_masked_offer_not_repeated_enum(self):
        obs = view('chooseBottomCards');obs['legalActions'] = []
        obs['pendingDecision'] = {'kind': 'BottomCards', 'requiresStructuredResponse': True,
            'cardsToPutOnBottom': 2, 'hand': ['c', 'a', 'b'], 'responseSpec': {
                'responseType': 'CardsSelectedResponse', 'requiredFields': {'selectedCards': 'ENTITY_ID_ARRAY'}}}
        result = json.loads(NativeLegalFixtureClient().create(**request(obs)).output_text)
        self.assertEqual(result['response']['selectedCards'], ['a', 'b'])
        obs['pendingDecision']['hand'] = ['a', 'a']
        with self.assertRaisesRegex(ValueError, 'unsupported'): NativeLegalFixtureClient().create(**request(obs))

    def test_current_schema_offer_and_parameterless_contract_must_agree(self):
        for mutate in (lambda o:o.update(callbackKind='paymentCorrection'),
                       lambda o:o.update(schemaHash='wrong'),
                       lambda o:o['legalActions'].pop(),
                       lambda o:o['legalActions'].append(copy.deepcopy(o['legalActions'][1])),
                       lambda o:o['legalActions'][1].update(parameterSpec={'allowedFields':{'x':'INTEGER'}}),
                       lambda o:o['legalActions'][1]['action'].update(type='Concede')):
            obs=view();mutate(obs)
            with self.subTest(obs=obs), self.assertRaisesRegex(ValueError, 'unsupported'):
                NativeLegalFixtureClient().create(**request(obs))
        req=request(view());req['text']['format']['schema']['properties']['choice']['anyOf'].pop()
        with self.assertRaisesRegex(ValueError, 'unsupported'): NativeLegalFixtureClient().create(**req)

    def test_zero_provider_usage_bounded_attempts_bytes_and_walltime(self):
        client=NativeLegalFixtureClient();response=client.create(**request(view()))
        self.assertEqual(response.usage.total_tokens,0)
        client.calls=128
        with self.assertRaisesRegex(ValueError,'bound'):client.create(**request(view()))
        # Exact synthetic origin avoids floating-point subtraction rounding at the deadline.
        client=NativeLegalFixtureClient();client._started=0.0
        with patch('commander_gym.qa_legal_responses.time.monotonic',return_value=899.0):
            self.assertEqual(client.create(**request(view())).usage.total_tokens,0)
        with patch('commander_gym.qa_legal_responses.time.monotonic',return_value=900.0):
            with self.assertRaisesRegex(ValueError,'bound'):client.create(**request(view()))
        req=request(view());req['instructions']='x'*(2*1024*1024)
        with self.assertRaisesRegex(ValueError,'unsupported'):NativeLegalFixtureClient().create(**req)

    def test_cache_and_compact_layouts_preserve_choices_without_private_state(self):
        obs=view();obs['state']['cards']={'visible':{'tapped':False}}
        for data in (obs,compact_seat_observation(obs)):
            req=request(obs);req['input']=cache_friendly_observation_input(data)
            self.assertEqual(json.loads(NativeLegalFixtureClient().create(**req).output_text)['choice']['semanticId'],'current-pass')

    def test_real_pilot_parser_resolves_current_offer_without_fallback(self):
        obs=view()
        for index, action in enumerate(obs['legalActions']): action['actionId']=index
        pilot=OpenAIResponsesPilot(client=NativeLegalFixtureClient(),model='qa-no-provider')
        result=pilot.choose({**obs})
        self.assertEqual(result.action_id,1)
        self.assertEqual(result.params,{})


if __name__ == '__main__': unittest.main()
