"""Private native lifecycle heartbeat. Never invokes updater drain/resume operations."""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import socket
import stat
import threading
import time
from typing import Any, Mapping

from .game_journal import JournalError, _private_dir
from .native_game_capture import NativeGameCapture


class RecordingHealthReporter:
    def __init__(self, capture: NativeGameCapture, socket_path: Path) -> None:
        self.capture, self.socket_path = capture, socket_path
        self._stop = threading.Event()
        self._stop_requested = False
        self._thread: threading.Thread | None = None
        self.last_error: str | None = None

    @classmethod
    def from_environment(cls, capture: NativeGameCapture | None, environment: Mapping[str, str]):
        path = environment.get('COMMANDER_GYM_RECORDING_LIFECYCLE_SOCKET')
        if not path:
            return None
        if capture is None:
            raise JournalError('lifecycle recording requires native capture')
        return cls(capture, Path(path))

    def exchange(self, request: dict[str, Any]) -> dict[str, Any]:
        path = self.socket_path.absolute()
        _private_dir(path.parent)
        metadata = path.lstat()
        if not stat.S_ISSOCK(metadata.st_mode) or metadata.st_mode & 0o077 or metadata.st_uid != os.getuid():
            raise JournalError('lifecycle socket must be private and owned by recorder')
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2)
            client.connect(str(path))
            client.sendall(json.dumps(request, separators=(',', ':')).encode() + b'\n')
            data = bytearray()
            while b'\n' not in data:
                block = client.recv(4096 - len(data))
                if not block or len(data) + len(block) >= 4096:
                    raise JournalError('invalid lifecycle response framing')
                data.extend(block)
            response = json.loads(data)
            if not isinstance(response, dict) or response.get('ok') is not True:
                raise JournalError('lifecycle rejected recording report')
            return response

    def build_report(self, status: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
        path = self.capture.root / '.native-health.json'
        if path.is_symlink() or path.stat().st_mode & 0o077:
            raise JournalError('native producer proof must be private')
        proof = json.loads(path.read_bytes())
        required = {'schemaVersion', 'bootId', 'releaseId', 'engineSha', 'gymSha', 'utc',
                    'recoveryComplete', 'recordingHealthy', 'producerCoverageComplete',
                    'registeredSources', 'connectedSources', 'pendingRecordWrites'}
        if not isinstance(proof, dict) or set(proof) != required or proof['schemaVersion'] != 1:
            raise JournalError('invalid native producer proof schema')
        for key in ('bootId', 'releaseId', 'engineSha'):
            if not isinstance(status.get(key), str) or proof[key] != status[key]:
                raise JournalError('native producer proof epoch mismatch')
        if proof['gymSha'] != self.capture.declared_pins['gym']:
            raise JournalError('native recorder pin mismatch')
        age = (time.time() if now is None else now) - dt.datetime.fromisoformat(proof['utc']).timestamp()
        if not 0 <= age <= 5:
            raise JournalError('native producer proof stale')
        for key in ('recoveryComplete', 'recordingHealthy', 'producerCoverageComplete'):
            if type(proof[key]) is not bool:
                raise JournalError('invalid producer health flag')
        for key in ('registeredSources', 'connectedSources', 'pendingRecordWrites'):
            if type(proof[key]) is not int or proof[key] < 0:
                raise JournalError('invalid producer health counter')
        metrics = self.capture.operational_metrics()
        pending = proof['pendingRecordWrites'] + metrics['pendingRecordWrites']
        healthy = proof['recordingHealthy'] and metrics['healthy']
        coverage = proof['producerCoverageComplete'] and proof['registeredSources'] == proof['connectedSources']
        drain = status.get('drainId', '')
        quiet = all(type(status.get(key)) is int and status[key] == 0
                    for key in ('activeGames', 'pendingActivities', 'inFlightAdmissions'))
        return dict(protocol=1, op='recording', bootId=proof['bootId'], releaseId=proof['releaseId'],
                    gymSha=proof['gymSha'], recordingSchemaVersion=1,
                    recoveryComplete=proof['recoveryComplete'], recordingHealthy=healthy,
                    producerCoverageComplete=coverage, pendingRecordWrites=pending,
                    durableBytes=metrics['durableBytes'], drainId=drain,
                    drainComplete=bool(drain and status.get('drainAcknowledged') is True and quiet and
                                       healthy and coverage and proof['recoveryComplete'] and pending == 0))

    def tick(self) -> None:
        try:
            self.capture.scan()
            if self._stop_requested:
                return
            status = self.exchange({'protocol': 1, 'op': 'status'})
            if self._stop_requested:
                return
            report = self.build_report(status)
            if self._stop_requested:
                return
            self.exchange(report)
            self.last_error = None
        except (OSError, ValueError, KeyError, TypeError) as error:
            # Absence of a valid heartbeat expires native admission; never invent zero counters.
            self.last_error = type(error).__name__

    def start(self) -> None:
        if self._thread is not None:
            raise JournalError('recording health reporter already started')
        def loop():
            while not self._stop_requested:
                self.tick()
                self._stop.wait(2)
        self._thread = threading.Thread(target=loop, name='private-recording-health', daemon=True)
        self._thread.start()

    def request_stop(self) -> None:
        """Only latch assignments transitively; safe in a Python signal handler."""
        self._stop_requested = True
        self.capture.request_stop()

    def close(self) -> None:
        self.request_stop()
        self._stop.set()  # Ordinary cleanup wakes the reporting loop.
        if self._thread is not None:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                raise TimeoutError('recording health reporter did not stop within close deadline')
