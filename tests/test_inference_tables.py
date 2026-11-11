from copy import deepcopy
import json
import random
import base64
import gzip
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from commander_gym.inference_tables import FORMAT, REFERENCE, compact_shared_view, expand_shared_view
from commander_gym.observation_projection import (
    compact_inference_observation, expand_inference_observation, _compact_inference_v3,
)


def observation():
    return {
        'deckKnowledge': 'A complete unchanged deck guide. ' * 30,
        'state': {'deck': [{'name': 'Forest', 'imageUri': 'https://cards.example/forest'}],
                  'cards': {f'e{i}': {'id': f'e{i}', 'name': 'Forest', 'isTapped': bool(i % 2),
                                     'oracleText': 'A long exact rule text that is repeated across visible cards. ' * 3,
                                     'imageUri': 'https://cards.example/very-long-public-image-identifier-123456789.jpg',
                                     'extra': None if i % 2 else []} for i in range(25)},
                  'gameLog': [{'type': 'reveal', 'cardId': f'e{i}', 'text': 'Public event. ' * 20}
                              for i in range(25)]},
        'legalActions': [{'semanticId': f'argentum-action-v1:{i:064x}', 'actionId': i,
                          'isAffordable': bool(i % 2),
                          'action': {'type': 'CastSpell', 'cardId': f'e{i}', 'targets': [],
                                     'xValue': None, 'paymentStrategy': {'type': 'AutoPay'},
                                     'manyNativeParameters': {f'optionalCost{k}': None for k in range(12)}},
                          'parameterSpec': {'allowedFields': {'targets': 'ENTITY_ID_ARRAY'},
                                            'targetRequirements': {'nativeRequirement': 'unchanged ' * 20}}}
                         for i in range(10)],
        'pendingDecision': None,
    }


