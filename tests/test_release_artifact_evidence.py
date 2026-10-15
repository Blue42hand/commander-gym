import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
import zipfile

from commander_gym.manual_runtime_profile import ManualRuntimeError
from commander_gym.release_artifact_evidence import verify_archive


class ArtifactEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'artifact.zip'
        self.files = {'server.jar': b'synthetic server bytes', 'adapter.jar': b'synthetic adapter bytes',
                      'frontend/index.html': b'<html/>', 'canonical-v2-fixture.json': b'{}'}
        receipt = {'nativeSha': 'a' * 40, 'gymSha': 'b' * 40, 'deploymentAuthorized': False,
                   'providerCallsMade': 0, 'qaCallbackGatePackaged': True, 'qaRedisVariantPackaged': True,
                   'serverSha256': self.digest(self.files['server.jar']),
                   'adapterSha256': self.digest(self.files['adapter.jar'])}
        self.files['manual-luna-release.json'] = json.dumps(receipt).encode()
        self.manifest = {'schemaVersion': 1, 'engineSha': 'a' * 40, 'gymSha': 'b' * 40,
                         'deploymentAuthorized': False, 'privateHostBundleRequired': True,
                         'files': {name: {'sha256': self.digest(raw), 'bytes': len(raw)}
                                   for name, raw in self.files.items()}}
        self.candidate = {'schemaVersion': 1, 'state': 'source-evidence-collected', 'promotionEnabled': False,
                          'sources': {'engine': {'sha': 'a' * 40}, 'gym': {'sha': 'b' * 40}},
                          'matchedArtifact': {'id': 1, 'runId': 2, 'runAttempt': 1}}

    @staticmethod
    def digest(raw): return hashlib.sha256(raw).hexdigest()

    def write(self, extra=None):
        with zipfile.ZipFile(self.path, 'w') as archive:
            for name, raw in self.files.items(): archive.writestr(name, raw)
            archive.writestr('matched-release.json', json.dumps(self.manifest))
            if extra is not None: archive.writestr(*extra)
        raw = self.path.read_bytes()
        self.candidate['matchedArtifact'].update(bytes=len(raw), sha256=self.digest(raw))

    def test_complete_inventory_verified_without_extracting_or_approving(self):
        self.write(); result = verify_archive(self.path, self.candidate)
        self.assertEqual(result['state'], 'matched-artifact-bytes-verified')
        self.assertFalse(result['promotionEnabled'])
        self.assertEqual(list(Path(self.temp.name).iterdir()), [self.path])

    def test_wrong_download_digest_rejected(self):
        self.write(); self.candidate['matchedArtifact']['sha256'] = 'f' * 64
        with self.assertRaisesRegex(ManualRuntimeError, 'artifact_archive_digest'):
            verify_archive(self.path, self.candidate)

    def test_changed_file_and_wrong_source_rejected(self):
        self.files['server.jar'] = b'tampered bytes'; self.write()
        with self.assertRaises(ManualRuntimeError): verify_archive(self.path, self.candidate)
        self.setUp(); self.manifest['gymSha'] = 'c' * 40; self.write()
        with self.assertRaisesRegex(ManualRuntimeError, 'artifact_manifest_identity'):
            verify_archive(self.path, self.candidate)

    def test_unlisted_traversal_symlink_duplicate_rejected(self):
        link = zipfile.ZipInfo('link'); link.create_system = 3; link.external_attr = 0o120777 << 16
        for extra in [('unlisted', b'x'), ('../escape', b'x'), (link, b'server.jar'), ('server.jar', b'x')]:
            with self.subTest(extra=extra):
                self.write(extra)
                with self.assertRaises(ManualRuntimeError): verify_archive(self.path, self.candidate)

    def test_missing_files_and_false_authority_rejected(self):
        del self.files['adapter.jar']; del self.manifest['files']['adapter.jar']; self.write()
        with self.assertRaisesRegex(ManualRuntimeError, 'artifact_inventory'):
            verify_archive(self.path, self.candidate)
        self.setUp(); self.manifest['deploymentAuthorized'] = True; self.write()
        with self.assertRaisesRegex(ManualRuntimeError, 'artifact_manifest_identity'):
            verify_archive(self.path, self.candidate)

    def test_fifo_rejected_without_waiting_for_a_writer(self):
        fifo = Path(self.temp.name) / 'artifact.fifo'
        os.mkfifo(fifo)
        candidate = Path(self.temp.name) / 'candidate.json'
        candidate.write_text(json.dumps(self.candidate))
        result = subprocess.run([
            sys.executable, '-m', 'commander_gym.release_artifact_evidence',
            '--archive', str(fifo), '--candidate', str(candidate),
            '--candidate-sha256', self.digest(candidate.read_bytes())],
            capture_output=True, timeout=3, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)['reason'], 'artifact_archive_size')

    def test_archive_symlink_rejected(self):
        self.write(); link = Path(self.temp.name) / 'link.zip'; link.symlink_to(self.path)
        with self.assertRaisesRegex(ManualRuntimeError, 'artifact_symlink'):
            verify_archive(link, self.candidate)


if __name__ == '__main__': unittest.main()
