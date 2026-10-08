"""Synthetic root attestations and real existing-file writer ownership checks."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from commander_gym import historical_recording as historical
from commander_gym.game_journal import JournalError
from commander_gym.native_game_capture import NativeGameCapture
from tests import test_native_game_capture as fixtures


def encoded(value):
    return json.dumps(value, sort_keys=True).encode() + b'\n'


class HistoricalRecordingTests(unittest.TestCase):
    add = fixtures.NativeGameCaptureTests.add

    def setUp(self):
        fixtures.NativeGameCaptureTests.setUp(self)
        self.capture.close()
        for name in historical.LOCKS:
            path = self.game / name
            path.touch(mode=0o600)
        gap = self.game / 'native-gap-synthetic.json'
        gap.write_bytes(b'{"recordingComplete":false,"reason":"synthetic-stop"}\n')
        gap.chmod(0o600)
        self.external = tempfile.TemporaryDirectory()
        self.ack = Path(self.external.name) / 'registry.json'
        self.receipt = Path(self.external.name) / 'stop.json'
        self.publish()
        # Only these synthetic issuer files replace the root-custody read in unit
        # tests; production has no injectable authority/UID bypass.
        def read(path): return path.read_bytes(), historical.identity(path.lstat())
        def metadata(path): return historical.identity(path.lstat())
        self.read_patch = patch.object(historical, '_root_private_read', side_effect=read)
        self.meta_patch = patch.object(historical, '_root_private_identity', side_effect=metadata)
        self.read_patch.start(); self.meta_patch.start()
        self.capture = self.create()

    def create(self):
        return NativeGameCapture(self.root, self.pins, acknowledged_incomplete_registry=self.ack)

    def publish(self):
        files = {}
        directories = {'': historical.identity(self.game.lstat())}
        for path in self.game.rglob('*'):
            info = path.lstat(); name = path.relative_to(self.game).as_posix()
            if path.is_dir(): directories[name] = historical.identity(info)
            else:
                files[name] = {**historical.identity(info), 'size': info.st_size,
                    'mtime_ns': info.st_mtime_ns, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        receipt_files = {name: {key: meta[key] for key in ('size', 'sha256', 'mtime_ns')}
                         for name, meta in files.items()}
        receipt = dict(capture_id=self.game.name, classification=historical.CLASSIFICATION,
                       recording_complete=False, winner=None, source_files_unchanged=True,
                       files_before=receipt_files, files_after=receipt_files)
        self.receipt.write_bytes(encoded(receipt)); self.receipt.chmod(0o600)
        self.entry = dict(captureId=self.game.name, canonicalPath=str(self.game),
            classification=historical.CLASSIFICATION, recordingComplete=False,
            stopReceipt=dict(path=str(self.receipt), sha256=hashlib.sha256(self.receipt.read_bytes()).hexdigest()),
            files=files, directories=directories,
            gapMarkers={name: meta['sha256'] for name, meta in files.items() if name.startswith('native-gap-')})
        self.registry = dict(schemaVersion=1, kind=historical.KIND, captures=[self.entry])
        self.ack.write_bytes(encoded(self.registry)); self.ack.chmod(0o600)

    def tearDown(self):
        self.capture.close()
        self.read_patch.stop(); self.meta_patch.stop()
        self.temp.cleanup(); self.external.cleanup()

    def original_bytes(self):
        # Do not reopen native lock while POSIX ownership is held in this process.
        return {p.name: p.read_bytes() for p in self.game.iterdir() if p.name not in historical.LOCKS}

    def assert_held_incomplete(self):
        self.capture.scan()
        metrics = self.capture.operational_metrics()
        self.assertTrue(metrics['healthy'])
        self.assertEqual(metrics['pendingRecordWrites'], 0)
        self.assertEqual(metrics['acknowledgedIncomplete'], 1)
        self.assertEqual(self.capture.health()['acknowledged_incomplete'], 1)
        self.assertFalse((self.game / 'manifest.json').exists())
        self.assertFalse(self.capture._journals)

    def test_verified_locked_capture_is_excluded_without_any_source_mutation(self):
        before = self.original_bytes()
        self.assert_held_incomplete()
        self.capture.scan()
        self.assertEqual(before, self.original_bytes())
        with self.assertRaises(JournalError): self.capture.seat_sink(self.game.name, 'seat-a')
        with self.assertRaises(JournalError): self.capture._journal(self.game.name, [])

    def test_unacknowledged_orphan_still_blocks_and_is_imported_normally(self):
        self.capture.close()
        self.capture = NativeGameCapture(self.root, self.pins)
        self.capture.scan()
        metrics = self.capture.operational_metrics()
        self.assertGreater(metrics['pendingRecordWrites'], 0)
        self.assertEqual(metrics['acknowledgedIncomplete'], 0)
        self.assertIn(self.game.name, self.capture._journals)

    def test_acknowledged_capture_does_not_hide_another_unacknowledged_orphan(self):
        other = self.root / 'another-orphan'; other.mkdir(mode=0o700)
        source = other / 'native-000000.ndjson'; source.write_bytes(b'pending-tail'); source.chmod(0o600)
        self.capture.scan()
        metrics = self.capture.operational_metrics()
        self.assertEqual(metrics['acknowledgedIncomplete'], 1)
        self.assertGreater(metrics['pendingRecordWrites'], 0)

    def test_changed_source_same_size_and_restored_mtime_is_rejected(self):
        self.assert_held_incomplete()
        before = self.source.stat()
        data = self.source.read_bytes()
        self.source.write_bytes(data.replace(b'synthetic-clock', b'tampered--clock', 1))
        os.utime(self.source, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.capture.scan()
        metrics = self.capture.operational_metrics()
        self.assertFalse(metrics['healthy'])
        self.assertEqual(metrics['acknowledgedIncomplete'], 0)
        self.assertGreater(metrics['pendingRecordWrites'], 0)
        self.assertFalse(self.capture._journals)

    def test_extra_member_and_symlink_are_rejected_before_import(self):
        for symlink in (False, True):
            with self.subTest(symlink=symlink):
                extra = self.game / 'new-member.json'
                if symlink: extra.symlink_to(self.source)
                else: extra.touch(mode=0o600)
                self.capture.scan()
                self.assertIn('historical_acknowledgements', self.capture.errors)
                self.assertFalse(self.capture._journals)
                extra.unlink()

    def test_missing_existing_writer_lock_is_never_created(self):
        path = self.game / '.native-writer.lock'; path.unlink()
        self.capture.scan()
        self.assertFalse(path.exists())
        self.assertFalse(self.capture._journals)
        self.assertEqual(self.capture.operational_metrics()['acknowledgedIncomplete'], 0)

    def test_replaced_writer_inode_with_identical_bytes_and_mtime_is_rejected(self):
        before = (self.game / '.native-writer.lock').stat()
        old = self.game / '.native-writer.lock'
        replacement = self.game / 'replacement'
        replacement.touch(mode=0o600)
        os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
        os.replace(replacement, old)
        self.capture.scan()
        self.assertFalse(self.capture._journals)
        self.assertEqual(self.capture.operational_metrics()['acknowledgedIncomplete'], 0)

    def test_registry_or_stop_receipt_tamper_rejects_all_import(self):
        for path in (self.ack, self.receipt):
            with self.subTest(path=path.name):
                self.capture.close(); self.publish(); self.capture = self.create()
                self.assert_held_incomplete()
                path.write_bytes(path.read_bytes()+b' ')
                self.capture.scan()
                self.assertFalse(self.capture.operational_metrics()['healthy'])
                self.assertEqual(self.capture.health()['acknowledged_incomplete'], 0)
                self.assertFalse(self.capture._journals)

    def test_wrong_receipt_hash_or_inventory_is_not_a_bool_only_skip(self):
        self.capture.close()
        for key in ('sha256', 'files_before'):
            with self.subTest(key=key):
                self.publish()
                if key == 'sha256': self.registry['captures'][0]['stopReceipt']['sha256'] = '0'*64
                else:
                    receipt = json.loads(self.receipt.read_bytes()); receipt['files_before'] = {}
                    self.receipt.write_bytes(encoded(receipt))
                    self.registry['captures'][0]['stopReceipt']['sha256'] = hashlib.sha256(self.receipt.read_bytes()).hexdigest()
                self.ack.write_bytes(encoded(self.registry)); self.capture = self.create()
                self.capture.scan()
                self.assertFalse(self.capture._journals)
                self.assertFalse(self.capture.operational_metrics()['healthy'])
                self.capture.close()

    def test_invalid_ack_blocks_import_of_other_valid_live_capture(self):
        other = self.root / 'unacknowledged-new'
        other.mkdir(mode=0o700)
        original = self.game, self.source, self.sequence, self.previous
        try:
            self.game = other; self.source = other / 'native-000000.ndjson'
            self.source.touch(mode=0o600); self.sequence = 0; self.previous = '0'*64
            self.add('initialization', {'setup': {'seed': 1, 'players': []}, 'pinnedCards': [], 'events': []})
        finally:
            self.game, self.source, self.sequence, self.previous = original
        self.ack.write_bytes(self.ack.read_bytes()+b' ')
        self.capture.scan()
        self.assertFalse(self.capture._journals)
        self.assertFalse((other / '000000.jsonl').exists())
        self.assertFalse(self.capture.operational_metrics()['healthy'])

    def test_capture_directory_replacement_is_rejected(self):
        self.assert_held_incomplete()
        old = self.root / 'retained-original'
        self.game.rename(old)
        self.game.mkdir(mode=0o700)
        for name in ('native-000000.ndjson', *historical.LOCKS):
            path = self.game / name; path.touch(mode=0o600)
        self.capture.scan()
        self.assertFalse(self.capture.operational_metrics()['healthy'])
        self.assertFalse(self.capture._journals)

    def test_receipt_classification_complete_winner_or_preservation_mismatch_rejected(self):
        self.capture.close()
        for key, value in (('classification', 'completed'), ('recording_complete', True),
                           ('winner', 'invented-seat'), ('source_files_unchanged', False)):
            with self.subTest(key=key):
                self.publish()
                receipt = json.loads(self.receipt.read_bytes()); receipt[key] = value
                self.receipt.write_bytes(encoded(receipt))
                self.registry['captures'][0]['stopReceipt']['sha256'] = hashlib.sha256(self.receipt.read_bytes()).hexdigest()
                self.ack.write_bytes(encoded(self.registry)); self.capture = self.create()
                self.capture.scan()
                self.assertFalse(self.capture._journals)
                self.assertFalse(self.capture.operational_metrics()['healthy'])
                self.capture.close()

    def test_competing_gym_owner_rejected_without_opening_native_lock(self):
        fd = os.open(self.game / '.writer.lock', os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.capture.scan()
            self.assertFalse(self.capture._journals)
            self.assertFalse(self.capture.operational_metrics()['healthy'])
        finally: os.close(fd)

    def test_second_registry_in_same_process_cannot_release_first_native_lock(self):
        self.assert_held_incomplete()
        second = self.create()
        try:
            second.scan()
            self.assertFalse(second.operational_metrics()['healthy'])
            self.assert_held_incomplete()
        finally: second.close()


class RootCustodyTests(unittest.TestCase):
    def test_untrusted_private_file_is_not_root_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'registry.json'; path.write_bytes(b'{}'); path.chmod(0o600)
            if os.getuid() == 0:
                path.chmod(0o666)
            with self.assertRaises(JournalError): historical._root_private_read(path)


class JavaInteropTests(HistoricalRecordingTests):
    @classmethod
    def setUpClass(cls):
        cls.compiler = shutil.which('javac'); cls.java = shutil.which('java')
        if not cls.compiler or not cls.java: raise unittest.SkipTest('local JDK required for actual Java FileLock interoperability')
        cls.compiled = tempfile.TemporaryDirectory()
        source = Path(__file__).parent / 'fixtures/HistoricalLockProbe.java'
        result = subprocess.run([cls.compiler, '-d', cls.compiled.name, str(source)], capture_output=True, timeout=20)
        if result.returncode: cls.compiled.cleanup(); raise unittest.SkipTest('available javac cannot compile offline lock fixture')

    @classmethod
    def tearDownClass(cls): cls.compiled.cleanup()

    def java_probe(self, mode):
        return subprocess.Popen([self.java, '-cp', self.compiled.name, 'HistoricalLockProbe',
            str(self.game / '.native-writer.lock'), mode], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)

    def test_java_writer_prevents_acknowledgement(self):
        process = self.java_probe('hold')
        try:
            self.assertEqual(process.stdout.readline().strip(), 'LOCKED')
            self.capture.scan()
            self.assertFalse(self.capture.operational_metrics()['healthy'])
            self.assertFalse(self.capture._journals)
        finally:
            process.communicate('\n', timeout=5)
        self.assert_held_incomplete()

    def test_failed_second_registry_preserves_first_process_native_lock(self):
        self.assert_held_incomplete()
        second = self.create()
        try:
            second.scan()
            self.assertFalse(second.operational_metrics()['healthy'])
            process = self.java_probe('probe')
            output, errors = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0, errors)
            self.assertEqual(output.strip(), 'BUSY')
        finally: second.close()

    def test_python_held_native_lock_excludes_java_across_hashing_rechecks_and_close(self):
        self.assert_held_incomplete()
        for _ in range(2):
            process = self.java_probe('probe')
            output, errors = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0, errors)
            self.assertEqual(output.strip(), 'BUSY')
            self.capture.scan(); self.capture.operational_metrics()
        self.capture.close()
        process = self.java_probe('probe'); output, errors = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, errors)
        self.assertEqual(output.strip(), 'LOCKED')


if __name__ == '__main__': unittest.main()
