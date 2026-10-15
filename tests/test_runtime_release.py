"""Release transport rejection and idle update/rollback boundaries; no services."""
from pathlib import Path
import copy
import hashlib
import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from commander_gym.manual_runtime_profile import ManualRuntimeError, canonical, digest
from commander_gym.runtime_release import inspect_bundle, pack_bundle, wait_and_promote, diagnostics
from commander_gym.runtime_install import bootstrap_units, accounts
from test_manual_runtime_profile import fixture_profile, qualification, approval


class RuntimeReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = fixture_profile(self.root)
        self.bundle = self.root/'bundle'
        # Pack real files, then model their production destinations in the manifest.
        receipt = qualification(self.profile)
        pack_bundle(self.profile, receipt, approval(self.profile, receipt), self.bundle)
        self.manifest = json.loads((self.bundle/'release.json').read_bytes())
        profile = self.manifest['profile']; profile['purpose'] = 'native-manual-play'
        for role, artifact in profile['artifacts'].items():
            artifact['root'] = ('/usr/local/libexec/argentum-' + ('native' if role == 'legacyNative' else 'source')
                                if role.startswith('legacy') else '/srv/argentum-luna/artifacts/' + role + '/' + digest(artifact['files']))
            artifact['uid'] = 0
        profile['paths'] = {'recordingRoot': '/var/lib/commander-gym/runs/native',
                            'lifecycleSocket': '/run/argentum-play/lifecycle.sock',
                            'recorderSocket': '/run/argentum-luna-ipc/native.sock',
                            'registryPath': '/etc/argentum-play/recording-dispositions/acknowledged-incomplete.json'}
        profile['recordingAuthority']['registryUid'] = 0
        from commander_gym.manual_runtime_config import service_units, shared_https_nginx
        profile['ingress']['origins'] = ['https://fixture.ts.net']
        contents = service_units(launcher_root=profile['artifacts']['launcher']['root'],
                                 dependency_root=profile['artifacts']['dependencies']['root'],
                                 python_relative='python/bin/python3.12', tailnet_only=True)
        contents['nginx.conf'] = shared_https_nginx(frontend_root=profile['artifacts']['frontend']['root'],
            tailnet_origin='https://fixture.ts.net', lan_address=profile['ingress']['lanAddress'],
            lan_subnet=profile['ingress']['lanSubnet'], tailnet_only=True)
        self.manifest['files'] = {n:r for n,r in self.manifest['files'].items() if not n.startswith('artifacts/config/')}
        hashes = {}
        for name, text in contents.items():
            key = 'artifacts/config/' + name; path = self.bundle/key; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text); sha=hashlib.sha256(text.encode()).hexdigest(); hashes[name]=sha
            self.manifest['files'][key]={'sha256':sha, 'executable':False}
        profile['artifacts']['config']['files']=hashes
        profile['artifacts']['config']['root']='/srv/argentum-luna/artifacts/config/'+digest(hashes)
        self.manifest['qualification'] = qualification(profile)
        self.manifest['approval'] = approval(profile, self.manifest['qualification'])
        self.save()

    def save(self):
        raw = canonical(self.manifest); (self.bundle/'release.json').write_bytes(raw)
        self.review = hashlib.sha256(raw).hexdigest()

    def inspect(self): return inspect_bundle(self.bundle, self.review, now=1000)

    def test_complete_pinned_bundle_is_verified_without_installation(self):
        self.assertEqual(self.inspect(), self.manifest)

    def test_review_digest_cannot_be_replaced_by_mutable_package(self):
        with self.assertRaisesRegex(ManualRuntimeError, 'bundle_review_digest'):
            inspect_bundle(self.bundle, '0'*64, now=1000)

    def test_tampered_artifact_rejected_before_staging(self):
        name = next(iter(self.manifest['files']))
        (self.bundle/name).write_bytes(b'damaged')
        with self.assertRaisesRegex(ManualRuntimeError, 'bundle_file_digest'): self.inspect()

    def test_unqualified_or_unapproved_bundle_rejected(self):
        self.manifest['qualification']['gates']['cleanup'] = False; self.save()
        with self.assertRaisesRegex(ManualRuntimeError, 'qualification_gates'): self.inspect()

    def test_stale_bundle_requires_new_qualification(self):
        with self.assertRaisesRegex(ManualRuntimeError, 'qualification_stale'):
            inspect_bundle(self.bundle, self.review, now=100000)

    def test_path_traversal_and_extra_files_not_installable(self):
        self.manifest['files']['../escape'] = {'sha256': 'a'*64, 'executable': False}; self.save()
        with self.assertRaisesRegex(ManualRuntimeError, 'bundle_inventory'): self.inspect()

    def test_symlink_parent_rejected(self):
        catalog = self.bundle/'artifacts/catalog'; moved = self.root/'moved'
        catalog.rename(moved); catalog.symlink_to(moved, target_is_directory=True)
        with self.assertRaisesRegex(ManualRuntimeError, 'bundle_symlink'): self.inspect()

    def test_bundle_limit_rejects_before_reading_huge_file(self):
        with patch('commander_gym.runtime_release.MAX_BUNDLE_BYTES', 1):
            with self.assertRaisesRegex(ManualRuntimeError, 'bundle_file_digest'): self.inspect()

    def test_update_waits_only_on_busy_and_rechecks_transaction(self):
        with patch('commander_gym.runtime_release.promote', side_effect=[ManualRuntimeError('not_idle'), {'state':'promoted'}]) as promote:
            sleep = Mock()
            result = wait_and_promote(Mock(), {}, {}, {}, wait_seconds=10, clock=lambda:0, sleep=sleep)
            self.assertEqual(result['state'], 'promoted'); self.assertEqual(promote.call_count, 2)
            sleep.assert_called_once_with(5)

    def test_unhealthy_or_failed_update_is_not_retried(self):
        for code in ('recording_health', 'candidate_failed_previous_restored', 'rollback_unconfirmed'):
            with patch('commander_gym.runtime_release.promote', side_effect=ManualRuntimeError(code)) as promote:
                with self.assertRaisesRegex(ManualRuntimeError, code):
                    wait_and_promote(Mock(), {}, {}, {}, wait_seconds=10, clock=lambda:0)
                self.assertEqual(promote.call_count, 1)

    def test_busy_deadline_does_not_drain_or_restart(self):
        with patch('commander_gym.runtime_release.promote', side_effect=ManualRuntimeError('not_idle')) as promote:
            with self.assertRaisesRegex(ManualRuntimeError, 'not_idle'):
                wait_and_promote(Mock(), {}, {}, {}, wait_seconds=0)
            self.assertEqual(promote.call_count, 1)

    def test_diagnostics_do_not_include_provider_or_game_payloads(self):
        host=Mock(); host.current.return_value=Mock(runtime_id='a'*64, engine_sha='b'*40, gym_sha='c'*40, sequence=2, recording_schema=1)
        host.status.return_value={'OPENAI_API_KEY':'private-fixture', 'game':'private-fixture', 'activeGames':1}
        result=diagnostics(host)
        self.assertEqual(result['activeGames'],1); self.assertNotIn('private-fixture', json.dumps(result))
        self.assertFalse(result['ready'])

    def test_bootstrap_uses_pinned_interpreters_and_automatic_admission(self):
        profile=self.manifest['profile']; units=bootstrap_units(profile)
        self.assertIn(profile['artifacts']['dependencies']['root'], units['argentum-play.service'])
        self.assertIn('OnUnitInactiveSec=15', units['argentum-admission.timer'])
        self.assertNotIn('OPENAI_API_KEY', '\n'.join(units.values()))

    def test_account_collision_never_reassigns_uid(self):
        with patch('commander_gym.runtime_install.pwd.getpwnam', return_value=Mock(pw_uid=-1)), patch('commander_gym.runtime_install.command') as command:
            with self.assertRaisesRegex(ManualRuntimeError, 'service_uid_conflict'): accounts(self.profile, create=True)
            command.assert_not_called()
