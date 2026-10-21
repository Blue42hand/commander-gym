"""Real separate-UID Unix admission on disposable Linux CI fixtures, no providers."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

import test_recorder_bridge as bridge_fixtures
from commander_gym.recorder_bridge import RecorderBridge


@unittest.skipUnless(sys.platform == 'linux' and os.getuid() == 0, 'requires isolated root Linux CI fixture')
class RecorderBridgeLinuxPeerTests(unittest.TestCase):
    def test_sidecar_uid_cannot_register_or_read_native_journals_but_can_send_matching_receipts(self):
        fixture = bridge_fixtures.RecorderBridgeTests()
        fixture.setUp()
        try:
            fixture.base.chmod(0o755)  # synthetic fixture only; journals remain 0700
            os.chown(fixture.ipc, 0, 65534)
            fixture.bridge = RecorderBridge(fixture.capture, fixture.registry,
                native_socket=fixture.ipc / 'native.sock', sidecar_socket=fixture.ipc / 'sidecar.sock',
                native_uid=0, sidecar_uid=65534, sidecar_gid=65534)
            fixture.bridge.start()
            fixture.register()
            request = fixture.event()
            child = r'''
import json, os, socket, sys
from pathlib import Path
from commander_gym.recorder_bridge import exchange
os.setgroups([]); os.setgid(65534); os.setuid(65534)
message=json.loads(sys.stdin.read())
failed=[]
for target in (sys.argv[1], sys.argv[3]):
    try:
        if target.endswith('.sock'):
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection: connection.connect(target)
        else: Path(target).read_bytes()
    except PermissionError: failed.append(target)
response=exchange(Path(sys.argv[2]), message, recorder_uid=0)
registration=dict(message, op='register', request={}); registration.pop('event')
try: exchange(Path(sys.argv[2]), registration, recorder_uid=0)
except ValueError: rejected=True
else: rejected=False
print(json.dumps({'denied':len(failed), 'receipt':response['ok'], 'registerRejected':rejected}))
'''
            result = subprocess.run([sys.executable, '-c', child, str(fixture.bridge.native_socket),
                str(fixture.bridge.sidecar_socket), str(fixture.source)], input=json.dumps(request),
                text=True, capture_output=True, timeout=5, check=True)
            self.assertEqual(json.loads(result.stdout), {'denied': 2, 'receipt': True, 'registerRejected': True})
            # An unrelated actual UID is denied even if filesystem group permissions allow connection.
            fixture.bridge.sidecar_uid = 65533
            denied = subprocess.run([sys.executable, '-c', child, str(fixture.bridge.native_socket),
                str(fixture.bridge.sidecar_socket), str(fixture.source)], input=json.dumps(request),
                text=True, capture_output=True, timeout=5)
            self.assertNotEqual(denied.returncode, 0)
        finally:
            fixture.tearDown()

    def test_local_qa_peer_probe_is_disposable_and_reaps_its_only_recorder(self):
        import hashlib
        import tempfile
        from types import SimpleNamespace
        from unittest.mock import patch
        from commander_gym import manual_runtime_qa as qa
        from commander_gym.qa_local_probes import recorder_peer, _as_role
        from test_manual_runtime_local import local_profile
        from test_game_server_binding_openai_sidecar import synthetic_catalog
        # A numerical UID fixture uses no account/group provisioning. Only this
        # disposable root-CI namespace receives fixture ownership/mode changes.
        with tempfile.TemporaryDirectory(prefix='qa-local-peer-', dir='/var/lib') as temp:
            base=Path(temp);base.chmod(0o755)
            profile=local_profile(base/'source')
            profile['identities']={'nativeUid':65534,'sidecarUid':65533,'proxyUid':65531}
            profile['localInference'].update(uid=65532,gid=65532)
            cloud=base/'cloud';cloud.mkdir();roster=synthetic_catalog(cloud)
            binding=cloud/'bindings/seat-a.json';data=json.loads(binding.read_text());data['binding_id']='cloud-seat';binding.write_text(json.dumps(data))
            data=json.loads(roster.read_text());data['active_bindings']=['cloud-seat'];roster.write_text(json.dumps(data))
            profile['artifacts']['catalog']={'root':str(cloud),'uid':0,'files':{
                str(p.relative_to(cloud)):hashlib.sha256(p.read_bytes()).hexdigest() for p in cloud.rglob('*') if p.is_file()}}
            profile['catalog']['roster']=str(roster.relative_to(cloud))
            nonce='a'*32;guest=base/'guest';fixture=guest/'qa/local-peer-fixture'/nonce;fixture.mkdir(parents=True)
            for folder,uid,gid,mode in [('recordings',65534,65534,0o700),('ipc',65534,65533,0o750),('local-ipc',65534,65532,0o750)]:
                path=fixture/folder;path.mkdir(mode=mode);os.chown(path,uid,gid)
            with patch.object(qa,'ROOT',guest),patch('commander_gym.qa_local_probes.pwd.getpwuid',side_effect=lambda uid:SimpleNamespace(pw_uid=uid,pw_gid=uid)):
                result=recorder_peer(profile,nonce)
            self.assertTrue(result['fixtureWorkerReaped']);self.assertFalse(result['activeRecordingTreeTouched'])
            self.assertEqual(result['cloudBindingScope'],'authenticated-rejection')
            self.assertEqual(result['localBindingScope'],'authenticated-rejection')
            self.assertTrue(result['duplicateReceiptsIdentical']);self.assertFalse(result['nativeTerminalFabricated'])
            self.assertFalse(Path(profile['paths']['recordingRoot']).exists())
            self.assertTrue((fixture/'recordings'/result['gameId']/'native-000000.ndjson').is_file())
            self.assertFalse(any((fixture/sub/name).exists() for sub,name in [('ipc','native.sock'),('ipc','sidecar.sock'),('local-ipc','local.sock')]))

    def test_local_uid_has_its_own_socket_and_cannot_enter_luna_socket(self):
        fixture = bridge_fixtures.RecorderBridgeTests()
        fixture.setUp()
        try: fixture.test_real_local_uid_can_only_connect_to_its_scoped_socket()
        finally: fixture.tearDown()
