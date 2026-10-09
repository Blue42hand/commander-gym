"""Sealed identity and precredential rejection without production secrets."""
import copy
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from commander_gym.manual_runtime_profile import (
    GATES, ROLES, ManualRuntimeError, ValidatedRuntime, canonical, decode, digest,
    precredential_validate, recording_pins, validate_approval, validate_profile,
    validate_qualification, verify_artifacts,
)
from commander_gym.manual_runtime_launch import (
    native_spec, parse_existing_key, sidecar_environment, sidecar_spec,
)


def fixture_profile(root):
    artifacts = {}
    for role in ROLES:
        directory = root / 'artifacts' / role
        directory.mkdir(parents=True)
        file = directory / ('game-server.jar' if role == 'server' else 'fixture')
        file.write_bytes(b'qa fixture never production')
        artifacts[role] = {'root': str(directory), 'uid': os.getuid(), 'files': {file.name: hashlib.sha256(file.read_bytes()).hexdigest()}}
    dependency = Path(artifacts['dependencies']['root'])
    (dependency / 'python').write_bytes(b'qa-python')
    (dependency / 'java').write_bytes(b'qa-java')
    (dependency / 'site-packages').mkdir()
    (dependency / 'site-packages/qa.py').write_bytes(b'# qa')
    lock = {'schemaVersion': 1, 'python': 'python', 'java': 'java', 'pythonPath': ['site-packages'], 'packages': {'openai': {'version': 'qa-fixture', 'wheelSha256': 'a' * 64}}}
    (dependency / 'runtime-lock.json').write_bytes(canonical(lock))
    artifacts['dependencies']['files'] = {str(p.relative_to(dependency)): hashlib.sha256(p.read_bytes()).hexdigest() for p in dependency.rglob('*') if p.is_file()}
    launcher = Path(artifacts['launcher']['root'])
    (launcher / 'sidecar_entry.py').write_text('# qa entry')
    artifacts['launcher']['files']['sidecar_entry.py'] = hashlib.sha256((launcher / 'sidecar_entry.py').read_bytes()).hexdigest()
    catalog = Path(artifacts['catalog']['root'])
    (catalog / 'roster.json').write_text('{}')
    (catalog / 'session-manifest.json').write_text('{}')
    artifacts['catalog']['files'].update({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in catalog.glob('*.json')})
    qa = root / 'qa'
    qa.mkdir()
    (qa / 'acknowledged.json').write_bytes(b'qa original registry')
    return {'schemaVersion': 1, 'profile': 'manual-luna-v1', 'purpose': 'qualification', 'engineSha': 'a' * 40, 'gymSha': 'b' * 40, 'recordingSchemaVersion': 1,
            'settings': {'defaultController': 'engine', 'manualOnly': True, 'manualUncapped': True, 'model': 'gpt-6-luna', 'requestTimeoutSeconds': 90, 'maxAttempts': 2, 'callbackTimeoutSeconds': 110, 'nativeTimeoutMs': 120000, 'paidProvidersEnabled': True, 'accountsEnabled': False, 'redisEnabled': False},
            'artifacts': artifacts, 'catalog': {'roster': 'roster.json', 'manifest': 'session-manifest.json', 'bindings': {'talrand': '1'*64, 'sythis': '2'*64, 'lathril': '3'*64}, 'pilotFingerprint': '4'*64, 'componentDigest': 'sha256:'+'5'*64},
            'ingress': {'origins': ['https://192.168.0.178:8443', 'https://tolaria.example.test'], 'backend': '127.0.0.1:18080', 'sidecar': '127.0.0.1:8083', 'lanAddress': '192.168.0.178', 'lanSubnet': '192.168.0.0/24', 'hostLocalTrusted': True},
            'identities': {'nativeUid': 10001, 'sidecarUid': os.getuid() or 10002, 'proxyUid': 10003},
            'paths': {'recordingRoot': str(qa/'runs'), 'lifecycleSocket': str(qa/'lifecycle.sock'), 'recorderSocket': str(qa/'provenance.sock'), 'registryPath': str(qa/'acknowledged.json')},
            'recordingAuthority': {'registryUid': os.getuid(), 'registrySha256': hashlib.sha256((qa/'acknowledged.json').read_bytes()).hexdigest()},
            'credentialPolicy': {'providerReaders': ['sidecar'], 'tokenReaders': ['native', 'sidecar'], 'providerSource': 'existing-private-env', 'delivery': 'systemd-credentials', 'valueInManifest': False}}