class InferenceTablesTests(unittest.TestCase):
    def test_complete_view_round_trip_reduces_bytes_preserving_choices_and_history(self):
        original = observation()
        before = deepcopy(original)
        legacy = _compact_inference_v3(original)
        packed = compact_inference_observation(original)
        self.assertEqual(packed['format'], FORMAT)
        self.assertEqual(expand_inference_observation(packed), original)
        self.assertEqual(original, before)
        self.assertLess(len(json.dumps(packed)), len(json.dumps(legacy)) * .8)
        self.assertTrue(packed['actionTemplates'])
        self.assertTrue(packed['cardColumns'])
        # No response alias mapping: exact native strings remain in the payload.
        for action in original['legalActions']:
            self.assertIn(action['semanticId'], json.dumps(packed))

    def test_missing_null_false_zero_and_heterogeneous_shapes_remain_distinct(self):
        for seed in range(15):
            rng = random.Random(seed)
            original = observation()
            values = [None, False, 0, True, 1, [], {}, '', '\u00e9\u2603']
            for card in original['state']['cards'].values():
                card['mixed'] = deepcopy(rng.choice(values))
                if rng.choice([True, False]):
                    card['sometimesAbsent'] = deepcopy(rng.choice(values))
            packed = compact_inference_observation(original)
            self.assertEqual(json.dumps(expand_inference_observation(packed), sort_keys=True),
                             json.dumps(original, sort_keys=True))

    def test_small_inputs_retain_legacy_view_and_reserved_keys_fall_back(self):
        base = _compact_inference_v3({'state': {'cards': {}}, 'legalActions': []})
        self.assertEqual(compact_shared_view(base), base)
        original = observation()
        original['nativeExtension'] = {REFERENCE: 0}
        self.assertEqual(compact_shared_view(_compact_inference_v3(original)), _compact_inference_v3(original))
        self.assertEqual(expand_inference_observation(compact_inference_observation(original)), original)

    def test_references_are_request_local_and_decoded_values_do_not_alias(self):
        packed = compact_inference_observation(observation())
        first = expand_shared_view(packed)
        untouched = deepcopy(packed)
        first['observation']['state']['cards']['e0']['oracleText'] = 'changed'
        self.assertEqual(packed, untouched)
        restored = expand_inference_observation(packed)
        self.assertNotEqual(restored['state']['cards']['e0']['oracleText'], 'changed')
        other = observation()
        other['state']['cards']['e0']['oracleText'] = 'Different rule. ' * 10
        self.assertEqual(expand_inference_observation(compact_inference_observation(other)), other)

    def test_invalid_cycles_forward_indices_and_reference_objects_rejected(self):
        for bad in [{REFERENCE: True}, {REFERENCE: -1}, {REFERENCE: 100000},
                    {REFERENCE: 0, 'extra': 1}, {REFERENCE: '0'}]:
            packed = compact_inference_observation(observation())
            packed['sharedValues'] = [bad]
            with self.assertRaises(ValueError):
                expand_inference_observation(packed)
        packed = compact_inference_observation(observation())
        packed['sharedValues'] = [{REFERENCE: 1}, 'forward']
        with self.assertRaises(ValueError):
            expand_inference_observation(packed)

    def test_bad_card_shapes_and_action_template_overrides_rejected(self):
        packed = compact_inference_observation(observation())
        base = expand_shared_view(packed)
        # Make an unshared envelope so corruptions address the format itself.
        envelope = {'format': FORMAT, 'sharedValues': [], 'actionTemplates': [{'type': 'CastSpell'}],
                    'cardColumns': [], 'view': base}
        envelope['view']['observation']['legalActions'][0]['action'] = [0, {'type': 'PassPriority'}]
        with self.assertRaises(ValueError):
            expand_shared_view(envelope)
        envelope['actionTemplates'] = []
        envelope['cardColumns'] = [['id', 'id']]
        with self.assertRaises(ValueError):
            expand_shared_view(envelope)

    def test_pilot_sends_lossless_view_and_unchanged_response_schema(self):
        from commander_gym.lmstudio_pilot import LMStudioPilot, _cache_ordered_observation
        from commander_gym.openai_responses_pilot import _without_live_routing
        from tests.test_lmstudio import FakeTransport, action_observation, config
        original = action_observation()
        original['state'] = observation()['state']
        models = {'models': [{'key': 'fixture-model', 'type': 'llm', 'format': 'gguf',
                             'loaded_instances': [{'id': 'fixture-model', 'config': {'context_length': 131072}}]}]}
        transport = FakeTransport(models=models)
        plain = FakeTransport(models=models)
        cfg = config(context_window=131072, max_prompt_bytes=122880)
        before = deepcopy(original)
        chosen = LMStudioPilot(cfg, transport=plain).choose(original)
        compact = LMStudioPilot(config(context_window=131072, max_prompt_bytes=122880,
                                      compact_observation=True), transport=transport).choose(original)
        request = transport.calls[1][1]
        sent = json.loads(request['messages'][1]['content'])
        self.assertEqual(sent['format'], FORMAT)
        self.assertIn('$gymValue', request['messages'][0]['content'])
        self.assertEqual(expand_inference_observation(sent), _without_live_routing(original))
        self.assertEqual(request['response_format'], plain.calls[1][1]['response_format'])
        self.assertEqual(compact.action_id, chosen.action_id)
        self.assertEqual(original, before)
        first = json.dumps(_cache_ordered_observation(compact_inference_observation(observation())))
        changed = observation()
        changed['legalActions'].append(deepcopy(changed['legalActions'][0]))
        second = json.dumps(_cache_ordered_observation(compact_inference_observation(changed)))
        self.assertEqual(first.split('"cards"')[0], second.split('"cards"')[0])

    def test_capture_audit_reads_rotated_codecs_without_network_or_mutation(self):
        from scripts.audit_local_inference_inputs import audit_capture
        from commander_gym.lmstudio_pilot import LMStudioPilot
        from tests.test_lmstudio import FakeTransport, action_observation, config
        original = action_observation()
        original['state'] = observation()['state']
        models = {'models': [{'key': 'fixture-model', 'type': 'llm', 'format': 'gguf',
                             'loaded_instances': [{'id': 'fixture-model', 'config': {'context_length': 131072}}]}]}
        cfg = config(context_window=131072, max_prompt_bytes=122880, compact_observation=True)
        with patch('commander_gym.lmstudio_pilot.compact_inference_observation', _compact_inference_v3):
            choice = LMStudioPilot(cfg, transport=FakeTransport(models=models)).choose(original)
        record = {'kind': 'decision_started', 'payload': {'observation': original}}
        compressed = {'encoding': 'gzip-base64',
                      'data': base64.b64encode(gzip.compress(json.dumps(record).encode())).decode()}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / '000000.jsonl'
            second = root / '000001.jsonl'
            first.write_text(json.dumps(compressed) + '\n')
            second.write_text(json.dumps({'kind': 'seat_callback', 'sequence': 2,
                                         'payload': {'choice': {'metadata': choice.metadata}}}) + '\n')
            before = [p.read_bytes() for p in (first, second)]
            with patch('socket.socket', side_effect=AssertionError('network forbidden')):
                result = audit_capture(root, cfg)
            self.assertEqual(result['observations_round_tripped'], 1)
            self.assertEqual(result['model_requests_round_tripped'], 1)
            self.assertTrue(result['response_schemas_unchanged'])
            self.assertLess(result['new_total_request_bytes'], result['old_total_request_bytes'])
            self.assertEqual([p.read_bytes() for p in (first, second)], before)
            self.assertEqual(result['sources'][0]['sha256'], hashlib.sha256(before[0]).hexdigest())


if __name__ == '__main__':
    unittest.main()
