"""UID-authenticated local callback receipts; only the recorder owns journals.

The native JVM registers its exact masked request on a private socket before HTTP.
The separate sidecar socket cannot register requests, read journals or finalize runs.
No provider, game-launch, lifecycle drain/resume or secret loading lives here.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import ctypes
import errno
import sys
import json
import os
from pathlib import Path
import re
import socket
import stat
import struct
import threading
import time
from types import SimpleNamespace
from typing import Any, Mapping

from .binding_catalog import load_binding_catalog
from .game_journal import JournalError, _json, _safe, inspect_journal
from .game_server_bindings import GameServerBindingRegistry
from .game_server_seat import SeatProvenance
from .game_server_sidecar import GameServerSidecarHandler

PROTOCOL = 1
MAX_BYTES = 4 * 1024 * 1024
TIMEOUT = 2.0
CALLBACK_TTL = 130.0  # native 120s / sidecar 110s; bounded late cancellation receipts
_ID = re.compile(r'[A-Za-z0-9_-]{1,128}')


class RecorderBridgeError(JournalError):
    """Fixed diagnostics only; never include request/model/journal contents."""


def _id(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise RecorderBridgeError('invalid callback identity')
    return value


def peer_uid(connection: socket.socket) -> int:
    if hasattr(socket, 'SO_PEERCRED'):
        return struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[1]
    if hasattr(connection, 'getpeereid'):
        return connection.getpeereid()[0]
    if sys.platform == 'darwin':
        libc = ctypes.CDLL(None, use_errno=True)
        function = libc.getpeereid
        function.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
        function.restype = ctypes.c_int
        uid, gid = ctypes.c_uint(), ctypes.c_uint()
        if function(connection.fileno(), ctypes.byref(uid), ctypes.byref(gid)) == 0:
            return uid.value
    raise RecorderBridgeError('Unix peer credentials unavailable')


def _socket_path(path: Path, owner_uid: int, *, listener=False, native=False):
    path = path.absolute()
    # Every parent must be real; the immediate IPC directory is owner-controlled.
    if any(parent.is_symlink() for parent in (path.parent, *path.parents)):
        raise RecorderBridgeError('invalid recorder socket parents')
    parent = path.parent.stat()
    if parent.st_uid != owner_uid or parent.st_mode & 0o027 or not stat.S_ISDIR(parent.st_mode):
        raise RecorderBridgeError('invalid recorder socket directory custody')
    if listener:
        if path.exists() or path.is_symlink():
            _socket_path(path, owner_uid, native=native)
            before = path.lstat()
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(TIMEOUT)
                try:
                    probe.connect(str(path))
                except OSError as error:
                    if error.errno != errno.ECONNREFUSED:
                        raise RecorderBridgeError('recorder socket cannot be reclaimed') from None
                else:
                    raise RecorderBridgeError('recorder socket already active')
            # Parent is owner-controlled; never unlink a substituted inode or file.
            after = path.lstat()
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                raise RecorderBridgeError('recorder socket changed during recovery')
            path.unlink()
    else:
        meta = path.lstat()
        allowed = 0o077 if native else 0o007
        if meta.st_uid != owner_uid or not stat.S_ISSOCK(meta.st_mode) or meta.st_mode & allowed:
            raise RecorderBridgeError('invalid recorder socket custody')
    return path


def _read(connection, *, deadline=None):
    data = bytearray()
    deadline = time.monotonic() + TIMEOUT if deadline is None else deadline
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RecorderBridgeError('recorder frame deadline exceeded')
        connection.settimeout(remaining)
        block = connection.recv(min(65536, MAX_BYTES + 1 - len(data)))
        if not block:
            raise RecorderBridgeError('invalid recorder framing')
        data.extend(block)
        if len(data) > MAX_BYTES:
            raise RecorderBridgeError('recorder message exceeds limit')
        if b'\n' in block:
            if data[-1:] != b'\n' or data.count(b'\n') != 1:
                raise RecorderBridgeError('invalid recorder framing')
            try:
                value = json.loads(data)
                _safe(value)
                _json(value)  # rejects nonfinite numbers
            except (ValueError, TypeError, RecursionError):
                raise RecorderBridgeError('invalid recorder message') from None
            if not isinstance(value, dict):
                raise RecorderBridgeError('invalid recorder message')
            return value


def exchange(path: Path, request: Mapping[str, Any], *, recorder_uid: int) -> dict[str, Any]:
    path = _socket_path(path, recorder_uid)
    data = _json(request) + b'\n'
    if len(data) > MAX_BYTES:
        raise RecorderBridgeError('recorder message exceeds limit')
    deadline = time.monotonic() + TIMEOUT
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(max(0.001, deadline - time.monotonic()))
        client.connect(str(path))
        if peer_uid(client) != recorder_uid:
            raise RecorderBridgeError('recorder peer mismatch')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RecorderBridgeError('recorder exchange deadline exceeded')
        client.settimeout(remaining)
        client.sendall(data)
        response = _read(client, deadline=deadline)
    if (set(response) != {'protocol', 'ok', 'callbackId', 'receiptSha256'} or
            response['protocol'] != PROTOCOL or response['ok'] is not True or
            response['callbackId'] != request['callbackId'] or
            not isinstance(response['receiptSha256'], str) or
            not re.fullmatch('[a-f0-9]{64}', response['receiptSha256'])):
        raise RecorderBridgeError('recorder did not acknowledge durable event')
    return response


class _RecordingPilot:
    def __init__(self, pilot):
        self.name, self.version = pilot.pilot_id, pilot.revision

    def choose(self, _observation):
        raise RecorderBridgeError('recorder cannot invoke a pilot')


def recording_registry(catalog_path: Path, instance_root: Path) -> GameServerBindingRegistry:
    """Resolve exact catalog identity/deck/lineage without SDK, credentials or calls."""
    loaded = load_binding_catalog(catalog_path, instance_root=instance_root,
                                  pilot_factory=lambda pilot, _binding: _RecordingPilot(pilot))
    return GameServerBindingRegistry(loaded.resolver, loaded.binding_ids)


class _Observed(Exception):
    def __init__(self, event):
        self.event = event


def _expected_event(registry, request, callback):
    def observed(event):
        raise _Observed(event)
    adapter = registry.create_seat(request['playerId'], request['profileId'], decision_start_sink=observed)
    # Reuse the exact transport validation and seat conversion; the start hook stops
    # before choose_for_observation, so this never executes even a synthetic pilot.
    handler = object.__new__(GameServerSidecarHandler)
    handler.server = SimpleNamespace(sidecar_config=SimpleNamespace(require_manual_human_game=True))
    try:
        handler._invoke(callback, adapter, request)
    except _Observed as result:
        return asdict(result.event)
    raise RecorderBridgeError('native callback observation unavailable')


class RecorderBridge:
    """Compose with the *existing* capture/reporter, in their recorder process.

    Two sockets distinguish native registration from untrusted sidecar receipts.
    All acknowledgments are hashes of fsynced journal rows; recovery reads those rows,
    so a crash between append and reply cannot cause a duplicate append.
    """
    def __init__(self, capture, registry, *, native_socket: Path, sidecar_socket: Path,
                 native_uid: int, sidecar_uid: int, sidecar_gid: int | None = None,
                 allow_same_uid_fixture: bool = False):
        if native_uid != os.getuid() or type(sidecar_uid) is not int or sidecar_uid < 0:
            raise RecorderBridgeError('invalid recorder identities')
        if native_uid == sidecar_uid and not allow_same_uid_fixture:
            raise RecorderBridgeError('sidecar and recorder must have distinct identities')
        if native_socket == sidecar_socket:
            raise RecorderBridgeError('recorder roles require separate sockets')
        self.capture, self.registry = capture, registry
        self.native_socket, self.sidecar_socket = Path(native_socket), Path(sidecar_socket)
        self.native_uid, self.sidecar_uid, self.sidecar_gid = native_uid, sidecar_uid, sidecar_gid
        self._listeners = []
        self._threads = []
        self._stop = threading.Event()
        self._connections = set()
        self._connections_lock = threading.Lock()

    @staticmethod
    def _receipt(rows, callback_id, op):
        for row in rows:
            metadata = row['payload'].get('recorder_bridge')
            if isinstance(metadata, dict) and metadata.get('callbackId') == callback_id and metadata.get('op') == op:
                return row, metadata
        return None

    def ingest(self, message, *, native: bool):
        required = {'protocol', 'op', 'callbackId', 'gameId', 'seatId', 'bindingId', 'callback'}
        op = message.get('op')
        required |= {'request'} if op == 'register' else {'event'} if op in {'started', 'finished'} else set()
        if (set(message) != required or type(message.get('protocol')) is not int or message['protocol'] != PROTOCOL or
                op not in ({'register', 'closed'} if native else {'started', 'finished'}) or
                message.get('callback') not in {'chooseAction', 'decideMulligan', 'chooseBottomCards'}):
            raise RecorderBridgeError('invalid recorder operation')
        for key in ('callbackId', 'gameId', 'seatId', 'bindingId'):
            _id(message[key])
        try:
            _safe(message)
        except JournalError:
            raise RecorderBridgeError('unsafe callback event') from None
        digest = hashlib.sha256(_json(message)).hexdigest()
        game_id, seat = message['gameId'], message['seatId']
        # Scanner and ingestion share its lock; finalization cannot race a receipt.
        with self.capture._lock:
            if self.capture.stop_requested():
                raise RecorderBridgeError('recorder stopping')
            self.capture.scan()
            journal = self.capture._journals.get(game_id)
            directory = self.capture.root / game_id
            # Duplicate acknowledgments remain available after finalization/restart.
            if not directory.is_dir() or directory.is_symlink():
                raise RecorderBridgeError('unknown native game')
            report = inspect_journal(directory, cancel=self.capture.stop_requested)
            if set(report['issues']) - {'missing_terminal'}:
                raise RecorderBridgeError('invalid recorder journal')
            rows = report['rows']
            previous = self._receipt(rows, message['callbackId'], op)
            if previous:
                if previous[1]['digest'] != digest:
                    raise RecorderBridgeError('conflicting duplicate recorder event')
                return dict(protocol=PROTOCOL, ok=True, callbackId=message['callbackId'], receiptSha256=previous[0]['sha256'])
            registration = self._receipt(rows, message['callbackId'], 'register')
            if op == 'closed' and journal is None and report['closed'] and registration:
                registered = registration[1]
                if any(message[k] != registered[k] for k in ('gameId', 'seatId', 'bindingId', 'callback')):
                    raise RecorderBridgeError('native callback identity mismatch')
                finished = self._receipt(rows, message['callbackId'], 'finished')
                if finished:
                    # Cancellation may seal between the durable completion and JVM close.
                    # Its existing finished receipt already proves this registration ended.
                    return dict(protocol=PROTOCOL, ok=True, callbackId=message['callbackId'], receiptSha256=finished[0]['sha256'])
            if journal is None or journal.terminal or journal.failed or self.capture.errors:
                raise RecorderBridgeError('native recording unavailable')
            initialization = self.capture._initializations.get(game_id)
            players = initialization['payload']['setup']['players'] if initialization else []
            if seat not in {p.get('playerId') for p in players}:
                raise RecorderBridgeError('unknown native seat')
            metadata = {k: message[k] for k in required - {'request', 'event'}}
            metadata['digest'] = digest
            if op == 'register':
                request = message['request']
                if (not isinstance(request, dict) or request.get('playerId') != seat or
                        request.get('gameSessionId') != game_id or request.get('profileId') != message['bindingId'] or
                        request.get('manualHumanGame') is not True):
                    raise RecorderBridgeError('native callback identity mismatch')
                expected = _expected_event(self.registry, request, message['callback'])
                metadata.update(expected=expected, registeredUnix=time.time())
                payload, kind, seat_id = {'recorder_bridge': metadata}, 'runtime_context', None
            else:
                if registration is None:
                    raise RecorderBridgeError('native callback registration missing')
                registered = registration[1]
                if any(message[k] != registered[k] for k in ('gameId', 'seatId', 'bindingId', 'callback')):
                    raise RecorderBridgeError('native callback identity mismatch')
                if op == 'closed':
                    payload, kind, seat_id = {'recorder_bridge': metadata}, 'runtime_context', None
                else:
                    age = time.time() - registered['registeredUnix']
                    if not 0 <= age <= CALLBACK_TTL:
                        raise RecorderBridgeError('native callback registration expired')
                    expected, event = registered['expected'], message['event']
                    if not isinstance(event, dict) or set(event) != set(expected):
                        raise RecorderBridgeError('invalid callback event schema')
                    if any(event[k] != expected[k] for k in set(expected) - {'choice'}):
                        raise RecorderBridgeError('callback differs from native masked input')
                    if op == 'started' and (event['choice'] != {} or self._receipt(rows, message['callbackId'], 'closed')):
                        raise RecorderBridgeError('native callback no longer pending')
                    start = self._receipt(rows, message['callbackId'], 'started')
                    decision_id = (expected['decision_evidence']['correlationId']
                                   if expected['decision_evidence'] is not None else message['callbackId'])
                    if op == 'finished' and (start is None or (seat, decision_id) not in journal.pending_decisions):
                        raise RecorderBridgeError('completion has no durable pending start')
                    if op == 'started' and any(p[0] == seat for p in journal.pending_decisions):
                        raise RecorderBridgeError('overlapping native seat callback')
                    if not isinstance(event['choice'], dict):
                        raise RecorderBridgeError('invalid callback choice')
                    payload = dict(event, decision_id=decision_id, gym_revision=self.capture.declared_pins['gym'],
                                   recorder_bridge=metadata)
                    kind, seat_id = ('decision_started' if op == 'started' else 'seat_callback'), seat
            source = 'recorder-bridge'
            try:
                receipt = journal.append(kind, payload, seat_id=seat_id,
                                         source=source, source_sequence=journal.sources.get(source, 0))
            except (OSError, ValueError):
                self.capture.errors['recorder_bridge'] = 'DurableWriteFailed'
                raise RecorderBridgeError('recorder durable write failed') from None
            return dict(protocol=PROTOCOL, ok=True, callbackId=message['callbackId'], receiptSha256=receipt)

    def start(self):
        if self._listeners or self._stop.is_set():
            raise RecorderBridgeError('recorder bridge already started or closed')
        self.capture.errors['recorder_bridge_transport'] = 'BridgeNotReady'
        try:
            for native, path in ((True, self.native_socket), (False, self.sidecar_socket)):
                path = _socket_path(path, self.native_uid, listener=True, native=native)
                listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    listener.bind(str(path))
                except BaseException:
                    listener.close()
                    raise
                self._listeners.append((listener, path, path.stat().st_ino))
                os.chmod(path, 0o600 if native else 0o660)
                if not native and self.sidecar_gid is not None:
                    os.chown(path, -1, self.sidecar_gid)
                listener.listen(8)
                listener.settimeout(0.2)
                thread = threading.Thread(target=self._serve, args=(listener, native), daemon=True,
                                          name='private-recorder-native' if native else 'private-recorder-sidecar')
                self._threads.append(thread)
                thread.start()
            self.capture.errors.pop('recorder_bridge_transport', None)
        except BaseException:
            self.close()
            raise

    def _serve(self, listener, native):
        while not self._stop.is_set():
            try:
                connection, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                if not self._stop.is_set():
                    self.capture.errors['recorder_bridge_transport'] = 'ListenerFailed'
                return
            with self._connections_lock:
                if self._stop.is_set():
                    connection.close()
                    return
                self._connections.add(connection)
            try:
                self._serve_connection(connection, native)
            finally:
                with self._connections_lock:
                    self._connections.discard(connection)
                connection.close()

    def _serve_connection(self, connection, native):
        with connection:
            connection.settimeout(TIMEOUT)
            try:
                if peer_uid(connection) != (self.native_uid if native else self.sidecar_uid):
                    raise RecorderBridgeError('recorder peer mismatch')
                response = self.ingest(_read(connection), native=native)
            except (OSError, ValueError, TypeError, KeyError, RecursionError):
                response = dict(protocol=PROTOCOL, ok=False, error='recorder_event_rejected')
            try:
                connection.sendall(_json(response) + b'\n')
            except OSError:
                pass  # durable retry acknowledgment comes from the journal itself

    def close(self):
        self._stop.set()
        if self._listeners:
            self.capture.errors['recorder_bridge_transport'] = 'BridgeStopped'
        with self._connections_lock:
            for connection in self._connections:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                connection.close()
        for listener, path, inode in self._listeners:
            listener.close()
        for thread in self._threads:
            thread.join(timeout=TIMEOUT + 1)
            if thread.is_alive():
                raise TimeoutError('recorder bridge did not stop')
        for _, path, inode in self._listeners:
            if path.exists() and not path.is_symlink() and path.stat().st_ino == inode:
                path.unlink()
        self._listeners.clear()


class RemoteCaptureSink:
    """Sidecar-only sink: no journal/root/pin/health access and no finalizer thread."""
    def __init__(self, socket_path: Path, *, recorder_uid: int):
        self.socket_path, self.recorder_uid = Path(socket_path), recorder_uid
        self._context = threading.local()

    @contextmanager
    def callback_context(self, request, callback, callback_id):
        _id(callback_id)
        identity = dict(protocol=PROTOCOL, callbackId=callback_id, gameId=_id(request.get('gameSessionId')),
                        seatId=_id(request.get('playerId')), bindingId=_id(request.get('profileId')), callback=callback)
        if getattr(self._context, 'identity', None) is not None:
            raise RecorderBridgeError('overlapping recording context')
        self._context.identity = identity
        try:
            yield
        finally:
            self._context.identity = None

    def seat_sink(self, game_id, seat_id):
        remote = self
        class Sink:
            def started(self, event): self._send('started', event)
            def finished(self, event): self._send('finished', event)
            def _send(self, op, event):
                identity = getattr(remote._context, 'identity', None)
                if identity is None or identity['gameId'] != game_id or identity['seatId'] != seat_id:
                    raise RecorderBridgeError('native recording context missing')
                message = dict(identity, op=op, event=asdict(event))
                # One retry handles a lost reply after fsync; exact bytes/id remain stable.
                for attempt in range(2):
                    try:
                        exchange(remote.socket_path, message, recorder_uid=remote.recorder_uid)
                        return
                    except (OSError, RecorderBridgeError):
                        if attempt: raise RecorderBridgeError('recorder receipt unavailable') from None
        return Sink()
