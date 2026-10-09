"""Single-owner composition and cleanup order with no socket or game."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from commander_gym.manual_runtime_profile import ValidatedRuntime, canonical, digest
from commander_gym.manual_runtime_recorder import run_recorder
from test_manual_runtime_profile import fixture_profile


class ManualRuntimeRecorderTests(unittest.TestCase):
    def test_same_capture_bridge_before_reporter_and_reverse_shutdown(self):
        with TemporaryDirectory(prefix='.recorder-qa-', dir=Path.cwd()) as tmp:
            profile=fixture_profile(Path(tmp)); runtime=ValidatedRuntime(canonical(profile),digest(profile),2)
            events=[]; capture=object()
            class Capture:
                def __init__(self,*args,**kwargs): events.append('capture'); self.marker=capture
                def close(self):events.append('capture-close')
            class Reporter:
                def __init__(self,owner,*args): self.owner=owner; events.append('reporter-compose')
                def start(self):events.append('reporter-start')
                def close(self):events.append('reporter-close')
            class Bridge:
                def __init__(self,owner,*args,**kwargs):
                    self.owner=owner; events.append('bridge-compose')
                    assert owner.marker is capture
                    assert 'allow_same_uid_fixture' not in kwargs
                    assert kwargs['native_socket'].name=='native.sock'
                    assert kwargs['sidecar_socket'].name=='sidecar.sock'
                    assert kwargs['native_uid'] != kwargs['sidecar_uid']
                def start(self):events.append('bridge-start')
                def close(self):events.append('bridge-close')
            run_recorder(runtime,sidecar_gid=983,stopping=lambda:True,capture_factory=Capture,
                         reporter_factory=Reporter,bridge_factory=Bridge,registry_factory=lambda *args:object())
            self.assertEqual(events.count('capture'),1)
            self.assertLess(events.index('bridge-start'),events.index('reporter-start'))
            self.assertEqual(events[-3:],['reporter-close','bridge-close','capture-close'])