def qualification(profile, now=1000):
    return {'schemaVersion': 1, 'profileSha256': digest(profile), 'gates': {gate: True for gate in GATES},
            'sourceCi': {role: {'sha': profile[role+'Sha'], 'checks': [{'name': name, 'conclusion': 'success', 'runId': 1} for name in ({'engine':['coverage'], 'gym':['python','jvm-adapter','manual-runtime-sdk']}[role])]} for role in ('engine', 'gym')},
            'providerMode': 'fake', 'externalProviderCalls': 0, 'observedUnix': now}


def approval(profile, receipt, previous='0'*64, sequence=2):
    return {'schemaVersion': 1, 'approved': True, 'scope': 'activate-manual-runtime', 'profileSha256': digest(profile), 'qualificationSha256': digest(receipt), 'previousRuntimeId': previous, 'sequence': sequence}


class ManualRuntimeProfileTests(unittest.TestCase):
    def setUp(self):
        # QA stays below the working checkout, not world-writable/tmp ancestry.
        self.tmp = TemporaryDirectory(prefix='.manual-runtime-qa-', dir=Path.cwd())
        self.addCleanup(self.tmp.cleanup)
        self.profile = fixture_profile(Path(self.tmp.name))

    def test_exact_qa_inventory_is_verified_but_never_activates(self):
        verify_artifacts(self.profile)
        receipt = qualification(self.profile)
        with self.assertRaisesRegex(ManualRuntimeError, 'qa_never_activates'):
            precredential_validate(self.profile, receipt, approval(self.profile, receipt), previous_id='0'*64, sequence=2, now=1000)

    def test_unindexed_file_symlink_and_changed_bytes_are_rejected(self):
        root = Path(self.profile['artifacts']['sidecar']['root'])
        extra = root/'extra.py'
        extra.write_text('not indexed')
        with self.assertRaisesRegex(ManualRuntimeError, 'unindexed_artifact'):
            verify_artifacts(self.profile)
        extra.unlink()
        extra.symlink_to(root/'fixture')
        with self.assertRaisesRegex(ManualRuntimeError, 'artifact_symlink'):
            verify_artifacts(self.profile)
        extra.unlink()
        (root/'fixture').write_text('changed')
        with self.assertRaisesRegex(ManualRuntimeError, 'artifact_changed'):
            verify_artifacts(self.profile)

    def test_untrusted_parent_and_group_writable_file_are_rejected(self):
        root = Path(self.profile['artifacts']['sidecar']['root'])
        path = root/'fixture'
        path.chmod(0o666)
        with self.assertRaisesRegex(ManualRuntimeError, 'untrusted_file'):
            verify_artifacts(self.profile)
        path.chmod(0o600)
        root.chmod(0o777)
        with self.assertRaisesRegex(ManualRuntimeError, 'untrusted_parent'):
            verify_artifacts(self.profile)

    def test_original_recording_authority_is_checksum_bound(self):
        Path(self.profile['paths']['registryPath']).write_bytes(b'changed registry')
        with self.assertRaisesRegex(ManualRuntimeError, 'recording_authority_changed'):
            verify_artifacts(self.profile)

    def test_empty_writable_directory_is_rejected(self):
        root = Path(self.profile['artifacts']['sidecar']['root'])
        empty = root / '__pycache__'
        empty.mkdir(mode=0o777)
        empty.chmod(0o777)
        with self.assertRaisesRegex(ManualRuntimeError, 'untrusted_artifact_directory'):
            verify_artifacts(self.profile)

    def test_nonexact_profiles_fail_closed(self):
        for change in (
            lambda p: p.update(unknown='anything'),
            lambda p: p['settings'].update(paidProvidersEnabled=False),
            lambda p: p['settings'].update(manualOnly=False),
            lambda p: p['settings'].update(maxAttempts=True),
            lambda p: p['ingress']['origins'].__setitem__(0, 'http://192.168.0.178:8180'),
            lambda p: p['artifacts']['server']['files'].update({'../escape': '1'*64}),
            lambda p: p['paths'].update(recordingRoot='/var/lib/real-runs'),
            lambda p: p['credentialPolicy'].update(providerReaders=['native', 'sidecar']),
        ):
            profile = copy.deepcopy(self.profile)
            change(profile)
            with self.subTest(profile=profile['settings']), self.assertRaises(ManualRuntimeError):
                validate_profile(profile)

    def test_qualification_requires_all_gates_fake_provider_freshness_and_exact_ci(self):
        original = qualification(self.profile)
        validate_qualification(self.profile, original, now=1000)
        for change in (
            lambda r: r['gates'].update(singleRecorder=False),
            lambda r: r['gates'].pop('cleanup'),
            lambda r: r.update(externalProviderCalls=True),
            lambda r: r.update(externalProviderCalls=1),
            lambda r: r.update(providerMode='real'),
            lambda r: r.update(observedUnix=float('nan')),
            lambda r: r.update(observedUnix=1001),
            lambda r: r['sourceCi']['gym'].update(sha='c'*40),
            lambda r: r['sourceCi']['engine']['checks'][0].update(conclusion='skipped'),
        ):
            receipt = copy.deepcopy(original)
            change(receipt)
            with self.subTest(receipt=receipt), self.assertRaises(ManualRuntimeError):
                validate_qualification(self.profile, receipt, now=1000)
        with self.assertRaisesRegex(ManualRuntimeError, 'qualification_stale'):
            validate_qualification(self.profile, original, now=100000)

    def test_exact_approval_cannot_authorize_other_tuple_previous_or_sequence(self):
        receipt = qualification(self.profile)
        exact = approval(self.profile, receipt)
        validate_approval(self.profile, receipt, exact, previous_id='0'*64, sequence=2)
        for field, value in (('approved', False), ('scope', 'implement-only'), ('profileSha256', '9'*64), ('qualificationSha256', '8'*64), ('previousRuntimeId', '7'*64), ('sequence', True)):
            changed = {**exact, field: value}
            with self.subTest(field=field), self.assertRaises(ManualRuntimeError):
                validate_approval(self.profile, receipt, changed, previous_id='0'*64, sequence=2)

    def test_truthful_capability_and_native_rng_not_fabricated(self):
        pins = recording_pins(self.profile)
        self.assertTrue(pins['config']['paidProvidersEnabled'])
        self.assertEqual(pins['config']['gameAiMode'], 'engine')
        self.assertEqual(pins['models']['model'], 'gpt-6-luna')
        self.assertIsNone(pins['rng'])
        self.assertIsNone(pins['decks'])
        self.assertEqual(pins['bindings']['fingerprints'], self.profile['catalog']['bindings'])

    def test_no_ambient_key_or_budget_enters_native_launch(self):
        runtime = ValidatedRuntime(canonical(self.profile), digest(self.profile), 2)
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'qa-ambient-key', 'COMMANDER_GYM_OPENAI_BUDGET_CAP_USD': '1'}):
            spec = native_spec(runtime, credential_directory=Path('/run/qa-credentials'))
        self.assertNotIn('OPENAI_API_KEY', spec.environment)
        self.assertNotIn('qa-ambient-key', str(spec.arguments))
        self.assertEqual(spec.environment['GAME_AI_MODE'], 'engine')
        self.assertTrue(any('configtree:/run/qa-credentials/' in arg for arg in spec.arguments))

    def test_sidecar_verification_precedes_any_credential_read(self):
        runtime = ValidatedRuntime(canonical(self.profile), digest(self.profile), 2)
        (Path(self.profile['artifacts']['sidecar']['root'])/'fixture').write_text('tampered')
        reader = unittest.mock.Mock(side_effect=AssertionError('credential opened'))
        with self.assertRaises(ManualRuntimeError):
            sidecar_environment(runtime, credential_directory=Path('/run/qa'), uid=self.profile['identities']['sidecarUid'], credential_reader=reader)
        reader.assert_not_called()

    def test_literal_fake_credentials_are_confined_to_sidecar_fresh_environment(self):
        runtime = ValidatedRuntime(canonical(self.profile), digest(self.profile), 2)
        def fake_reader(directory, name, **kwargs):
            return 'export OPENAI_API_KEY="qa-placeholder"' if name == 'openai.env' else 'qa-placeholder-token-never-real-000000'
        env = sidecar_environment(runtime, credential_directory=Path('/run/qa'), uid=self.profile['identities']['sidecarUid'], credential_reader=fake_reader)
        self.assertEqual(env['OPENAI_API_KEY'], 'qa-placeholder')
        self.assertFalse(any('BUDGET' in name or 'RECORDING_ROOT' in name or 'SESSION_MAX' in name for name in env))
        self.assertEqual(env['COMMANDER_GYM_OPENAI_MAX_ATTEMPTS'], '2')
        self.assertIn('-I', sidecar_spec(runtime, env).arguments)

    def test_validated_runtime_does_not_alias_mutable_input(self):
        runtime = ValidatedRuntime(canonical(self.profile), digest(self.profile), 2)
        copy_profile = runtime.profile
        copy_profile['settings']['model'] = 'changed'
        self.assertEqual(runtime.profile['settings']['model'], 'gpt-6-luna')

    def test_duplicate_json_keys_and_nan_are_rejected(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}'):
            with self.assertRaises(ManualRuntimeError):
                decode(raw)


if __name__ == '__main__':
    unittest.main()
