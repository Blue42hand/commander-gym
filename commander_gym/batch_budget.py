"""Additional durable total-token fence; ambiguous requests retain reservations."""
import fcntl
import json
import os
from pathlib import Path
import threading
from collections.abc import Mapping


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
            if data['reserved_or_used'] + reserve > self.limit: raise ValueError('batch_token_fence')
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
                        atomic_json(guard.progress, {'state':'Limit reached','decisions':guard.count,'observed_unix':time.time()})
                        raise ValueError('batch_decision_or_time_limit')
                    guard.count += 1
                    atomic_json(guard.progress, {'state':'Active','decisions':guard.count,'observed_unix':time.time()})
                try: return pilot.choose(observation)
                except Exception:
                    with guard.lock:
                        atomic_json(guard.progress, {'state':'Callback failed','decisions':guard.count,'observed_unix':time.time()})
                    raise
        return Guarded()
