"""Keyless custody regressions; Linux systemd delivery is tested separately."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import shutil
import unittest
from unittest.mock import patch

from commander_gym.manual_runtime_credentials import credential
from commander_gym.manual_runtime_profile import ManualRuntimeError


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(prefix='.credential-qa-', dir=Path.cwd())
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.file = self.directory / 'fake.env'
        self.file.write_bytes(b'fake credential only')
        self.file.chmod(0o600)

    def read(self, **kwargs):
        return credential(self.directory, 'fake.env', uid=os.getuid(), limit=100, **kwargs)

    def test_private_file_and_shared_file_separation(self):
        self.assertEqual('fake credential only', self.read())
        for mode in (0o440, 0o640, 0o644, 0o660, 0o601):
            with self.subTest(mode=mode):
                self.file.chmod(mode)
                with self.assertRaisesRegex(ManualRuntimeError, 'credential_file_custody'):
                    self.read()

    def test_systemd_path_role_and_env_cannot_authorize_private_file(self):
        with patch.dict(os.environ, {'CREDENTIALS_DIRECTORY': str(self.directory)}):
            for role in ('sidecar', 'native', 'recorder', 'wrong'):
                with self.subTest(role=role), self.assertRaises(ManualRuntimeError):
                    self.read(role=role)
        for role, name in (('native', 'openai.env'), ('recorder', 'openai.env'),
                           ('recorder', 'commander-gym.sidecar.token')):
            with self.subTest(role=role, name=name), self.assertRaisesRegex(ManualRuntimeError, 'credential_role'):
                credential(Path('/run/credentials/argentum-play.service'), name,
                           uid=os.getuid(), limit=100, role=role)

    def test_symlink_ancestor_shared_directory_hardlink_fifo(self):
        link = self.directory / 'link'
        link.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaises(ManualRuntimeError):
            credential(link, self.file.name, uid=os.getuid(), limit=100)
        self.directory.chmod(0o777)
        with self.assertRaisesRegex(ManualRuntimeError, 'credential_directory_custody'):
            self.read()
        self.directory.chmod(0o700)
        other = self.directory / 'other'
        os.link(self.file, other)
        with self.assertRaisesRegex(ManualRuntimeError, 'credential_file_custody'):
            self.read()
        other.unlink()
        self.file.unlink()
        self.file.symlink_to(other)
        with self.assertRaises(ManualRuntimeError):
            self.read()
        self.file.unlink()
        os.mkfifo(self.file, 0o600)
        with self.assertRaisesRegex(ManualRuntimeError, 'credential_file_custody'):
            self.read()

    def test_size_encoding_and_secret_free_errors(self):
        self.file.write_bytes(b'x' * 101)
        with self.assertRaises(ManualRuntimeError):
            self.read()
        self.file.write_bytes(b'FAKE_SECRET\xff')
        with self.assertRaisesRegex(ManualRuntimeError, '^credential_encoding$') as caught:
            self.read()
        self.assertNotIn('FAKE_SECRET', repr(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)

    def test_descriptor_replacement_and_mode_change_during_read(self):
        original = os.read
        for mutation in ('replace', 'mode', 'parent', 'ancestor-replace'):
            self.file.write_bytes(b'fake credential only')
            self.file.chmod(0o600)
            done = False
            def racing_read(fd, size):
                nonlocal done
                raw = original(fd, size)
                if not done:
                    done = True
                    if mutation == 'replace':
                        replacement = self.directory / 'new'
                        replacement.write_bytes(b'different fake')
                        replacement.chmod(0o600)
                        replacement.replace(self.file)
                    elif mutation == 'mode':
                        self.file.chmod(0o644)
                    elif mutation == 'parent':
                        self.directory.chmod(0o755)
                    else:
                        moved = self.directory.with_name(self.directory.name + '-moved')
                        self.directory.rename(moved)
                        self.directory.mkdir(mode=0o700)
                        self.addCleanup(shutil.rmtree, moved)
                return raw
            with self.subTest(mutation=mutation), patch('os.read', racing_read):
                with self.assertRaisesRegex(ManualRuntimeError, 'credential_(changed|directory_changed)'):
                    self.read()
            self.directory.chmod(0o700)


if __name__ == '__main__':
    unittest.main()
