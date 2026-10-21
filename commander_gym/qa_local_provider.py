"""Bounded local-provider fixtures for the authenticated isolated QA guest.

No HTTP/SDK transport, credential reader or model endpoint is constructed here.
The configured local model/catalog identity is preserved; response IDs and capture
pins identify actual execution as a fake. This module never launches a game.
"""
from copy import deepcopy
import json
import math
import threading
import time

from .lmstudio_provider import LMStudioConfig, LMStudioError, require_loaded_model
from .manual_runtime_profile import require
from .qa_legal_responses import (
    MAX_CALLS, MAX_REQUEST_BYTES, MAX_SECONDS, PREFIX, legal_fixture_value,
)

LOCAL_TOKEN = 'commander-gym-isolated-local-qa-placeholder-only'
LOCAL_PROBES = ('local-provider-isolation', 'local-recorder-peer', 'local-failure-cancellation')
LOCAL_GATES = dict(zip(LOCAL_PROBES, ('localProviderIsolation', 'localRecorderPeer', 'localFailureCancellation')))


class NativeLegalLocalTransport:
    """Implement the existing transport contract using current masked offers only."""
    def __init__(self, config: LMStudioConfig):
        self.config = config
        self.calls = 0
        self._started = time.monotonic()
        self._lock = threading.Lock()

    def request(self, path, body, *, timeout, cancel=None):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise LMStudioError('deadline_exceeded')
        if cancel is not None and cancel.is_set(): raise LMStudioError('cancelled')
        if path not in ('/api/v1/models', '/v1/chat/completions'):
            raise LMStudioError('unsupported_endpoint')
        with self._lock:
            if self.calls >= MAX_CALLS * 2 or time.monotonic() - self._started >= MAX_SECONDS:
                raise LMStudioError('qa_local_fixture_bound')
            self.calls += 1
            ordinal = self.calls
        if path == '/api/v1/models':
            if body is not None: raise LMStudioError('qa_local_fixture_request')
            return {'models': [{'key': 'qa-local-fixture', 'type': 'llm', 'format': 'qa-fixture',
                'loaded_instances': [{'id': self.config.model, 'config': {'context_length': self.config.context_window}}],
                'capabilities': {'reasoning': {'allowed_options': ['off']}}}]}
        try:
            require(type(body) is dict and body.get('model') == self.config.model, 'qa_local_fixture_model')
            require(len(json.dumps(body, allow_nan=False).encode()) <= min(MAX_REQUEST_BYTES, self.config.max_prompt_bytes), 'qa_local_fixture_size')
            messages = body['messages']
            require(type(messages) is list and len(messages) == 2
                    and [m.get('role') for m in messages] == ['system', 'user'], 'qa_local_fixture_messages')
            observation = json.loads(messages[1]['content'])
            if isinstance(observation, dict) and 'format' in observation:
                from .observation_projection import expand_seat_observation
                observation = expand_seat_observation(observation)
            require(body['response_format']['type'] == 'json_schema', 'qa_local_fixture_schema')
            format_ = deepcopy(body['response_format']['json_schema'])
            value = legal_fixture_value({'input': PREFIX + json.dumps(observation), 'text': {'format': format_}})
            from .lmstudio_pilot import _schema_check
            _schema_check(format_['schema'], value, validate=True)
        except (KeyError, TypeError, IndexError, RecursionError, ValueError):
            raise LMStudioError('qa_local_fixture_unsupported') from None
        if cancel is not None and cancel.is_set(): raise LMStudioError('cancelled')
        return {'id': f'qa-local-fixture-{ordinal}', 'model': self.config.model,
                'choices': [{'finish_reason': 'stop', 'message': {
                    'role': 'assistant', 'content': json.dumps(value)}}],
                'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0},
                'stats': {'qaExecution': 'fake-local-native-legal', 'externalProviderCalls': 0}}


def build_local_server(runtime):
    """Called only after the root-authenticated QA child identity checks."""
    from pathlib import Path
    from .game_server_binding_lmstudio_sidecar import LocalBindingConfig, build_binding_lmstudio_sidecar
    from .game_server_sidecar import GameServerSidecarConfig
    from .manual_runtime_local import local_ipc, local_recording_registry
    from .recorder_bridge import RemoteCaptureSink
    profile = runtime.profile
    require(profile['purpose'] == 'qualification' and 'localInference' in profile, 'qa_local_profile')
    config = LMStudioConfig(**profile['localInference']['config'])
    local_recording_registry(profile)
    transport = NativeLegalLocalTransport(config)
    require_loaded_model(transport.request('/api/v1/models', None, timeout=min(10, config.timeout_seconds)), config)
    catalog = profile['artifacts']['localCatalog']['root']
    sink = RemoteCaptureSink(local_ipc(profile) / 'local.sock', recorder_uid=profile['identities']['nativeUid'])
    return build_binding_lmstudio_sidecar(LocalBindingConfig(
        GameServerSidecarConfig(LOCAL_TOKEN, port=profile['localInference']['port'], require_manual_human_game=True), config,
        Path(catalog) / profile['localInference']['catalog']['roster'], Path(catalog)),
        transport=transport, game_capture=sink)


def capture_pins(pins, profile):
    execution = {'providerMode': 'fake', 'credentialSource': 'qa-placeholder-only'}
    if 'localInference' in profile:
        execution['localProvider'] = 'fake-local-native-legal'
    return {**pins, 'models': {'provider': 'qa-native-legal-fixture', 'model': 'none', 'configured': pins['models']},
            'config': {**pins['config'], 'qaExecution': execution}}


def require_local_ready(profile):
    """Fixed loopback authenticated catalog check before native QA admission."""
    from .qa_local_probes import _catalog
    from .manual_runtime_local import local_recording_registry
    registry = local_recording_registry(profile)
    status, payload = _catalog(profile['localInference']['port'], LOCAL_TOKEN)
    require(status == 200 and payload == {'profiles': list(registry.profile_payloads())}, 'qa_local_not_ready')


def validate_probe_result(name, result, profile, nonce):
    from .manual_runtime_profile import digest
    require(name in LOCAL_GATES and 'localInference' in profile, 'qa_local_probe_role')
    require(type(result) is dict and set(result) == {'kind', 'gate', 'nonce', 'runtimeId', 'observations', 'externalProviderCalls', 'productionQualificationIssued', 'activationEligible'}
            and result['kind'] == 'isolated-local-qa-observation' and result['gate'] == LOCAL_GATES[name]
            and result['nonce'] == nonce and result['runtimeId'] == digest(profile)
            and type(result['observations']) is dict and result['observations']
            and type(result['externalProviderCalls']) is int and result['externalProviderCalls'] == 0
            and result['productionQualificationIssued'] is False and result['activationEligible'] is False, 'qa_local_probe_identity')
    return result
