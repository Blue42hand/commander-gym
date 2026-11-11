#!/usr/bin/env python3
"""Read-only capture audit. Never dispatches inference or changes runtime config."""

import argparse
import base64
from contextlib import nullcontext
from dataclasses import replace
import gzip
import hashlib
import json
from pathlib import Path
import statistics
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from commander_gym.lmstudio_pilot import LMStudioPilot, LMStudioPilotError
from commander_gym.lmstudio_provider import LMStudioConfig, LMStudioError
from commander_gym.observation_projection import (
    compact_inference_observation, expand_inference_observation, _compact_inference_v3,
)


def request_bytes(request):
    # Match the production pre-dispatch byte guard, including the outer JSON escapes.
    return len(json.dumps(request, ensure_ascii=False).encode())


class OfflineTransport:
    def __init__(self, config):
        self.config = config
        self.request_body = None

    def request(self, path, body, **_):
        if path == '/api/v1/models':
            return {'models': [{'key': self.config.model, 'type': 'llm', 'format': 'gguf',
                                'loaded_instances': [{'id': self.config.model,
                                                      'config': {'context_length': self.config.context_window}}]}]}
        if path != '/v1/chat/completions':
            raise AssertionError('unexpected offline audit operation')
        self.request_body = body
        raise LMStudioError('offline_audit_no_dispatch')


def build_offline(observation, config, *, legacy=False):
    # The larger ceiling exists only in this in-memory audit, to measure requests
    # even if they exceed production's configured guard. No runtime files are written.
    audit = replace(config, context_window=262144, max_prompt_bytes=253952, compact_observation=True)
    transport = OfflineTransport(audit)
    try:
        with (patch('commander_gym.lmstudio_pilot.compact_inference_observation', _compact_inference_v3)
              if legacy else nullcontext()):
            LMStudioPilot(audit, transport=transport).choose(observation)
    except LMStudioPilotError as error:
        if str(error) != 'offline_audit_no_dispatch':
            raise
    if transport.request_body is None:
        raise AssertionError('request was not constructed')
    return transport.request_body


def equal_json(left, right):
    return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)


def audit_capture(root, config, failed_observation=None):
    rows = []
    sources = []
    observations = 0
    for path in sorted(root.glob('*.jsonl')):
        before = path.stat()
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for line in stream:
                digest.update(line)
                record = json.loads(line)
                if record.get('encoding') == 'gzip-base64':
                    record = json.loads(gzip.decompress(base64.b64decode(record['data'])))
                payload = record.get('payload', {})
                if record.get('kind') == 'decision_started':
                    original = payload['observation']
                    if not equal_json(expand_inference_observation(compact_inference_observation(original)), original):
                        raise AssertionError('recorded observation did not round-trip')
                    observations += 1
                if record.get('kind') != 'seat_callback':
                    continue
                attempts = payload.get('choice', {}).get('metadata', {}).get('modelIo', {}).get('attempts', [])
                for attempt in attempts:
                    old = attempt['request']
                    original = expand_inference_observation(json.loads(old['messages'][1]['content']))
                    new = build_offline(original, config)
                    if new['response_format'] != old['response_format']:
                        raise AssertionError('native response schema changed')
                    restored = expand_inference_observation(json.loads(new['messages'][1]['content']))
                    # The production builder may strip live routing identities.
                    from commander_gym.openai_responses_pilot import _without_live_routing
                    if not equal_json(restored, _without_live_routing(original)):
                        raise AssertionError('recorded model input did not round-trip')
                    rows.append({'source_sequence': record.get('sequence'),
                                 'old_request_bytes': request_bytes(old),
                                 'new_request_bytes': request_bytes(new)})
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise RuntimeError('capture changed during audit')
        sources.append({'name': path.name, 'sha256': digest.hexdigest(), 'bytes': after.st_size})
    if not sources or not observations or not rows:
        raise ValueError('capture contains no auditable observations and model requests')
    old_total = sum(r['old_request_bytes'] for r in rows)
    new_total = sum(r['new_request_bytes'] for r in rows)
    result = {
        'scope': 'Offline exact JSON reconstruction and request byte counts; no model inference or token/latency claim.',
        'capture': str(root), 'sources': sources, 'observations_round_tripped': observations,
        'model_requests_round_tripped': len(rows), 'response_schemas_unchanged': True,
        'production_byte_limit': config.max_prompt_bytes,
        'old_total_request_bytes': old_total, 'new_total_request_bytes': new_total,
        'total_reduction_percent': round(100 * (1 - new_total / old_total), 2),
        'old_max_request_bytes': max(r['old_request_bytes'] for r in rows),
        'new_max_request_bytes': max(r['new_request_bytes'] for r in rows),
        'median_new_request_bytes': statistics.median(r['new_request_bytes'] for r in rows),
        'new_requests_over_production_limit': sum(r['new_request_bytes'] > config.max_prompt_bytes for r in rows),
        'requests': rows,
    }
    if failed_observation is not None:
        original = json.loads(failed_observation.read_text())
        old = build_offline(original, config, legacy=True)
        new = build_offline(original, config)
        result['failed_position'] = {'old_request_bytes': request_bytes(old),
                                     'new_request_bytes': request_bytes(new),
                                     'reduction_percent': round(100 * (1 - request_bytes(new) / request_bytes(old)), 2),
                                     'within_production_limit': request_bytes(new) <= config.max_prompt_bytes}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--failed-observation', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    config = LMStudioConfig(**json.loads(args.config.read_text())['local_inference'])
    if args.output.resolve().is_relative_to(args.capture.resolve()):
        parser.error('audit output must be outside the immutable capture')
    result = audit_capture(args.capture, config, args.failed_observation)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({k: v for k, v in result.items() if k not in {'sources', 'requests'}}))


if __name__ == '__main__':
    main()
