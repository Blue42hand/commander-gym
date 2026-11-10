"""Credential-free llmster transport with durable batch-wide admission limits."""
import fcntl
import json
from pathlib import Path
import time

from .batch_budget import BatchLimitError, atomic_json
from .lmstudio_provider import LMStudioTransport


class LocalBatchTransport:
    def __init__(self, inference, directory, plan, deadline, *, transport=None):
        self.config = inference
        self.root = Path(directory)
        self.plan, self.deadline = plan, deadline
        self.transport = transport or LMStudioTransport(inference)

    def request(self, path, body, *, timeout, cancel=None):
        remaining = self.deadline - time.time()
        if remaining <= 0:
            raise BatchLimitError('batch_time_limit')
        if path != '/v1/chat/completions':
            return self.transport.request(path, body, timeout=min(timeout, remaining), cancel=cancel)
        reserve = len(json.dumps(body, ensure_ascii=False).encode()) + self.config.max_output_tokens + self.config.template_reserve_tokens
        with self.root.joinpath('local.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            local = json.loads((self.root/'local.json').read_text())
            tokens = json.loads((self.root/'tokens.json').read_text())
            if tokens['limit'] != self.plan['max_tokens']:
                raise ValueError('batch_token_limit_changed')
            if local['requests'] >= self.plan['max_requests']:
                raise BatchLimitError('batch_request_limit')
            if tokens['reserved_or_used'] + reserve > tokens['limit']:
                raise BatchLimitError('batch_token_fence', limit=tokens['limit'], used=tokens['reserved_or_used'], reservation=reserve)
            tokens['reserved_or_used'] += reserve
            tokens['unsettled'] += 1
            local['requests'] += 1
            local['unsettled_requests'] += 1
            atomic_json(self.root/'tokens.json', tokens)
            atomic_json(self.root/'local.json', local)
        remaining = self.deadline - time.time()
        if remaining <= 0:
            raise BatchLimitError('batch_time_limit')
        response = self.transport.request(path, body, timeout=min(timeout, remaining), cancel=cancel)
        usage = response.get('usage')
        if isinstance(usage, dict):
            values = [usage.get(k) for k in ('prompt_tokens','completion_tokens','total_tokens')]
            if all(type(v) is int and v >= 0 for v in values) and values[0]+values[1] == values[2] and values[0] <= self.config.context_window and values[1] <= self.config.max_output_tokens:
                with self.root.joinpath('local.lock').open('a') as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    local = json.loads((self.root/'local.json').read_text())
                    tokens = json.loads((self.root/'tokens.json').read_text())
                    tokens['reserved_or_used'] += values[2] - reserve
                    tokens['reported_tokens'] += values[2]
                    tokens['unsettled'] -= 1
                    local['input_tokens'] += values[0]
                    local['output_tokens'] += values[1]
                    local['unsettled_requests'] -= 1
                    atomic_json(self.root/'tokens.json', tokens)
                    atomic_json(self.root/'local.json', local)
        return response
