"""Optional sealed local service contracts; all catalogs and credentials synthetic."""
import copy
from dataclasses import asdict
import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from commander_gym.manual_runtime_profile import (ManualRuntimeError, ValidatedRuntime, canonical, digest,
    validate_profile, verify_artifacts, recording_pins, validate_qualification)
from commander_gym.manual_runtime_local import (GATES, RecordingRegistries, local_recording_registry,
    local_environment, local_arguments)
from commander_gym.manual_runtime_launch import native_spec
from commander_gym.manual_runtime_config import service_units
from commander_gym.manual_runtime_host import ProtectedRuntimeHost, runtime_units, selected_targets, verify_local_identity
from commander_gym.game_server_binding_lmstudio_sidecar import local_component_ref
from commander_gym.identity import Pilot
from test_lmstudio import config
from test_manual_runtime_profile import fixture_profile, qualification
from test_game_server_binding_openai_sidecar import synthetic_catalog
from commander_gym.binding_catalog import load_binding_catalog
from commander_gym.game_server_bindings import GameServerBindingRegistry
from commander_gym.recorder_bridge import _RecordingPilot


def local_profile(root, *, source_entries=True):
    profile = fixture_profile(root)
    cfg = config()
    pilot = Pilot(pilot_id='local-fixture', revision='1', generalist_base=local_component_ref(cfg))
    catalog = root/'artifacts'/'localCatalog'; catalog.mkdir(parents=True)
    roster = synthetic_catalog(catalog, pilot=pilot)
    loaded = load_binding_catalog(roster, instance_root=catalog, pilot_factory=lambda p,b: _RecordingPilot(p))
    registry = GameServerBindingRegistry(loaded.resolver, loaded.binding_ids)
    files = {str(p.relative_to(catalog)): hashlib.sha256(p.read_bytes()).hexdigest() for p in catalog.rglob('*') if p.is_file()}
    manifest = {'provider':'lmstudio','model':cfg.model,'profiles':list(registry.binding_fingerprints),'files':files}
    (catalog/'manifest.json').write_bytes(canonical(manifest))
    files['manifest.json'] = hashlib.sha256(canonical(manifest)).hexdigest()
    configuration = root/'artifacts'/'localConfig'; configuration.mkdir()
    (configuration/'inference.json').write_bytes(canonical(asdict(cfg)))
    for role, directory, inventory in [('localCatalog',catalog,files),('localConfig',configuration,
            {'inference.json':hashlib.sha256(canonical(asdict(cfg))).hexdigest()})]:
        profile['artifacts'][role]={'root':str(directory),'uid':os.getuid(),'files':inventory}
    profile['localInference']={'uid':10004,'gid':10004,'port':8084,'config':asdict(cfg),
        'catalog':{'roster':str(roster.relative_to(catalog)),'manifest':'manifest.json',
            'bindings':registry.binding_fingerprints,'pilotFingerprint':pilot.fingerprint(),
            'componentDigest':local_component_ref(cfg).digest}}
    launcher = profile['artifacts']['launcher']
    for name in ('scripts/local_sidecar_entry.py', 'commander_gym/manual_runtime_local.py', 'commander_gym/lmstudio_provider.py', 'commander_gym/lmstudio_http_worker.py'):
        path = Path(launcher['root'])/name;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(Path(name).read_bytes() if source_entries else b'# nonexecuted credential fixture entry');launcher['files'][name]=hashlib.sha256(path.read_bytes()).hexdigest()
    return profile


class LocalRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory(prefix='.local-runtime-qa-',dir=Path.cwd());self.addCleanup(self.tmp.cleanup)
        self.profile=local_profile(Path(self.tmp.name))
        self.runtime=ValidatedRuntime(canonical(self.profile),digest(self.profile),2)

    def test_canonical_catalog_and_exact_configuration_seals(self):
        validate_profile(self.profile);verify_artifacts(self.profile)
        registry=local_recording_registry(self.profile)
        self.assertEqual(registry.binding_fingerprints,self.profile['localInference']['catalog']['bindings'])
        for mutate in [lambda p:p['localInference'].update(uid=p['identities']['sidecarUid']),
                       lambda p:p['localInference'].update(port=8083),
                       lambda p:p['localInference']['config'].update(model='other'),
                       lambda p:p['catalog']['bindings'].update({'seat-a':'1'*64}),
                       lambda p:p['localInference']['config'].update(endpoint='https://api.openai.com:443')]:
            changed=copy.deepcopy(self.profile);mutate(changed)
            with self.assertRaises((ValueError,ManualRuntimeError)):validate_profile(changed)
        path=Path(self.profile['artifacts']['localConfig']['root'])/'inference.json'
        path.write_text('{}')
        with self.assertRaisesRegex(ManualRuntimeError,'artifact_changed'):verify_artifacts(self.profile)

    def test_local_credential_read_is_after_seals_and_canonical_validation(self):
        reader=Mock(return_value='local-test-token-'+'a'*32)
        env=local_environment(self.runtime,credential_directory=Path('/fixture'),uid=10004,credential_reader=reader)
        self.assertEqual(reader.call_args.args[1],'commander-gym.local.token')
        self.assertEqual(reader.call_args.kwargs['role'],'local')
        self.assertFalse(any('OPENAI' in k for k in env))
        self.assertNotIn('COMMANDER_GYM_SIDECAR_TOKEN',env)
        reader.reset_mock()
        with self.assertRaisesRegex(ManualRuntimeError,'local_identity'):
            local_environment(self.runtime,credential_directory=Path('/fixture'),uid=10002,credential_reader=reader)
        reader.assert_not_called()
        with patch('commander_gym.manual_runtime_local.local_recording_registry',side_effect=ValueError('bad catalog')):
            with self.assertRaises(ValueError):local_environment(self.runtime,credential_directory=Path('/fixture'),uid=10004,credential_reader=reader)
        reader.assert_not_called()

    def test_default_native_and_luna_units_remain_identical(self):
        args=dict(launcher_root='/srv/argentum-luna/artifacts/launcher/'+'a'*64,
                  dependency_root='/srv/argentum-luna/artifacts/dependencies/'+'b'*64,python_relative='python/bin/python3.12')
        default=service_units(**args);local=service_units(**args,local_inference=self.profile['localInference'])
        self.assertEqual(default['units/argentum-luna-sidecar.service'],local['units/argentum-luna-sidecar.service'])
        unit=local['units/argentum-local-sidecar.service']
        self.assertIn('Type=notify',unit);self.assertIn('User=commander-local',unit)
        self.assertIn('IPAddressDeny=any',unit);self.assertNotIn('LoadCredential=openai.env',unit)
        self.assertNotIn('LoadCredential=commander-gym.sidecar.token',unit)
        self.assertIn('InaccessiblePaths=',unit);self.assertIn('/run/argentum-luna-ipc',unit)
        self.assertIn('commander-gym.local.token',local['units/argentum-play.conf'])
        self.assertNotIn('local',default['units/argentum-play.conf'])
        changed=native_spec(self.runtime,credential_directory=Path('/fixture')).environment
        base=copy.deepcopy(self.profile);base.pop('localInference')
        for role in ('localConfig','localCatalog'):base['artifacts'].pop(role)
        original=native_spec(ValidatedRuntime(canonical(base),digest(base),2),credential_directory=Path('/fixture')).environment
        self.assertEqual({k:v for k,v in changed.items() if not k.startswith('COMMANDER_GYM_LOCAL_') and k!='NATIVE_RELEASE_ID'},
                         {k:v for k,v in original.items() if k!='NATIVE_RELEASE_ID'})
        self.assertEqual(changed['GAME_AI_MODE'],'engine')
        self.assertNotIn('TOKEN',' '.join(changed))
        self.assertNotIn('argentum-local-sidecar.service',runtime_units(base))
        self.assertIn('argentum-local-sidecar.service',runtime_units(self.profile))
        self.assertNotIn('units/argentum-local-sidecar.service',selected_targets(base))
        self.assertIn('units/argentum-local-sidecar.service',selected_targets(self.profile))

    def test_local_qualification_gates_and_recording_identity_are_explicit(self):
        receipt=qualification(self.profile)
        with self.assertRaises(ManualRuntimeError):validate_qualification(self.profile,receipt,now=1000)
        receipt['gates'].update({gate:True for gate in GATES})
        validate_qualification(self.profile,receipt,now=1000)
        pins=recording_pins(self.profile)
        self.assertEqual(pins['models']['local'],self.profile['localInference']['config'])
        self.assertEqual(pins['models']['model'],'gpt-6-luna')
        self.assertEqual(pins['bindings']['local'],self.profile['localInference']['catalog'])
        self.assertIn('--inference-config',local_arguments(self.profile))

    def test_live_catalog_collision_fails_before_local_registration(self):
        cloud=Mock();cloud.contains.return_value=False;local=Mock()
        mixed=RecordingRegistries(cloud,local,{'local-binding'})
        mixed.create_seat('player','local-binding');local.create_seat.assert_called_once()
        cloud.contains.return_value=True;local.reset_mock()
        with self.assertRaisesRegex(ManualRuntimeError,'local_binding_collision'):mixed.create_seat('player','local-binding')
        local.create_seat.assert_not_called()
        static=SimpleNamespace(binding_fingerprints={'cloud-binding':'x'},create_seat=Mock())
        mixed=RecordingRegistries(static,local,{'local-binding'})
        mixed.create_seat('player','cloud-binding');static.create_seat.assert_called_once()

    def test_stop_union_includes_previous_local_after_disabling(self):
        disabled=copy.deepcopy(self.profile);disabled.pop('localInference')
        host=ProtectedRuntimeHost.__new__(ProtectedRuntimeHost);host.anchor=disabled
        host.pending={'candidate':SimpleNamespace(profile=disabled),
                      'previous':SimpleNamespace(kind='manual-luna-v1',runtime_id='previous')}
        with patch('commander_gym.manual_runtime_host.load_bundle',return_value=(self.profile,None,None)):
            self.assertIn('argentum-local-sidecar.service',host._units())

    def test_local_identity_may_not_join_cloud_group(self):
        with patch('commander_gym.manual_runtime_host.pwd.getpwnam',return_value=SimpleNamespace(pw_uid=10004,pw_gid=10004)), \
             patch('commander_gym.manual_runtime_host.grp.getgrnam',side_effect=lambda name:SimpleNamespace(gr_gid=10004 if name=='commander-local' else 10002)), \
             patch('commander_gym.manual_runtime_host.os.getgrouplist',return_value=[10004]):
            verify_local_identity(self.profile)
            with patch('commander_gym.manual_runtime_host.os.getgrouplist',return_value=[10004,10002]):
                with self.assertRaisesRegex(ManualRuntimeError,'local_identity_isolation'):verify_local_identity(self.profile)

    def test_selected_profile_identity_checked_in_both_preflight_paths(self):
        host=ProtectedRuntimeHost.__new__(ProtectedRuntimeHost)
        host.anchor={'identities':self.profile['identities']};host._verify_selected_files=Mock()
        snapshot=SimpleNamespace(kind='manual-luna-v1',runtime_id=self.runtime.runtime_id,sequence=2)
        with patch('commander_gym.manual_runtime_host.load_bundle',return_value=(self.profile,{'observedUnix':1000},{'previousRuntimeId':'0'*64,'sequence':2})), \
             patch('commander_gym.manual_runtime_host.precredential_validate',return_value=self.runtime), \
             patch('commander_gym.manual_runtime_host.verify_local_identity',side_effect=ManualRuntimeError('changed local group')), \
             patch('commander_gym.manual_runtime_host.atomic_root') as write:
            for method in (host._attest_precredential,host.verify_snapshot_unlocked):
                with self.assertRaisesRegex(ManualRuntimeError,'changed local group'):method(snapshot)
            write.assert_not_called();host._verify_selected_files.assert_not_called()

    def test_bootstrap_validates_local_profile_outside_checkout_without_site_packages(self):
        import subprocess,sys
        launcher=Path(self.profile['artifacts']['launcher']['root'])
        package=launcher/'commander_gym';package.mkdir(exist_ok=True)
        for path in Path('commander_gym').glob('manual_runtime_*.py'):
            (package/path.name).write_bytes(path.read_bytes())
        (package/'__init__.py').write_text('')
        source=Path(self.tmp.name)/'profile.json';source.write_bytes(canonical(self.profile))
        script='import sys,json;sys.path.insert(0,sys.argv[1]);from commander_gym.manual_runtime_profile import validate_profile;validate_profile(json.load(open(sys.argv[2])));import commander_gym.lmstudio_provider as m;from pathlib import Path;assert Path(m.__file__).with_name("lmstudio_http_worker.py").is_file()'
        result=subprocess.run([sys.executable,'-I','-S','-B','-c',script,str(launcher),str(source)],
                              cwd='/',env={},capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_local_autoconfiguration_is_required_in_compiled_adapter(self):
        import io,zipfile
        from commander_gym.manual_runtime_qualification import inspect_matched_artifacts
        def jar(items):
            out=io.BytesIO()
            with zipfile.ZipFile(out,'w') as archive:
                for name,data in items.items():archive.writestr(name,data)
            return out.getvalue()
        for has_local in (False,True):
            imports='org.commandergym.argentum.CommanderGymAutoConfiguration\n'
            files={'META-INF/MANIFEST.MF':'Argentum-Revision: '+self.profile['engineSha']+'\nCommander-Gym-Revision: '+self.profile['gymSha']+'\n',
                   'org/commandergym/argentum/CommanderGymControllerProvider.class':b'fixture'}
            if has_local:
                imports+='org.commandergym.argentum.CommanderGymLocalAutoConfiguration\n'
                files['org/commandergym/argentum/CommanderGymLocalAutoConfiguration.class']=b'fixture'
            files['META-INF/spring/org.springframework.boot.autoconfigure.AutoConfiguration.imports']=imports
            adapter=jar(files)
            server=jar({'META-INF/MANIFEST.MF':'Argentum-Revision: '+self.profile['engineSha']+'\n',
                        'BOOT-INF/lib/commander-gym-argentum-adapter.jar':adapter})
            with patch('commander_gym.manual_runtime_qualification.protected_bytes',side_effect=[adapter,server]):
                if has_local:self.assertIn('embeddedAdapter',inspect_matched_artifacts(self.profile))
                else:
                    with self.assertRaisesRegex(ManualRuntimeError,'compiled_local_provider_missing'):inspect_matched_artifacts(self.profile)

    def test_local_role_cannot_read_cloud_credential_names(self):
        from commander_gym.manual_runtime_credentials import credential
        for name in ('openai.env','commander-gym.sidecar.token'):
            with self.assertRaisesRegex(ManualRuntimeError,'credential_role'):
                credential(Path('/run/credentials/argentum-local-sidecar.service'),name,uid=10004,limit=100,role='local')

    def test_local_and_cloud_compose_one_capture_and_reporter(self):
        from commander_gym.manual_runtime_recorder import run_recorder
        from commander_gym.manual_runtime_local import local_ipc
        import stat
        directory=local_ipc(self.profile);directory.mkdir()
        capture=Mock();reporter=Mock();bridge=Mock()
        factory=Mock(return_value=capture);reporters=Mock(return_value=reporter);bridges=Mock(return_value=bridge)
        cloud=SimpleNamespace(binding_fingerprints={},create_seat=Mock())
        actual=Path.lstat
        def info(path):
            if path==directory:return SimpleNamespace(st_mode=stat.S_IFDIR|0o750,st_uid=self.profile['identities']['nativeUid'])
            return actual(path)
        with patch.object(Path,'lstat',info),patch('os.getgroups',return_value=[10004]),patch('os.chown'):
            run_recorder(self.runtime,sidecar_gid=10002,stopping=lambda:True,capture_factory=factory,
                         reporter_factory=reporters,bridge_factory=bridges,registry_factory=lambda *a:cloud)
        factory.assert_called_once();reporters.assert_called_once();bridges.assert_called_once()
        self.assertIs(reporters.call_args.args[0],capture);self.assertIs(bridges.call_args.args[0],capture)
        self.assertEqual(bridges.call_args.kwargs['local_binding_ids'],self.profile['localInference']['catalog']['bindings'])
        bridge.start.assert_called_once();capture.close.assert_called_once()
