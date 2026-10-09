"""Real isolated systemd dependency/credential ordering; only ephemeral QA units.

Must run in a root Linux CI runner. No Tolaria route, host user, provider, network,
original capture, production unit, persistent key or activation profile is used.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
import uuid


class ManualRuntimeSystemdTests(unittest.TestCase):
    def setUp(self):
        available = sys.platform=='linux' and os.geteuid()==0 and Path('/run/systemd/system').is_dir() and shutil.which('systemctl')
        if not available:
            if os.environ.get('REQUIRE_MANUAL_SYSTEMD_TESTS')=='1':self.fail('root Linux systemd QA required')
            self.skipTest('isolated systemd CI only')
        self.tmp=TemporaryDirectory(prefix='manual-runtime-qa-',dir='/run')
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.prefix='manual-runtime-qa-'+uuid.uuid4().hex
        self.units=[self.prefix+'-'+role+'.service' for role in ('preflight','recorder','sidecar')]
        self.addCleanup(self.cleanup_units)
        (self.root/'qa-provider.txt').write_text('qa-placeholder-never-real')
        (self.root/'qa-provider.txt').chmod(0o600)

    def systemctl(self,*args,check=True):
        return subprocess.run(['systemctl',*args],check=check,capture_output=True,text=True,timeout=30)

    def cleanup_units(self):
        self.systemctl('stop',*reversed(self.units),check=False)
        for unit in self.units:
            Path('/run/systemd/system',unit).unlink(missing_ok=True)
        self.systemctl('daemon-reload',check=False)
        self.systemctl('reset-failed',*self.units,check=False)

    def install(self, *, preflight_ok):
        preflight, recorder, sidecar=self.units
        gate=self.root/'gate'
        preflight_command=f'/usr/bin/touch {gate}' if preflight_ok else '/usr/bin/false'
        texts={preflight:f'''[Unit]
Before={recorder} {sidecar}
[Service]
Type=oneshot
ExecStart={preflight_command}
''', recorder:f'''[Unit]
ConditionPathExists={gate}
Requires={preflight}
After={preflight}
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/touch {self.root}/recorder-started
''', sidecar:f'''[Unit]
ConditionPathExists={gate}
Requires={preflight} {recorder}
After={preflight} {recorder}
[Service]
Type=oneshot
RemainAfterExit=yes
LoadCredential=qa-provider.txt:{self.root}/qa-provider.txt
ExecStart=/usr/bin/touch {self.root}/sidecar-started
'''}
        for unit,text in texts.items():Path('/run/systemd/system',unit).write_text(text)
        self.systemctl('daemon-reload')

    def test_cold_boot_dependency_creates_gate_before_conditions(self):
        self.install(preflight_ok=True)
        self.assertFalse((self.root/'gate').exists())
        self.systemctl('start',self.units[2])
        self.assertTrue((self.root/'recorder-started').exists())
        self.assertTrue((self.root/'sidecar-started').exists())
        self.assertEqual(Path('/run/credentials',self.units[2],'qa-provider.txt').read_text(),'qa-placeholder-never-real')

    def test_failed_preflight_withholds_sidecar_credentials_even_with_stale_gate(self):
        self.install(preflight_ok=False)
        (self.root/'gate').write_text('qa-stale-gate')
        result=self.systemctl('start',self.units[2],check=False)
        self.assertNotEqual(result.returncode,0)
        self.assertFalse((self.root/'sidecar-started').exists())
        self.assertFalse(Path('/run/credentials',self.units[2]).exists())

    def test_promotion_existing_gate_does_not_depend_on_another_lock(self):
        self.install(preflight_ok=True)
        (self.root/'gate').write_text('qa-root-attested-promotion')
        self.systemctl('start',self.units[2])
        self.assertTrue((self.root/'sidecar-started').exists())
