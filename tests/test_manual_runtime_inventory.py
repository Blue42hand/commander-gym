"""Real filesystem tests for the production recording stop barrier."""
from pathlib import Path
from tempfile import TemporaryDirectory
from contextlib import contextmanager
from types import SimpleNamespace
import hashlib
import os
import tracemalloc
import unittest
from unittest.mock import patch

from commander_gym import manual_runtime_quiescence as inventory
from commander_gym.manual_runtime_profile import ManualRuntimeError
from commander_gym.manual_runtime_host import ProtectedRuntimeHost


class CaptureInventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / 'native'
        self.root.mkdir(mode=0o700)
        self.file = self.root / 'original'
        self.file.write_bytes(b'original evidence')
        self.file.chmod(0o600)

    def capture(self, **kwargs):
        return inventory.capture_inventory(self.root, uid=os.getuid(), **kwargs)

    def test_large_recording_is_streamed_with_bounded_memory(self):
        size = 512 * 1024**2 + 1
        with self.file.open('wb') as f:
            f.truncate(size)
        expected = hashlib.sha256()
        block = bytes(1024**2)
        for _ in range(512):
            expected.update(block)
        expected.update(b'\0')
        tracemalloc.start()
        try:
            result = self.capture()
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(result, {'original': expected.hexdigest()})
        self.assertLess(peak, 8 * 1024**2)

    def test_file_and_total_limits_are_independent(self):
        with patch.object(inventory, 'MAX_CAPTURE_FILE_BYTES', 1):
            with self.assertRaisesRegex(ManualRuntimeError, 'capture_file_limit'): self.capture()
        with patch.object(inventory, 'MAX_CAPTURE_TOTAL_BYTES', 1):
            with self.assertRaisesRegex(ManualRuntimeError, 'capture_total_limit'): self.capture()

    def test_deadline_and_entry_limits(self):
        with patch.object(inventory, 'CAPTURE_TIMEOUT_SECONDS', 0):
            with self.assertRaisesRegex(ManualRuntimeError, 'capture_deadline'): self.capture()
        with patch.object(inventory, 'MAX_CAPTURE_ENTRIES', 0):
            with self.assertRaisesRegex(ManualRuntimeError, 'capture_entry_limit'): self.capture()

    def test_hardlink_and_symlink_rejected(self):
        link = self.root / 'link'
        os.link(self.file, link)
        with self.assertRaisesRegex(ManualRuntimeError, 'capture_special_file'): self.capture()
        link.unlink()
        link.symlink_to(self.file)
        with self.assertRaisesRegex(ManualRuntimeError, 'capture_special_file'): self.capture()

    def test_group_writable_ancestor_rejected_even_if_explicitly_named(self):
        self.base.chmod(0o770)
        with self.assertRaisesRegex(ManualRuntimeError, 'capture_ancestor_custody'):
            self.capture(ancestor_owners={self.base: os.getuid()})

    def test_policy_cannot_authorize_recording_root_or_descendants(self):
        for path in (self.root, self.file, Path('/')):
            with self.subTest(path=path), self.assertRaisesRegex(ManualRuntimeError, 'capture_ancestor_policy'):
                self.capture(ancestor_owners={path: os.getuid()})

    def test_symlink_ancestor_rejected(self):
        alias = self.base / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            inventory.capture_inventory(alias, uid=os.getuid())

    def mutate_during_read(self, mutation, error):
        original_read = os.read
        mutated = False
        def read(fd, size):
            nonlocal mutated
            result = original_read(fd, size)
            if result and not mutated:
                mutated = True
                mutation()
            return result
        with patch.object(inventory.os, 'read', side_effect=read):
            with self.assertRaisesRegex(ManualRuntimeError, error): self.capture()

    def test_growth_during_hash_rejected(self):
        def grow():
            with self.file.open('ab') as f: f.write(b'growth')
        self.mutate_during_read(grow, 'capture_file_changed')

    def test_replacement_with_identical_bytes_rejected(self):
        def replace():
            raw = self.file.read_bytes()
            self.file.rename(self.base / 'saved')
            self.file.write_bytes(raw)
            self.file.chmod(0o600)
        self.mutate_during_read(replace, 'capture_file_changed')

    def test_new_entry_during_hash_rejected(self):
        self.mutate_during_read(lambda: (self.root/'new').write_bytes(b'new'), 'capture_membership_changed')

    def test_ancestor_replacement_rejected(self):
        def replace():
            self.root.rename(self.base/'saved')
            self.root.mkdir(mode=0o700)
        self.mutate_during_read(replace, 'capture_directory_changed')

    @contextmanager
    def simulated_owners(self, ownership):
        original_stat, original_fstat = os.stat, os.fstat
        def changed(info):
            if info.st_ino not in ownership:
                return info
            fields = {key: getattr(info, key) for key in dir(info) if key.startswith('st_')}
            fields['st_uid'] = ownership[info.st_ino]
            return SimpleNamespace(**fields)
        with patch.object(inventory.os, 'stat', side_effect=lambda *a, **kw: changed(original_stat(*a, **kw))), \
             patch.object(inventory.os, 'fstat', side_effect=lambda *a, **kw: changed(original_fstat(*a, **kw))):
            yield

    def test_declared_ancestor_does_not_authorize_foreign_capture_owner(self):
        foreign = os.getuid() + 10000
        with self.simulated_owners({self.base.stat().st_ino: foreign}):
            with self.assertRaisesRegex(ManualRuntimeError, 'capture_ancestor_custody'):
                self.capture()
            self.assertEqual(self.capture(ancestor_owners={self.base: foreign}),
                             {'original': hashlib.sha256(b'original evidence').hexdigest()})
        with self.simulated_owners({self.file.stat().st_ino: foreign}):
            with self.assertRaisesRegex(ManualRuntimeError, 'capture_custody'):
                self.capture(ancestor_owners={self.base: os.getuid()})

    @unittest.skipIf(os.getuid() == 0, 'requires distinct native and root identities')
    def test_root_analysis_is_hashed_but_root_native_capture_is_rejected(self):
        game = self.root/'game'; game.mkdir(mode=0o700)
        analysis = game/'analysis'; analysis.mkdir(mode=0o700)
        report = analysis/'report.json'; report.write_bytes(b'root analysis'); report.chmod(0o600)
        lock = game/'.analysis.lock'; lock.touch(mode=0o600)
        with self.simulated_owners({p.stat().st_ino: 0 for p in (analysis, report, lock)}):
            captured = self.capture()
            self.assertEqual(captured['game/analysis/report.json'], hashlib.sha256(b'root analysis').hexdigest())
            self.assertEqual(captured['game/.analysis.lock'], hashlib.sha256(b'').hexdigest())
        with self.simulated_owners({self.file.stat().st_ino: 0}):
            with self.assertRaisesRegex(ManualRuntimeError, 'capture_custody'): self.capture()

    def test_host_supplies_only_the_two_declared_ancestor_owners(self):
        host = ProtectedRuntimeHost.__new__(ProtectedRuntimeHost)
        host.anchor = {'paths': {'recordingRoot': '/var/lib/commander-gym/runs/native'},
                       'identities': {'nativeUid': 982, 'sidecarUid': 999}}
        with patch('commander_gym.manual_runtime_host.capture_inventory', return_value={}) as call:
            host.recording_inventory()
        call.assert_called_once_with(Path('/var/lib/commander-gym/runs/native'), uid=982,
            ancestor_owners={Path('/var/lib/commander-gym'): 999, Path('/var/lib/commander-gym/runs'): 999})


if __name__ == '__main__': unittest.main()
