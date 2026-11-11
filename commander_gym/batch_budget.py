"""Additional durable total-token fence; ambiguous requests retain reservations."""
import fcntl
import json
import os
from pathlib import Path
import threading
from collections.abc import Mapping


class BatchLimitError(ValueError):
    """Local admission rejection, with safe structured diagnostics only."""
    def __init__(self, reason, **details):
        super().__init__(reason)
        self.details = {'reason': reason, **details}


def limit_details(error):
    from .openai_run_budget import OpenAIRunBudgetError
    monetary_limits = {
        'absolute request limit reached before dispatch': 'batch_request_limit',
        'session request limit reached before dispatch': 'batch_request_limit',
        'the next OpenAI request exceeds the cumulative run cap': 'batch_cost_limit',
        'the next OpenAI request exceeds the session cap': 'batch_cost_limit',
        'dispatch deadline reached before reservation': 'batch_time_limit',
        'dispatch deadline reached after reservation': 'batch_time_limit',
    }
    # The pilot wraps provider errors; never publish arbitrary exception text.
    for _ in range(8):
        if isinstance(error, BatchLimitError): return error.details
        if isinstance(error, OpenAIRunBudgetError) and str(error) in monetary_limits:
            return {'reason': monetary_limits[str(error)]}
        if error is None: break
        error = error.__cause__
    return None


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream, allow_nan=False)
        stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


class TokenFence:
    def __init__(self, client, path, limit):
        self.client, self.path, self.limit = client, Path(path), limit
        self.responses = self

    def create(self, **request):
        reserve = len(json.dumps(request, default=str).encode()) + 4096 + 2048
        def transact(change):
            with self.path.with_suffix('.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                data = json.loads(self.path.read_text())  # Missing ledger fails closed.
                if data['limit'] != self.limit: raise ValueError('batch_token_limit_changed')
                change(data); atomic_json(self.path, data)
        def admit(data):
            if data['reserved_or_used'] + reserve > self.limit:
                raise BatchLimitError('batch_token_fence', limit=self.limit,
                                      used=data['reserved_or_used'], reservation=reserve)
            data['reserved_or_used'] += reserve; data['unsettled'] += 1
        transact(admit)
        result = self.client.responses.create(**request)
        usage = result.get('usage') if isinstance(result, Mapping) else getattr(result, 'usage', None)
        actual = usage.get('total_tokens') if isinstance(usage, Mapping) else getattr(usage, 'total_tokens', None)
        if type(actual) is int and actual >= 0:
            def settle(data):
                data['reserved_or_used'] += actual - reserve
                data['reported_tokens'] += actual; data['unsettled'] -= 1
            transact(settle)
        return result


class ChoiceGuard:
    """One attempt-wide callback limit across all four seats, with no fallback."""
    def __init__(self, maximum, deadline, progress):
        self.maximum, self.deadline, self.progress = maximum, deadline, Path(progress)
        self.count = 0
        self.lock = threading.Lock()

    def wrap(self, pilot):
        guard = self
        class Guarded:
            name, version = pilot.name, pilot.version
            def choose(self, observation):
                import time
                with guard.lock:
                    if guard.count >= guard.maximum or time.time() >= guard.deadline:
                        reason = 'batch_decision_limit' if guard.count >= guard.maximum else 'batch_time_limit'
                        atomic_json(guard.progress, {'state':'Limit reached','reason':reason,'decisions':guard.count,'observed_unix':time.time()})
                        raise BatchLimitError(reason)
                    guard.count += 1
                    atomic_json(guard.progress, {'state':'Active','decisions':guard.count,'observed_unix':time.time()})
                try: return pilot.choose(observation)
                except Exception as error:
                    with guard.lock:
                        details = limit_details(error)
                        atomic_json(guard.progress, {'state':'Limit reached' if details else 'Callback failed',
                            **(details or {}),'decisions':guard.count,'observed_unix':time.time()})
                    raise
        return Guarded()
