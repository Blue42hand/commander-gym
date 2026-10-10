"""Reader-only acceptance in an offline container's own systemd manager.

Synthetic production-shaped metadata exercises service_runtime without issuing
qualification/approval receipts, starting application services or any games.
It is not a ProtectedRuntimeHost rollout or recording-authority qualification.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

from commander_gym.manual_runtime_config import service_units, shared_https_nginx
from commander_gym.manual_runtime_credentials import UNITS
from commander_gym.manual_runtime_launch import _credential, native_spec, sidecar_environment
from commander_gym.manual_runtime_profile import ManualRuntimeError, canonical, digest, validate_profile
from commander_gym.manual_runtime_service import GATE, service_runtime
from test_manual_runtime_profile import fixture_profile

FAKE_PROVIDER = 'credential-fixture-only-never-a-provider-key'
FAKE_TOKEN = 'synthetic-callback-fixture-only-00000000'
BOOT = Path('/proc/sys/kernel/random/boot_id')
SOURCE = Path('/run/credential-fixture-sources')
REPORT = Path('/run/credential-fixture-report.json')


def install_artifact(role, files):
    inventory = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    root = (Path('/usr/local/libexec/argentum-' + ('native' if role == 'legacyNative' else 'source'))
            if role in ('legacyNative', 'legacySource') else
            Path('/srv/argentum-luna/artifacts') / role / digest(inventory))
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o644)
    return {'root': str(root), 'uid': 0, 'files': inventory}


def synthetic_profile():
    # Only this disposable filesystem has these fixed production paths. Hashes
    # describe real fake files; no historical registry identity is invented.
    base = Path('/run/credential-fixture-profile')
    base.mkdir()
    profile = fixture_profile(base)
    profile['purpose'] = 'native-manual-play'
    profile['identities'] = {'nativeUid': 10001, 'sidecarUid': 10002, 'proxyUid': 10003}
    for role in profile['artifacts']:
        root = Path(profile['artifacts'][role]['root'])
        files = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
        if role == 'dependencies':
            wheel = b'non-executable synthetic wheel bytes'
            lock = {'schemaVersion': 1, 'python': 'python/bin/python3.12', 'java': 'java/bin/java',
                    'pythonPath': ['site-packages'], 'packages': {'openai': {
                        'version': '3.27.0', 'wheelSha256': hashlib.sha256(wheel).hexdigest()}}}
            files = {'runtime-lock.json': canonical(lock), 'wheels/fake.whl': wheel,
                     'python/bin/python3.12': b'fake interpreter never executed',
                     'java/bin/java': b'fake java never executed', 'site-packages/fake.py': b'# fake'}
        profile['artifacts'][role] = install_artifact(role, files)
    profile['paths'] = {'recordingRoot': '/var/lib/commander-gym/runs/native',
                        'lifecycleSocket': '/run/argentum-play/lifecycle.sock',
                        'recorderSocket': '/run/argentum-luna-ipc/native.sock',
                        'registryPath': '/etc/argentum-play/recording-dispositions/acknowledged-incomplete.json'}
    # service_runtime deliberately does not claim a recording authority gate.
    # The registry is only a synthetic empty inventory and is never acquired.
    registry = Path(profile['paths']['registryPath'])
    registry.parent.mkdir(parents=True)
    registry.write_bytes(b'{}')
    profile['recordingAuthority'] = {'registryUid': 0, 'registrySha256': hashlib.sha256(b'{}').hexdigest()}
    artifacts = profile['artifacts']
    config = service_units(launcher_root=artifacts['launcher']['root'], dependency_root=artifacts['dependencies']['root'],
                           python_relative='python/bin/python3.12')
    config['nginx.conf'] = shared_https_nginx(frontend_root=artifacts['frontend']['root'],
        tailnet_origin='https://tolaria.example.test', lan_address='192.168.0.178', lan_subnet='192.168.0.0/24')
    config['lan-server.crt'] = 'synthetic certificate never served'
    artifacts['config'] = install_artifact('config', {name: text.encode() for name, text in config.items()})
    validate_profile(profile)
    return profile


def probe(role, scenario):
    directory = Path(os.environ['CREDENTIALS_DIRECTORY'])
    if scenario == 'wrong-env':
        os.environ['CREDENTIALS_DIRECTORY'] = str(SOURCE)
        directory = Path(os.environ['CREDENTIALS_DIRECTORY'])
    observed = []
    # This observer does not replace the reader: every observed invocation calls
    # the actual production reader and actual service_runtime checks.
    import commander_gym.manual_runtime_launch as launch
    def reader(directory, name, **kwargs):
        observed.append(name)
        return _credential(directory, name, **kwargs)
    try:
        runtime = service_runtime(role, directory, boot_id=BOOT.read_text().strip())
        if role == 'sidecar':
            env = launch.sidecar_environment(runtime, credential_directory=directory, uid=os.getuid(), credential_reader=reader)
            assert env['OPENAI_API_KEY'] == FAKE_PROVIDER and env['COMMANDER_GYM_SIDECAR_TOKEN'] == FAKE_TOKEN
        elif role == 'native':
            assert _credential(directory, 'commander-gym.sidecar.token', uid=os.getuid(), limit=1024, role=role) == FAKE_TOKEN
            spec = native_spec(runtime, credential_directory=directory)
            assert not any('OPENAI' in k or 'TOKEN' in k for k in spec.environment)
            assert FAKE_PROVIDER not in repr(spec) and FAKE_TOKEN not in repr(spec)
        if role != 'sidecar':
            assert not (directory / 'openai.env').exists()
            try:
                _credential(directory, 'openai.env', uid=os.getuid(), limit=16384, role=role)
            except ManualRuntimeError as error:
                assert str(error) == 'credential_role'
            else:
                raise AssertionError('provider reader admitted')
        if role == 'recorder':
            assert not (directory / 'commander-gym.sidecar.token').exists()
        assert scenario == 'valid'
        info = (directory / 'profile.json').stat()
        print(json.dumps({'result': 'accepted', 'role': role, 'uid': os.getuid(), 'copyUid': info.st_uid,
                          'copyGid': info.st_gid, 'copyMode': oct(info.st_mode & 0o7777),
                          'directoryMode': oct(directory.stat().st_mode & 0o7777),
                          'readonly': bool(os.statvfs(directory).f_flag & os.ST_RDONLY)}), flush=True)
        time.sleep(30)  # Parent probes denied UIDs inside this service mount.
    except (ManualRuntimeError, OSError) as error:
        if scenario == 'valid':
            metadata = {}
            for label, path in (('run', Path('/run')), ('credentialsRoot', Path('/run/credentials')),
                                ('directory', directory), ('copy', directory / 'profile.json')):
                try:
                    info = path.stat()
                    metadata[label] = {'uid': info.st_uid, 'gid': info.st_gid, 'mode': oct(info.st_mode & 0o7777)}
                    try:
                        metadata[label]['readonly'] = bool(os.statvfs(path).f_flag & os.ST_RDONLY)
                    except OSError:
                        metadata[label]['readonly'] = None
                except OSError as failure:
                    metadata[label] = {'accessible': False, 'errno': failure.errno}
            print(json.dumps({'result': 'unexpected-hold', 'code': str(error) if isinstance(error, ManualRuntimeError) else type(error).__name__, 'directoryPath': str(directory), 'metadata': metadata}), flush=True)
            time.sleep(15)
            raise AssertionError('valid fixture held') from None
        assert not observed, 'secret reader reached before service guard'
        print(json.dumps({'result': 'held', 'role': role, 'code': type(error).__name__}), flush=True)


@unittest.skipUnless(os.environ.get('REQUIRE_SYSTEMD_CREDENTIALS') == '1', 'offline systemd container CI only')
class SystemdCredentialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert Path('/.dockerenv').exists() and Path('/proc/1/comm').read_text().strip() == 'systemd'
        assert os.getuid() == 0 and not Path('/run/host').exists()
        # No host bus/mounts are supplied by the CI invocation; Docker network=none.
        cls.profile = synthetic_profile()
        SOURCE.mkdir(mode=0o700)
        cls.write_source('openai.env', ('OPENAI_API_KEY=' + FAKE_PROVIDER).encode())
        cls.write_source('commander-gym.sidecar.token', FAKE_TOKEN.encode())
        GATE.parent.mkdir(mode=0o755)

    @classmethod
    def write_source(cls, name, data):
        path = SOURCE / name
        path.write_bytes(data)
        path.chmod(0o600)

    def ctl(self, *args, check=True):
        return subprocess.run(['systemctl', *args], capture_output=True, text=True, check=check, timeout=40)

    def setUp(self):
        self.profile = copy.deepcopy(type(self).profile)
        self.write_source('profile.json', canonical(self.profile))
        self.write_gate()

    def write_gate(self, **overrides):
        gate = {'runtimeId': digest(self.profile), 'sequence': 1, 'bootId': BOOT.read_text().strip()}
        gate.update(overrides)
        GATE.write_bytes(canonical(gate))
        GATE.chmod(0o644)  # Nonsecret attestation, protected root ancestry.

    def unit(self, role, scenario='valid', extra=''):
        uid = 10002 if role == 'sidecar' else 10001
        names = ['profile.json'] + (['openai.env', 'commander-gym.sidecar.token'] if role == 'sidecar'
                                   else ['commander-gym.sidecar.token'] if role == 'native' else [])
        credentials = ''.join(f'LoadCredential={name}:{SOURCE / name}\n' for name in names)
        path = Path('/etc/systemd/system') / UNITS[role]
        path.write_text(f'[Service]\nType=exec\nUser={uid}\nGroup={uid}\n{credentials}'
                        'Environment=PYTHONPATH=/opt/fixture:/opt/fixture/tests\n'
                        f'ExecStart=/usr/bin/python3 -B /opt/fixture/tests/test_manual_runtime_credentials_systemd.py probe {role} {scenario}\n'
                        f'StandardOutput=file:{REPORT}\nStandardError=journal\n{extra}')
        self.ctl('daemon-reload')
        REPORT.unlink(missing_ok=True)
        self.ctl('reset-failed', UNITS[role], check=False)
        self.ctl('start', UNITS[role])
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if REPORT.exists() and REPORT.stat().st_size:
                result = json.loads(REPORT.read_text())
                if result['result'] == 'unexpected-hold':
                    pid = self.ctl('show', '-p', 'MainPID', '--value', UNITS[role]).stdout.strip()
                    diagnostic = subprocess.run(['nsenter', '-t', pid, '-m', '/usr/bin/python3', '-c',
                        "import os,json,sys; p=sys.argv[1]; print(json.dumps({'present':os.path.exists(p),'siblings':os.listdir('/run/credentials')}))",
                        '/run/credentials/' + UNITS[role]], capture_output=True, text=True, timeout=10)
                    result['rootNamespaceDiagnostic'] = diagnostic.stdout.strip() or diagnostic.stderr.strip()
                return result
            time.sleep(.05)
        raise AssertionError(self.ctl('status', UNITS[role], check=False).stdout)

    def tearDown(self):
        for unit in UNITS.values():
            self.ctl('stop', unit, check=False)
        journal = subprocess.run(['journalctl', '--no-pager'], capture_output=True, text=True, check=True).stdout
        self.assertNotIn(FAKE_PROVIDER, journal)
        self.assertNotIn(FAKE_TOKEN, journal)

    def test_actual_nonroot_copies_all_roles_and_other_uid_denied(self):
        for role in UNITS:
            with self.subTest(role=role):
                report = self.unit(role)
                self.assertEqual('accepted', report['result'], report)
                self.assertEqual((0, 0, '0o440', '0o550', True),
                                 (report['copyUid'], report['copyGid'], report['copyMode'], report['directoryMode'], report['readonly']))
                pid = self.ctl('show', '-p', 'MainPID', '--value', UNITS[role]).stdout.strip()
                names = ['profile.json'] + (['openai.env', 'commander-gym.sidecar.token'] if role == 'sidecar'
                                           else ['commander-gym.sidecar.token'] if role == 'native' else [])
                for uid in (10003, 10001 if role == 'sidecar' else 10002):
                    for name in names:
                        # Given the exact service mount, another UID still cannot
                        # raw-open any copy, including each actual secret copy.
                        command = ['nsenter', '-t', pid, '-m', 'setpriv', '--reuid', str(uid), '--regid', str(uid), '--clear-groups',
                                   '/usr/bin/python3', '-c',
                                   "import os,sys;\ntry: os.open(sys.argv[1],os.O_RDONLY)\nexcept PermissionError: sys.exit(0)\nelse: sys.exit(1)",
                                   '/run/credentials/' + UNITS[role] + '/' + name]
                        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
                        self.assertEqual(0, result.returncode, result.stderr)
                self.ctl('stop', UNITS[role])

    def test_real_private_file_modes_owner_gid_and_unprotected_ancestor(self):
        directory = Path('/run/credential-fixture-private')
        directory.mkdir(mode=0o700)
        file = directory / 'fake.txt'
        file.write_bytes(b'fake-private-credential')
        file.chmod(0o600)
        self.assertEqual('fake-private-credential', _credential(directory, file.name, uid=0, limit=100))
        for mode in (0o440, 0o644, 0o660, 0o601, 0o4600, 0o2600):
            file.chmod(mode)
            self.assertEqual(mode, file.stat().st_mode & 0o7777)
            with self.subTest(mode=mode), self.assertRaises(ManualRuntimeError):
                _credential(directory, file.name, uid=0, limit=100)
        file.chmod(0o600)
        for owner, group in ((10003, 0), (0, 10003)):
            os.chown(file, owner, group)
            with self.subTest(owner=owner, group=group), self.assertRaises(ManualRuntimeError):
                _credential(directory, file.name, uid=0, limit=100)
        os.chown(file, 0, 0)
        os.chown(directory, 10003, 10003)
        with self.assertRaises(ManualRuntimeError):
            _credential(directory, file.name, uid=0, limit=100)
        os.chown(directory, 0, 0)

    def test_service_guards_hold_before_provider_reads(self):
        original = copy.deepcopy(self.profile)
        for scenario in ('missing-gate', 'wrong-boot', 'wrong-profile', 'wrong-sequence', 'changed-profile', 'changed-artifact', 'wrong-role', 'wrong-env'):
            with self.subTest(scenario=scenario):
                self.profile = copy.deepcopy(original)
                self.write_source('profile.json', canonical(self.profile))
                self.write_gate()
                extra = ''
                artifact = None
                if scenario == 'missing-gate': GATE.unlink()
                elif scenario == 'wrong-boot': self.write_gate(bootId='stale-boot')
                elif scenario == 'wrong-profile': self.write_gate(runtimeId='0' * 64)
                elif scenario == 'wrong-sequence': self.write_gate(sequence=0)
                elif scenario == 'changed-profile':
                    self.profile['gymSha'] = 'c' * 40
                    self.write_source('profile.json', canonical(self.profile))
                elif scenario == 'changed-artifact':
                    root = Path(self.profile['artifacts']['sidecar']['root'])
                    artifact = root / next(iter(self.profile['artifacts']['sidecar']['files']))
                    saved = artifact.read_bytes()
                    artifact.write_bytes(b'changed fake artifact')
                elif scenario == 'wrong-role':
                    self.profile['identities']['sidecarUid'] = 10004
                    self.write_source('profile.json', canonical(self.profile))
                    self.write_gate()
                try:
                    self.assertEqual('held', self.unit('sidecar', scenario, extra)['result'])
                finally:
                    self.ctl('stop', UNITS['sidecar'])
                    if artifact: artifact.write_bytes(saved)


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == 'probe':
        probe(sys.argv[2], sys.argv[3])
    else:
        unittest.main()
