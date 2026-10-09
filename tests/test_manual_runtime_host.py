"""Production host paths under fake files/subprocesses; no service/key IO."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import hashlib
import os
import unittest
from unittest.mock import Mock, patch

from commander_gym import manual_runtime_host as host_module
from commander_gym.manual_runtime_host import ProtectedRuntimeHost
from commander_gym.manual_runtime_profile import ManualRuntimeError, ValidatedRuntime, canonical, digest
from commander_gym.manual_runtime_quiescence import capture_inventory, stop_context, matches_stop_proof
from commander_gym.manual_runtime_rollout import RuntimeSnapshot
from test_manual_runtime_profile import fixture_profile


class ManualRuntimeHostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(prefix='.manual-host-qa-', dir=Path.cwd())
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.profile = fixture_profile(self.root)
        self.host = ProtectedRuntimeHost.__new__(ProtectedRuntimeHost)
        self.host.locked = True
        self.host.policy = {'minFreeBytes': 32*1024**3}
        self.host.anchor = self.profile
        self.host.pending = None
        self.host.base = Mock()
        self.host.native = Mock()
        self.snapshot = RuntimeSnapshot('0'*64, 1, 'keyless', 'c'*40, 'd'*40, 1)
        self.state = {'releaseId': self.snapshot.runtime_id, 'engineSha': self.snapshot.engine_sha,
                      'gymSha': self.snapshot.gym_sha, 'recordingSchemaVersion': 1, 'bootId': 'qa-boot',
                      'observedUnix': 1000, 'recordingHealthy': True, 'recoveryComplete': True,
                      'activeGames': 0, 'pendingActivities': 0, 'pendingRecordWrites': 0, 'inFlightAdmissions': 0,
                      'acceptingNewGames': False, 'drainAcknowledged': True, 'recordingDrainComplete': True}

    def test_previous_verification_never_calls_mutating_storage_helper(self):
        self.host.native.current_release.return_value = (Path('/qa/release'), {'releaseId': self.snapshot.runtime_id})
        with patch.object(host_module.os, 'statvfs', return_value=SimpleNamespace(f_bavail=40*1024**3, f_frsize=1)):
            self.host.verify_snapshot(self.snapshot)
        self.host.base.stage_and_verify.assert_not_called()
        self.host.base.drain.assert_not_called()
        self.host.base.smoke_closed.assert_called_once()

    def test_low_storage_fails_without_drain(self):
        self.host.native.current_release.return_value = (Path('/qa/release'), {'releaseId': self.snapshot.runtime_id})
        with patch.object(host_module.os, 'statvfs', return_value=SimpleNamespace(f_bavail=0, f_frsize=1)):
            with self.assertRaisesRegex(ManualRuntimeError, 'storage_reserve'): self.host.verify_snapshot(self.snapshot)
        self.host.base.stage_and_verify.assert_not_called(); self.host.base.drain.assert_not_called()

    def test_legacy_drain_returns_fresh_barrier_not_initial_reply(self):
        self.host.base.drain.return_value = {'recordingDrainComplete': False}
        self.host.base.status.return_value = self.state
        self.assertEqual(self.host.drain('qa-boot'), self.state)

    def test_stop_proof_rejects_previous_boot_runtime_nonce_and_boolean_counts(self):
        context = stop_context(self.snapshot, self.state, {'original.jsonl': 'a'*64}, nonce='1'*32, now=1000)
        proof = {**context, 'reconciled': True, 'pendingRecordWrites': 0, 'verifiedUnix': 1000}
        self.assertTrue(matches_stop_proof(proof, context, now=1000))
        for key, value in (('bootId', 'old-boot'), ('runtimeId', '9'*64), ('stopNonce', '2'*32), ('sequence', 99), ('pendingRecordWrites', False), ('verifiedUnix', 989)):
            with self.subTest(key=key): self.assertFalse(matches_stop_proof({**proof, key: value}, context, now=1000))

    def test_capture_inventory_keeps_originals_and_rejects_changed_bytes(self):
        root = self.root / 'qa' / 'captures'; root.mkdir(parents=True)
        game = root/'fixture'; game.mkdir()
        original = game/'000000.jsonl'; original.write_bytes(b'preserved original')
        health = root/'.native-health.json'; health.write_bytes(b'heartbeat-one')
        before = capture_inventory(root, uid=os.getuid())
        health.write_bytes(b'heartbeat-two')
        self.assertEqual(capture_inventory(root, uid=os.getuid()), before)
        original.write_bytes(b'changed')
        self.assertNotEqual(capture_inventory(root, uid=os.getuid()), before)
        link=game/'link'; link.symlink_to(original)
        with self.assertRaises(ManualRuntimeError): capture_inventory(root, uid=os.getuid())

    def test_rollback_rejects_foreign_target_and_changed_bank(self):
        target=self.root/'unit.conf'; target.write_bytes(b'foreign')
        bank=self.root/'bank'; bank.mkdir()
        old=b'original'; (bank/'0.original').write_bytes(old)
        entries={str(target): {'original':'0.original','sha256':hashlib.sha256(old).hexdigest(),'mode':0o600}}
        runtime=ValidatedRuntime(canonical(self.profile),digest(self.profile),2)
        self.host.pending={'candidate':runtime,'bank':bank}
        def fake_root_json(path): return entries if path.name=='bank.json' else {'bankSha256':digest(entries)}
        with patch.object(host_module,'TARGETS',{'fixture':target}), patch.object(host_module,'root_json',side_effect=fake_root_json), patch.object(host_module,'protected_bytes',side_effect=lambda path,**kw:path.read_bytes()):
            with self.assertRaisesRegex(ManualRuntimeError,'foreign_runtime_config'): self.host._restore_files()
        target.write_bytes(old)
        with patch.object(host_module,'root_json',side_effect=lambda path: entries if path.name=='bank.json' else {'bankSha256':'0'*64}):
            with self.assertRaisesRegex(ManualRuntimeError,'rollback_bank_changed'): self.host._restore_files()

    def test_selector_preserves_consumed_failed_attempt_sequence(self):
        runtime=ValidatedRuntime(canonical(self.profile),digest(self.profile),2)
        self.host.pending={'candidate':runtime,'previous':self.snapshot}
        self.host._restore_files=Mock(); self.host._reload=Mock()
        with patch.object(host_module,'atomic_root') as write:
            self.host.select(self.snapshot)
        from commander_gym.manual_runtime_profile import decode
        selector=decode(write.call_args.args[1])
        self.assertEqual(selector['sequence'],2)
        self.assertEqual(selector['runtimeId'],self.snapshot.runtime_id)

    def test_selected_profile_and_config_are_checked_before_root_attestation(self):
        target=self.root/'profile.json'
        target.write_bytes(b'qa wrong selected context')
        with patch.object(host_module,'TARGETS',{'profile.json':target}), patch.object(host_module,'protected_bytes',side_effect=lambda path,**kw:path.read_bytes()):
            with self.assertRaisesRegex(ManualRuntimeError,'selected_config_changed'):
                self.host._verify_selected_files(self.profile)
        target.write_bytes(canonical(self.profile))
        with patch.object(host_module,'TARGETS',{'profile.json':target}), patch.object(host_module,'protected_bytes',side_effect=lambda path,**kw:path.read_bytes()):
            self.host._verify_selected_files(self.profile)

    def test_failed_candidate_without_lifecycle_is_held_before_stopping_anything(self):
        self.host.pending={'candidate':object(), 'previous':self.snapshot}
        self.host.current=Mock(return_value=self.snapshot)
        self.host.status=Mock(side_effect=OSError('qa missing lifecycle socket'))
        self.host._service=Mock()
        with self.assertRaises(OSError): self.host.stop_closure()
        self.host._service.assert_not_called()

    def test_config_selection_failure_has_no_selector_publication(self):
        runtime=ValidatedRuntime(canonical(self.profile),digest(self.profile),2)
        self.host.pending={'candidate':runtime,'previous':self.snapshot}
        self.host._select_files=Mock(side_effect=OSError('qa write failure')); self.host._reload=Mock()
        with patch.object(host_module,'atomic_root') as write:
            with self.assertRaises(OSError):self.host.select(runtime)
        write.assert_not_called(); self.host._reload.assert_not_called()

    def test_tailnet_only_selects_and_attests_without_certificate_but_banks_all_targets(self):
        self.profile['ingress']['origins'] = ['https://tolaria.taila3c720.ts.net']
        targets = {'profile.json':self.root/'profile.json', 'lan-server.crt':self.root/'lan-server.crt',
                   'nginx.conf':self.root/'nginx.conf'}
        self.profile['artifacts']['config']['files'] = {'nginx.conf':hashlib.sha256(b'tailnet-qa').hexdigest()}
        source=Path(self.profile['artifacts']['config']['root']); (source/'nginx.conf').write_bytes(b'tailnet-qa')
        runtime=ValidatedRuntime(canonical(self.profile),digest(self.profile),2)
        with patch.object(host_module,'TARGETS',targets), patch.object(host_module,'protected_bytes',side_effect=lambda path,**kw:path.read_bytes()), patch.object(host_module,'atomic_root',side_effect=lambda path,raw,**kw:path.write_bytes(raw)):
            self.host._select_files(runtime)
            self.host._verify_selected_files(self.profile)
            self.assertFalse(targets['lan-server.crt'].exists())
            self.assertIn('lan-server.crt',host_module.TARGETS)
        self.profile['ingress']['origins'].append('https://192.168.0.178:8443')
        runtime=ValidatedRuntime(canonical(self.profile),digest(self.profile),2)
        with patch.object(host_module,'TARGETS',targets):
            with self.assertRaisesRegex(ManualRuntimeError,'service_config_incomplete'):
                self.host._select_files(runtime)
