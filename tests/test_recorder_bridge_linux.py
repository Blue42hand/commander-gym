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
