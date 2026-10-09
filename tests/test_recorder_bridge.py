"""Synthetic callbacks only: no native game launch, SDK, credentials or provider."""
import copy
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, MagicMock
import urllib.request

from commander_gym.game_journal import PIN_KEYS, ZERO, inspect_journal, seat_projection, verify_finalized_manifest
from commander_gym.game_server_binding_openai_sidecar import (
    BindingOpenAIGameServerConfig, build_binding_openai_game_server_sidecar,
)
from commander_gym.game_server_openai_sidecar import OpenAIGameServerSidecarConfig
from commander_gym.native_game_capture import NativeGameCapture
from commander_gym.recorder_bridge import (
    RecorderBridge, RecorderBridgeError, RemoteCaptureSink, exchange, recording_registry, _expected_event,
)
from test_game_server_binding_openai_sidecar import synthetic_catalog, FakeClient, free_port


class RecorderBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.base.chmod(0o700)
        self.root = self.base / 'recordings'; self.root.mkdir(mode=0o700)
        self.game = self.root / 'synthetic-game'; self.game.mkdir(mode=0o700)
        self.source = self.game / 'native-000000.ndjson'; self.source.touch(mode=0o600)
        self.catalog = synthetic_catalog(self.base)
        self.registry = recording_registry(self.catalog, self.base)
        self.pins = {k: None for k in PIN_KEYS}; self.pins['gym'] = 'b' * 40
        self.capture = NativeGameCapture(self.root, self.pins)
        self.previous, self.sequence = ZERO, 0
        self.add('state_checkpoint', {'hiddenOpponentHand': ['DO-NOT-SEND-ADMIN-SECRET']})
        self.add('initialization', {'setup': {'seed': 42, 'players': [{'playerId': 'seat-a', 'deck': ['Forest']}]},
                                    'pinnedCards': ['Forest']})
        self.ipc = self.base / 'ipc'; self.ipc.mkdir(mode=0o750)
        self.bridge = RecorderBridge(self.capture, self.registry, native_socket=self.ipc / 'native.sock',
                                     sidecar_socket=self.ipc / 'sidecar.sock', native_uid=os.getuid(), sidecar_uid=os.getuid(), allow_same_uid_fixture=True)
        self.identity = dict(protocol=1, callbackId='callback-1', gameId='synthetic-game', seatId='seat-a',
                             bindingId='seat-a', callback='chooseAction')
        self.request = dict(playerId='seat-a', profileId='seat-a', gameSessionId='synthetic-game', manualHumanGame=True,
                            state={'viewingPlayerId': 'seat-a'}, pendingDecision=None, recentGameLog=[],
                            legalActions=[{'action': {'type': 'PassPriority', 'playerId': 'seat-a'}, 'actionType': 'PassPriority'}])

    def tearDown(self):
        self.bridge.close(); self.capture.close(); self.temp.cleanup()

    def add(self, kind, payload):
        self.sequence += 1
        body = dict(schemaVersion=1, gameId='synthetic-game', sequence=self.sequence, previousSha256=self.previous,
                    engineRevision='a' * 40, clockEpoch='synthetic-clock', kind=kind, visibility='admin', payload=payload)
        exact = json.dumps(body, separators=(',', ':'))
        self.previous = hashlib.sha256(exact.encode()).hexdigest()
        with self.source.open('a') as stream:
            stream.write(json.dumps({'body': exact, 'sha256': self.previous}) + '\n')

    def register(self):
        return self.bridge.ingest(dict(self.identity, op='register', request=self.request), native=True)

    def event(self, op='started'):
        event = _expected_event(self.registry, self.request, self.identity['callback'])
        if op == 'finished': event['choice'] = {'channel': 'action', 'actionId': 0, 'metadata': {'input_tokens': 17, 'cost_usd': 0}}
        return dict(self.identity, op=op, event=event)

    def test_fsync_ack_idempotent_conflicting_duplicates_and_restart(self):
        self.register()
        event = self.event()
        first = self.bridge.ingest(event, native=False)
        before = inspect_journal(self.game)['root_sha256']
        self.assertEqual(self.bridge.ingest(event, native=False), first)
        self.assertEqual(inspect_journal(self.game)['root_sha256'], before)
        conflicting = copy.deepcopy(event); conflicting['event']['choice'] = {'x': 'bad'}
        with self.assertRaises(RecorderBridgeError): self.bridge.ingest(conflicting, native=False)
        self.capture.close()
        self.capture = NativeGameCapture(self.root, self.pins); self.bridge.capture = self.capture
        self.assertEqual(self.bridge.ingest(event, native=False), first)
        self.bridge.ingest(self.event('finished'), native=False)
        self.assertEqual(len([r for r in inspect_journal(self.game)['rows'] if r['kind'] == 'seat_callback']), 1)

    def test_lost_reply_retries_same_durable_receipt(self):
        self.register(); self.bridge.start()
        remote = RemoteCaptureSink(self.bridge.sidecar_socket, recorder_uid=os.getuid())
        event = self.event()['event']
        from commander_gym.game_server_seat import SeatProvenance
        actual_exchange = exchange
        calls = []
        def lose_first_reply(*args, **kwargs):
            reply = actual_exchange(*args, **kwargs); calls.append(reply)
            if len(calls) == 1: raise OSError('synthetic lost reply')
            return reply
        with patch('commander_gym.recorder_bridge.exchange', side_effect=lose_first_reply):
            with remote.callback_context(self.request, 'chooseAction', 'callback-1'):
                remote.seat_sink('synthetic-game', 'seat-a').started(SeatProvenance(**event))
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(len([r for r in inspect_journal(self.game)['rows'] if r['kind'] == 'decision_started']), 1)

    def test_native_registration_required_and_wrong_role_cannot_register(self):
        with self.assertRaises(RecorderBridgeError): self.bridge.ingest(self.event(), native=False)
        with self.assertRaises(RecorderBridgeError):
            self.bridge.ingest(dict(self.identity, op='register', request=self.request), native=False)
        self.register()
        for key in ('gameId', 'seatId', 'bindingId', 'callback'):
            message = self.event(); message[key] = 'spoofed'
            with self.subTest(key=key), self.assertRaises((RecorderBridgeError, OSError)):
                self.bridge.ingest(message, native=False)
        bad = self.event(); bad['event']['observation']['state']['hiddenOpponentHand'] = ['injected']
        with self.assertRaises(RecorderBridgeError): self.bridge.ingest(bad, native=False)
        bad = self.event(); bad['event']['lineage']['binding']['revision'] = 'spoofed'
        with self.assertRaises(RecorderBridgeError): self.bridge.ingest(bad, native=False)

    def test_closed_and_expired_start_denied_late_finish_captured(self):
        self.register()
        self.bridge.ingest(self.event(), native=False)
        self.bridge.ingest(dict(self.identity, op='closed'), native=True)
        self.add('terminal', {'winnerId': None, 'nativeGameOver': True, 'administrativeStall': None})
        self.bridge.ingest(self.event('finished'), native=False)
        self.capture.scan()
        manifest = verify_finalized_manifest(self.game)
        self.assertNotIn('pending_decisions', manifest['gaps'])
        self.assertFalse(manifest['recording_complete'])
        projected = seat_projection(inspect_journal(self.game), 'seat-a')
        self.assertNotIn('DO-NOT-SEND-ADMIN-SECRET', json.dumps(projected))
        self.assertIn('input_tokens', json.dumps(projected))
        self.assertTrue(self.bridge.ingest(dict(self.identity, op='closed'), native=True)['ok'])

    def test_client_deadline_covers_connect_send_and_read_together(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        with patch('commander_gym.recorder_bridge._socket_path', return_value=self.bridge.sidecar_socket), \
             patch('commander_gym.recorder_bridge.peer_uid', return_value=os.getuid()), \
             patch('commander_gym.recorder_bridge.socket.socket', return_value=connection), \
             patch('commander_gym.recorder_bridge.TIMEOUT', 1), \
             patch('commander_gym.recorder_bridge.time.monotonic', side_effect=[100, 100, 100.7, 101.1]):
            with self.assertRaises(RecorderBridgeError):
                exchange(self.bridge.sidecar_socket, {'callbackId': 'synthetic'}, recorder_uid=os.getuid())
        connection.sendall.assert_called_once()
        connection.recv.assert_not_called()

    def test_native_close_after_terminal_sealing_returns_existing_finish_receipt(self):
        self.register(); self.bridge.ingest(self.event(), native=False)
        self.add('terminal', {'winnerId': None, 'nativeGameOver': True, 'administrativeStall': None})
        receipt = self.bridge.ingest(self.event('finished'), native=False)
        closed = dict(self.identity, op='closed')
        self.assertEqual(self.bridge.ingest(closed, native=True), receipt)
        before = inspect_journal(self.game)['root_sha256']
        self.assertEqual(self.bridge.ingest(closed, native=True), receipt)
        self.assertEqual(inspect_journal(self.game)['root_sha256'], before)

    def test_expiration_completion_without_start_and_closed_start(self):
        with patch('commander_gym.recorder_bridge.time.time', return_value=100): self.register()
        with patch('commander_gym.recorder_bridge.time.time', return_value=231):
            with self.assertRaises(RecorderBridgeError): self.bridge.ingest(self.event(), native=False)
        with patch('commander_gym.recorder_bridge.time.time', return_value=101):
            with self.assertRaises(RecorderBridgeError): self.bridge.ingest(self.event('finished'), native=False)
            self.bridge.ingest(dict(self.identity, op='closed'), native=True)
            with self.assertRaises(RecorderBridgeError): self.bridge.ingest(self.event(), native=False)

    def test_durable_failure_latches_unhealthy_and_never_acknowledges(self):
        self.register()
        journal = self.capture._journals['synthetic-game']
        with patch.object(journal, 'append', side_effect=OSError('private error')):
            with self.assertRaises(RecorderBridgeError): self.bridge.ingest(self.event(), native=False)
        self.assertFalse(self.capture.operational_metrics()['healthy'])

    def test_peer_auth_socket_modes_and_no_raw_data_response(self):
        self.bridge.start()
        self.assertEqual(self.bridge.native_socket.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.bridge.sidecar_socket.stat().st_mode & 0o777, 0o660)
        request = dict(self.identity, op='register', request=self.request)
        reply = exchange(self.bridge.native_socket, request, recorder_uid=os.getuid())
        self.assertEqual(set(reply), {'protocol', 'ok', 'callbackId', 'receiptSha256'})
        self.assertNotIn('DO-NOT-SEND-ADMIN-SECRET', json.dumps(reply))
        self.bridge.sidecar_uid = os.getuid() + 1
        with self.assertRaises((RecorderBridgeError, OSError)):
            exchange(self.bridge.sidecar_socket, self.event(), recorder_uid=os.getuid())
        self.bridge.sidecar_uid = os.getuid()
        self.bridge.sidecar_socket.chmod(0o666)
        with self.assertRaises(RecorderBridgeError): exchange(self.bridge.sidecar_socket, self.event(), recorder_uid=os.getuid())

    def test_oversize_secret_unknown_fields_and_symlinks_rejected(self):
        self.register(); self.bridge.start()
        bad = self.event(); bad['extra'] = 'x'
        with self.assertRaises(RecorderBridgeError): self.bridge.ingest(bad, native=False)
        bad = self.event(); bad['event']['choice'] = {'api_key': 'private'}
        with self.assertRaises(RecorderBridgeError): self.bridge.ingest(bad, native=False)
        with self.assertRaises(RecorderBridgeError):
            exchange(self.bridge.sidecar_socket, dict(self.event(), extra='x' * (4 * 1024 * 1024)), recorder_uid=os.getuid())
        linked = self.base / 'linked'; linked.symlink_to(self.ipc, target_is_directory=True)
        with self.assertRaises(RecorderBridgeError): exchange(linked / 'sidecar.sock', self.event(), recorder_uid=os.getuid())

    def test_all_callbacks_preserve_binding_lineage_and_native_masked_input(self):
        for callback, values in (
            ('decideMulligan', {'mulligan': {'hand': ['card-a'], 'cards': {'card-a': {'name': 'Forest'}}, 'mulliganCount': 0}}),
            ('chooseBottomCards', {'bottomCards': {'hand': ['card-a'], 'cards': {'card-a': {'name': 'Forest'}}, 'cardsToPutOnBottom': 1}}),
            ('chooseAction', {'state': {'viewingPlayerId': 'seat-a'}, 'legalActions': [], 'pendingDecision': {
                'type': 'ChooseModeDecision', 'id': 'decision-1', 'responseSpec': {'responseType': 'ModeChosenResponse'}}}),
        ):
            self.identity.update(callback=callback, callbackId='callback-' + callback)
            self.request = dict(playerId='seat-a', profileId='seat-a', gameSessionId='synthetic-game', manualHumanGame=True, **values)
            self.register()
            self.bridge.ingest(self.event(), native=False)
            self.bridge.ingest(self.event('finished'), native=False)
            self.bridge.ingest(dict(self.identity, op='closed'), native=True)

    def test_native_evidence_provider_error_keeps_correlation_and_model_io(self):
        from commander_gym.captured_choices import SCHEMA_HASH
        from commander_gym.openai_responses_pilot import OpenAIResponsesPilotError
        observation = self.event()['event']['observation'].copy()
        observation.pop('knownDeck')
        exact = json.dumps(observation, separators=(',', ':'))
        evidence = dict(version=1, correlationId='native-correlation', schemaHash=SCHEMA_HASH,
                        stateDigest=hashlib.sha256(exact.encode()).hexdigest(), observationBody=exact)
        self.request['decisionEvidence'] = evidence
        self.register(); self.bridge.start()
        remote = RemoteCaptureSink(self.bridge.sidecar_socket, recorder_uid=os.getuid())
        sink = remote.seat_sink('synthetic-game', 'seat-a')
        adapter = self.registry.create_seat('seat-a', 'seat-a', decision_start_sink=sink.started, provenance_sink=sink.finished)
        class FailingPilot:
            def choose(self, _observation):
                raise OpenAIResponsesPilotError('synthetic failure', model_io={'attempts': [{'response': {
                    'validationError': 'synthetic', 'usage': {'input_tokens': 17}}}]})
        adapter._pilot = FailingPilot()
        with remote.callback_context(self.request, 'chooseAction', 'callback-1'):
            with self.assertRaises(OpenAIResponsesPilotError):
                adapter.choose_action(self.request['state'], self.request['legalActions'], None, decision_evidence=evidence)
        row = next(r for r in inspect_journal(self.game)['rows'] if r['kind'] == 'seat_callback')
        self.assertEqual(row['payload']['decision_id'], 'native-correlation')
        self.assertEqual(row['payload']['decision_evidence'], evidence)
        self.assertIn('input_tokens', json.dumps(row['payload']['choice']))
        self.assertFalse(self.capture._journals['synthetic-game'].pending_decisions)

    def test_bridge_restart_reclaims_only_stale_owned_sockets_and_keeps_receipts(self):
        self.register(); message = self.event(); first = self.bridge.ingest(message, native=False)
        for path in (self.bridge.native_socket, self.bridge.sidecar_socket):
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stale:
                stale.bind(str(path)); path.chmod(0o600)
        self.bridge.start()
        self.assertEqual(exchange(self.bridge.sidecar_socket, message, recorder_uid=os.getuid()), first)
        duplicate = RecorderBridge(self.capture, self.registry, native_socket=self.bridge.native_socket,
            sidecar_socket=self.bridge.sidecar_socket, native_uid=os.getuid(), sidecar_uid=os.getuid(), allow_same_uid_fixture=True)
        with self.assertRaises(RecorderBridgeError): duplicate.start()
        self.assertTrue(self.bridge.native_socket.exists())
        self.assertTrue(self.bridge.sidecar_socket.exists())

    def test_partial_frame_shutdown_and_slow_drip_have_absolute_deadlines(self):
        with patch('commander_gym.recorder_bridge.TIMEOUT', 0.15):
            self.bridge.start()
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.connect(str(self.bridge.sidecar_socket))
                started = time.monotonic()
                for _ in range(12):
                    try: client.sendall(b'{')
                    except OSError: break
                    time.sleep(0.03)
                self.assertLess(time.monotonic() - started, 0.5)
                response = client.recv(4096)
                self.assertIn(b'recorder_event_rejected', response)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.connect(str(self.bridge.sidecar_socket)); client.sendall(b'{')
                time.sleep(0.03)
                started = time.monotonic(); self.bridge.close()
                self.assertLess(time.monotonic() - started, 0.5)
                self.assertTrue(all(not t.is_alive() for t in self.bridge._threads))
                self.assertFalse(self.capture.operational_metrics()['healthy'])

    def test_binding_factory_remote_http_fixture_no_capture_or_provider_call(self):
        config = BindingOpenAIGameServerConfig(
            sidecar=OpenAIGameServerSidecarConfig(api_key='literal-fake-key', token='literal-fake-bearer', port=free_port()),
            catalog_path=self.catalog, instance_root=self.base, manual_uncapped=True)
        self.bridge.start(); self.register()
        remote = RemoteCaptureSink(self.bridge.sidecar_socket, recorder_uid=os.getuid())
        server = build_binding_openai_game_server_sidecar(config, client=FakeClient(), game_capture=remote)
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        try:
            body = json.dumps(self.request).encode()
            headers = {'Authorization': 'Bearer literal-fake-bearer', 'Content-Type': 'application/json',
                       'X-Commander-Gym-Callback-Id': 'callback-1'}
            request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/v1/choose-action', body, headers)
            with urllib.request.urlopen(request, timeout=5) as response:
                self.assertEqual(json.load(response)['actionId'], 0)
            callbacks = [r for r in inspect_journal(self.game)['rows'] if r['kind'] == 'seat_callback']
            self.assertEqual(len(callbacks), 1)
            self.assertFalse(hasattr(remote, 'root'))
            self.assertFalse(hasattr(remote, 'start'))
        finally:
            server.shutdown(); server.server_close(); worker.join(timeout=2)
