"""Offline disposable-container acceptance of real installation and service transactions.

Uses synthetic lifecycle/recorder/provider processes, never a game or real key.
This proves bootstrap/update/rollback plumbing, not native rules or human gameplay.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from commander_gym.manual_runtime_config import service_units, shared_https_nginx
from commander_gym.manual_runtime_profile import canonical, digest
from commander_gym.runtime_install import install
from commander_gym.runtime_release import pack_bundle, inspect_bundle, stage_bundle, wait_and_promote, rollback
from test_manual_runtime_profile import qualification, approval
from test_manual_runtime_credentials_systemd import synthetic_profile, install_artifact

NATIVE = b'''import hashlib,json,os,socket,time
from pathlib import Path
p=json.loads((Path(os.environ['CREDENTIALS_DIRECTORY'])/'profile.json').read_bytes())
r=hashlib.sha256(json.dumps(p,sort_keys=True,separators=(',',':')).encode()).hexdigest()
path=Path('/run/argentum-play/lifecycle.sock');path.unlink(missing_ok=True)
s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);s.bind(str(path));os.chmod(path,0o600);s.listen()
boot=str(os.getpid());opened=False;drained=False
while True:
 c,_=s.accept()
 with c:
  data=b''
  while not data.endswith(b'\\n'): data+=c.recv(4096)
  request=json.loads(data)
  if request['op']=='drain':opened=False;drained=True
  if request['op']=='resume':opened=True;drained=False
  busy=Path('/run/argentum-play/fixture-busy').exists()
  reply=dict(protocol=1,ok=True,releaseId=r,engineSha=p['engineSha'],gymSha=p['gymSha'],recordingSchemaVersion=1,
   bootId=boot,observedUnix=time.time(),recordingHealthy=True,recoveryComplete=True,activeGames=int(busy),
   pendingActivities=0,pendingRecordWrites=0,inFlightAdmissions=0,acceptingNewGames=opened,
   drainAcknowledged=drained,recordingDrainComplete=drained,drainId='fixture' if drained else '')
  c.sendall(json.dumps(reply).encode()+b'\\n')
'''
IDLE = b'import time\nwhile True: time.sleep(1)\n'


class RuntimeInstallSystemdTests(unittest.TestCase):
    def setUp(self):
        if os.environ.get('REQUIRE_RELEASE_INSTALL_SYSTEMD') != '1': self.skipTest('offline disposable release container only')
        self.assertTrue(Path('/.dockerenv').exists())
        self.assertTrue(Path('/opt/fixture/ISOLATED_RELEASE_TEST').exists())
        self.assertTrue(Path('/run/systemd/system').is_dir()); self.assertEqual(os.getuid(),0)
        self.assertFalse(Path('/etc/argentum-luna/current.json').exists())

    def command(self, *args):
        return subprocess.run(args,check=True,capture_output=True,text=True,timeout=60)

    def prepare(self):
        p=synthetic_profile()
        p['identities']={'nativeUid':10011,'sidecarUid':10012,'proxyUid':10013}
        p['ingress']['origins']=['https://fixture.ts.net']
        p['liveCatalog']={'checkoutRoot':'/srv/commander-bindings/checkout','snapshotRoot':'/var/lib/commander-bindings',
                          'roster':p['catalog']['roster'],'pollSeconds':5}
        source=Path('/opt/fixture')
        files={str(path.relative_to(source)):path.read_bytes() for path in (source/'commander_gym').rglob('*.py')}
        for name in ('run_manual_runtime.py','commander.py'):
            files['scripts/'+name]=(source/'scripts'/name).read_bytes()
            if name == 'run_manual_runtime.py':
                files['scripts/'+name] = (b'import os, types\nos.statvfs=lambda path: types.SimpleNamespace(f_bavail=128*1024**3,f_frsize=1)\n'
                                         + files['scripts/'+name])
        files.update({'scripts/manual_service_entry.py':NATIVE,'recorder_entry.py':IDLE,'sidecar_entry.py':IDLE,
                      'scripts/publish_binding_catalog.py':b'# fixture publisher; real Git data separately tested\n'})
        p['artifacts']['launcher']=install_artifact('launcher',files)
        old=Path(p['artifacts']['dependencies']['root'])
        deps={name:(old/name).read_bytes() for name in p['artifacts']['dependencies']['files']}
        deps['python/bin/python3.12']=Path(sys.executable).read_bytes()
        deps['qa-proxy/nginx']=b'#!/bin/sh\nexec /usr/bin/sleep infinity\n'
        p['artifacts']['dependencies']=install_artifact('dependencies',deps)
        for name in ('python/bin/python3.12','qa-proxy/nginx'):
            (Path(p['artifacts']['dependencies']['root'])/name).chmod(0o755)
        self.render(p)
        Path(p['liveCatalog']['checkoutRoot']).mkdir(parents=True)
        key=Path('/srv/commander-runtime/.config/commander-gym/openai.env')
        key.parent.mkdir(parents=True);key.write_text('OPENAI_API_KEY=offline-fixture-not-a-key\n');key.chmod(0o600)
        stub=Path('/usr/local/bin/tailscale');stub.write_text('#!/bin/sh\nexit 0\n');stub.chmod(0o755)
        return p

    def render(self,p):
        a=p['artifacts']
        units=service_units(launcher_root=a['launcher']['root'],dependency_root=a['dependencies']['root'],
                            python_relative='python/bin/python3.12',tailnet_only=True,live_catalog=p['liveCatalog'])
        units['nginx.conf']=shared_https_nginx(frontend_root=a['frontend']['root'],tailnet_origin='https://fixture.ts.net',
            lan_address=p['ingress']['lanAddress'],lan_subnet=p['ingress']['lanSubnet'],tailnet_only=True)
        p['artifacts']['config']=install_artifact('config',{n:t.encode() for n,t in units.items()})

    def bundle(self,p,previous='0'*64,sequence=1):
        q=qualification(p,now=time.time());a=approval(p,q,previous=previous,sequence=sequence)
        target=Path('/opt/fixture/bundles')/str(sequence)
        reviewed=pack_bundle(p,q,a,target)
        return target,inspect_bundle(target,reviewed)

    def wait_open(self,host):
        deadline=time.monotonic()+45
        while time.monotonic()<deadline:
            try:
                state=host.status()
                if state['acceptingNewGames']: return state
            except (OSError,ValueError): pass
            time.sleep(.5)
        self.fail('automatic admission did not recover')

    def test_clean_install_idle_restart_busy_update_failure_and_explicit_rollback(self):
        from commander_gym.manual_runtime_host import ProtectedRuntimeHost, CONFIG, GATE, load_bundle
        from commander_gym.manual_runtime_profile import ManualRuntimeError
        p=self.prepare();root,manifest=self.bundle(p)
        # Remove the selected artifact trees: installation must really stage them.
        for artifact in p['artifacts'].values(): shutil.rmtree(artifact['root'])
        # CI disk size is not a capacity qualification; production reserves remain.
        capacity=SimpleNamespace(f_bavail=128*1024**3,f_frsize=1)
        with patch('os.statvfs',return_value=capacity):
            result=install(root,manifest);self.assertEqual(result['state'],'installed')
            host=ProtectedRuntimeHost(p);first=self.wait_open(host)
            self.command('systemctl','stop','argentum-play.service')
            GATE.unlink(missing_ok=True)
            self.command('systemctl','start','argentum-play.service')
            recovered=self.wait_open(host);self.assertNotEqual(first['bootId'],recovered['bootId'])
            # Next release changes only the fixture label/source identity.
            second=copy.deepcopy(p);second['gymSha']='e'*40
            root2,m2=self.bundle(second,digest(p),2)
            with host.exclusive_lock():stage_bundle(root2,m2)
            marker=Path('/run/argentum-play/fixture-busy');marker.touch()
            with self.assertRaisesRegex(ManualRuntimeError,'not_idle'):
                wait_and_promote(host,second,m2['qualification'],m2['approval'],wait_seconds=0)
            self.assertEqual(host.status()['bootId'],recovered['bootId']);marker.unlink()
            self.assertEqual(wait_and_promote(host,second,m2['qualification'],m2['approval'])['state'],'promoted')
            self.assertEqual(host.current().runtime_id,digest(second))
            self.assertEqual(rollback(host,load_bundle=load_bundle)['state'],'promoted')
            self.assertEqual(host.current().runtime_id,digest(p));self.wait_open(host)
            # A candidate that cannot execute is stopped and the prior tuple returns.
            broken=copy.deepcopy(second);broken['gymSha']='f'*40
            a=p['artifacts']['launcher'];files={n:(Path(a['root'])/n).read_bytes() for n in a['files']}
            files['scripts/manual_service_entry.py']=b'raise SystemExit(1)\n'
            broken['artifacts']['launcher']=install_artifact('launcher',files);self.render(broken)
            root3,m3=self.bundle(broken,digest(p),4)
            with host.exclusive_lock():stage_bundle(root3,m3)
            with patch('commander_gym.manual_runtime_host.READINESS_TIMEOUT_SECONDS',15):
                with self.assertRaisesRegex(ManualRuntimeError,'candidate_failed_previous_restored'):
                    wait_and_promote(host,broken,m3['qualification'],m3['approval'])
            self.assertEqual(host.current().runtime_id,digest(p));self.wait_open(host)
            self.assertEqual(host.current().sequence,4)
