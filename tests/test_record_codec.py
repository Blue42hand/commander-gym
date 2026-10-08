import base64
import gzip
import hashlib
import io
import json
from pathlib import Path
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch

from commander_gym.record_codec import encode_record, decode_record, physical_lines, RecordCodecError, MAX_RECORD_BYTES
from commander_gym.game_journal import (PIN_KEYS, PrivateGameJournal, JournalError, inspect_journal,
    publish_finalized_manifest, verify_finalized_manifest, iter_projection_artifact, seat_projection, _json)
from commander_gym.native_game_capture import read_native_source, NativeCursor


class RecordCodecTests(unittest.TestCase):
    def test_python_and_actual_jdk21_golden_frames_recover_identical_unicode_bytes(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures/record-codec-v1.json').read_text())
        logical = base64.b64decode(fixture['logicalUtf8Base64'])
        for key in ('pythonFrameBase64', 'jvmFrameBase64'):
            self.assertEqual(decode_record(base64.b64decode(fixture[key])), logical)
        self.assertEqual(decode_record(encode_record(logical)), logical)
        self.assertLess(len(encode_record(logical)), len(logical) // 10)
        self.assertEqual(encode_record(b'{"synthetic":1}\n'), b'{"synthetic":1}\n')

    def test_frames_reject_bad_versions_lengths_checksums_members_and_trailing_bytes(self):
        logical = _json({'text': 'synthetic-語🙂' * 1000}) + b'\n'
        frame = json.loads(encode_record(logical))
        compressed = base64.b64decode(frame['data'])
        cases = [dict(frame, recordCodec=True), dict(frame, recordCodec=2), dict(frame, encoding='unknown'),
                 dict(frame, decodedBytes=True), dict(frame, decodedBytes=str(len(logical))),
                 dict(frame, decodedBytes=MAX_RECORD_BYTES + 1), dict(frame, extra=1),
                 dict(frame, decodedBytes=len(logical) - 1), dict(frame, data=frame['data'] + '\n')]
        damaged = bytearray(compressed); damaged[-8] ^= 1
        for data in (bytes(damaged), compressed[:-1], compressed + compressed, compressed + b'tail'):
            cases.append(dict(frame, data=base64.b64encode(data).decode()))
        for value in cases:
            with self.subTest(value={k: v for k, v in value.items() if k != 'data'}):
                with self.assertRaises(ValueError): decode_record(_json(value) + b'\n')

    def test_decode_bomb_and_physical_read_are_bounded_before_expansion(self):
        compressed = gzip.compress(b'x' * (8 * 1024 * 1024), mtime=0)
        frame = {'recordCodec': 1, 'encoding': 'gzip-base64', 'decodedBytes': 64,
                 'data': base64.b64encode(compressed).decode()}
        tracemalloc.start()
        try:
            with self.assertRaises(RecordCodecError): decode_record(_json(frame) + b'\n')
            _, peak = tracemalloc.get_traced_memory()
        finally: tracemalloc.stop()
        self.assertLess(peak, 1024 * 1024)
        with patch('commander_gym.record_codec.MAX_RECORD_BYTES', 1024):
            with self.assertRaises(RecordCodecError): list(physical_lines(io.BytesIO(b'x' * 4096)))

    def test_physical_caps_rotation_legacy_mixed_resume_and_hashes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'game'
            pins = dict.fromkeys(PIN_KEYS)
            with PrivateGameJournal(root, 'g', pins, compress_records=False) as w:
                w.append('coverage_gap', {'detail': 'legacy'})
            prefix = (root / '000000.jsonl').read_bytes()
            with PrivateGameJournal(root, 'g', pins, max_bytes=6000, terminal_reserve=1500, segment_bytes=1600) as w:
                w.append('coverage_gap', {'detail': 'synthetic-large-state' * 20000})
                self.assertLess(w.used, 4500)
                w.finish({'kind': 'operator_stop'}, expected_sources={}, gaps=['synthetic'])
            self.assertTrue((root / '000000.jsonl').read_bytes().startswith(prefix))
            report = inspect_journal(root)
            self.assertTrue(report['closed'])
            self.assertEqual(len(report['rows']), 5)
            self.assertLess(sum(p.stat().st_size for p in root.glob('*.jsonl')), 6000)
            self.assertEqual(verify_finalized_manifest(root)['journal_root_sha256'], report['root_sha256'])
            self.assertEqual(publish_finalized_manifest(root), verify_finalized_manifest(root))

    def test_partial_and_corrupt_compressed_frames_preserve_source_and_refuse_publication(self):
        for damage in ('partial', 'checksum'):
            with self.subTest(damage=damage), tempfile.TemporaryDirectory() as temp:
                root = Path(temp) / 'game'
                with PrivateGameJournal(root, 'g', dict.fromkeys(PIN_KEYS)) as w:
                    w.append('coverage_gap', {'detail': 'synthetic-state' * 1000})
                path = root / '000000.jsonl'
                lines = path.read_bytes().splitlines(keepends=True)
                if damage == 'partial': lines[-1] = lines[-1][:-4]
                else:
                    value = json.loads(lines[-1]); value['decodedBytes'] += 1; lines[-1] = _json(value) + b'\n'
                path.write_bytes(b''.join(lines)); before = path.read_bytes()
                self.assertFalse(inspect_journal(root)['integrity_ok'])
                with self.assertRaises(JournalError): publish_finalized_manifest(root)
                with self.assertRaises(JournalError): PrivateGameJournal(root, 'g', dict.fromkeys(PIN_KEYS))
                self.assertEqual(path.read_bytes(), before)
                self.assertFalse((root / 'manifest.json').exists())

    def test_streaming_history_finalization_projection_and_discovery_have_bounded_peak_memory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'game'
            payload = 'synthetic-own-state-' * 55000  # about 1 MiB per logical row
            with PrivateGameJournal(root, 'g', dict.fromkeys(PIN_KEYS)) as w:
                for i in range(96): w.append('coverage_gap', {'i': i, 'data': payload})
                for i in range(8):
                    w.append('native_seat_observation', {'native': {'visibility': 'seat', 'seatId': 'a', 'gameId': w.game_id},
                        'own': payload}, seat_id='a', source='native', source_sequence=i)
                tracemalloc.start()
                try:
                    report = inspect_journal(root)
                    self.assertEqual(len(report['rows']), 105)
                    self.assertEqual(sum(1 for _ in report['rows']), 105)
                    w.finish({'kind': 'operator_stop'}, expected_sources={'native': 8}, gaps=['synthetic'])
                    manifest = verify_finalized_manifest(root)
                    projection = next(a for a in manifest['artifacts'] if a['role'] == 'derived_seat_projection')
                    self.assertEqual(projection['encoding'], 'gzip-json-array-v1')
                    self.assertEqual(sum(1 for _ in iter_projection_artifact(root / projection['path'])), 8)
                    _, peak = tracemalloc.get_traced_memory()
                finally: tracemalloc.stop()
            self.assertLess(peak, 28 * 1024 * 1024)
            self.assertLess(manifest['storage_bytes'], 2 * 1024 * 1024)
            for path in root.iterdir(): self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_projection_output_exact_legacy_bytes_and_own_seat_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'game'
            with PrivateGameJournal(root, 'g', dict.fromkeys(PIN_KEYS)) as w:
                for seat in ('a', 'b'):
                    w.append('native_seat_observation', {'native': {'visibility': 'seat', 'seatId': seat, 'gameId': w.game_id},
                        'ownHand': 'synthetic-' + seat}, seat_id=seat, source='native', source_sequence=w.sources.get('native', 0))
                w.append('coverage_gap', {'referee': 'synthetic-admin-only'})
                w.finish({'kind': 'operator_stop'}, expected_sources={'native': 2}, gaps=['synthetic'])
            report = inspect_journal(root); manifest = verify_finalized_manifest(root)
            for item in manifest['artifacts']:
                if item['role'] != 'derived_seat_projection': continue
                path = root / item['path']; expected = seat_projection(report, item['seat_id'])
                self.assertEqual(gzip.decompress(path.read_bytes()), _json(expected) + b'\n')
                self.assertEqual(list(iter_projection_artifact(path)), expected)
                self.assertNotIn('synthetic-admin-only', json.dumps(expected))
                other = 'b' if item['seat_id'] == 'a' else 'a'
                self.assertNotIn('synthetic-' + other, json.dumps(expected))

    def test_near_native_limit_wrapped_seat_record_fits_gym_boundary(self):
        # Scale the same producer/importer ratio to keep this regression inexpensive.
        from commander_gym.native_game_capture import NativeGameCapture
        native_limit = 8192
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); root.chmod(0o700)
            game = root / 'synthetic'; game.mkdir(mode=0o700)
            previous = '0' * 64
            source = game / 'native-000000.ndjson'
            with source.open('wb') as stream:
                for sequence, (kind, visibility, payload) in enumerate([
                    ('initialization', 'admin', {'setup': {'players': [], 'seed': 42}, 'pinnedCards': []}),
                    ('seat_observation', 'seat', {'own': 'synthetic-' * 680})], 1):
                    body = dict(schemaVersion=1, gameId=game.name, sequence=sequence,
                        previousSha256=previous, engineRevision='a'*40, kind=kind,
                        visibility=visibility, payload=payload)
                    if visibility == 'seat': body['seatId'] = 'a'
                    exact = json.dumps(body, separators=(',', ':'))
                    previous = hashlib.sha256(exact.encode()).hexdigest()
                    logical = _json({'body': exact, 'sha256': previous}) + b'\n'
                    self.assertLess(len(logical), native_limit)
                    stream.write(encode_record(logical))
            source.chmod(0o600)
            with patch('commander_gym.record_codec.MAX_RECORD_BYTES', 4 * native_limit), \
                 patch('commander_gym.native_game_capture.NATIVE_MAX_RECORD_BYTES', native_limit):
                capture = NativeGameCapture(root, {**dict.fromkeys(PIN_KEYS), 'gym': 'b'*40})
                try:
                    capture.scan()
                    report = inspect_journal(game)
                    self.assertTrue(report['integrity_ok'])
                    row = report['rows'][-1]
                    self.assertGreater(len(_json(row)), native_limit)
                    self.assertEqual(row['payload']['native']['payload']['own'], 'synthetic-' * 680)
                    snapshot = read_native_source(game)
                    # A later live tail is outside the verified snapshot, even if oversized.
                    with source.open('ab') as stream: stream.write(b'x' * (native_limit + 1))
                    self.assertEqual(len(list(snapshot)), 2)
                finally: capture.close()

    def test_oversized_legacy_record_is_explicitly_rejected_without_modifying_it(self):
        logical = b'{"legacy":"' + b'x' * 2000 + b'"}\n'
        with patch('commander_gym.record_codec.MAX_RECORD_BYTES', 1024):
            with self.assertRaisesRegex(RecordCodecError, 'record_size_or_framing_limit'):
                decode_record(logical)
        self.assertTrue(logical.endswith(b'"}\n'))

    def test_compressed_choice_reader_preserves_native_join_and_hidden_isolation(self):
        from tests.test_captured_choices import CapturedChoicesTests
        case = CapturedChoicesTests('test_accepted_join_returns_existing_canonical_record_with_provenance_and_no_admin_state')
        try:
            game = case.fixture(compressed_source=True, large_observation=True)
            from commander_gym.captured_choices import accepted_choice_records
            records, diagnostics = accepted_choice_records(game)
            self.assertEqual(len(records), 1)
            record = records[0].to_dict()
            self.assertNotIn('synthetic-admin-secret', json.dumps(record))
            self.assertEqual(diagnostics, {})
            cursor = NativeCursor(); rows = read_native_source(game, cursor)
            self.assertTrue(cursor.terminal)
            self.assertEqual(cursor.offset, (game / 'native-000000.ndjson').stat().st_size)
            self.assertEqual(len(rows), 5)
        finally: case.doCleanups()
