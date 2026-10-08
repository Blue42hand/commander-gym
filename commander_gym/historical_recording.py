"""Root-attested historical incomplete captures, held outside live import.

No attestation is created here. Source files, lock contents, outcomes and manifests
are never changed. POSIX native locks and Gym flock ownership last until close.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from .game_journal import JournalError
from .record_codec import check_cancelled

KIND = 'commander-gym.acknowledged-incomplete-registry'
CLASSIFICATION = 'operator-stopped/incomplete'
LOCKS = ('.writer.lock', '.native-writer.lock')
IDENTITY_KEYS = {'device', 'inode', 'ctime_ns', 'uid', 'mode'}
FILE_KEYS = IDENTITY_KEYS | {'size', 'sha256', 'mtime_ns'}


def identity(info):
    return dict(device=info.st_dev, inode=info.st_ino, ctime_ns=info.st_ctime_ns,
                uid=info.st_uid, mode=stat.S_IMODE(info.st_mode))


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise JournalError('duplicate historical attestation key')
        result[key] = value
    return result


def _root_private_identity(path: Path):
    if not path.is_absolute(): raise JournalError('historical attestation path must be absolute')
    for item in (path, *path.parents):
        info = item.lstat()
        kind = stat.S_ISREG if item == path else stat.S_ISDIR
        if not kind(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise JournalError('historical attestation custody failure')
    info = path.lstat()
    if stat.S_IMODE(info.st_mode) not in (0o600, 0o640):
        raise JournalError('historical attestation must be root private')
    return identity(info)


def _root_private_read(path: Path):
    """Root custody, group-readable private metadata; no authority from caller UID."""
    before = _root_private_identity(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or identity(info) != before:
            raise JournalError('historical attestation changed during open')
        data = source.read(4 * 1024 * 1024 + 1)
        if len(data) > 4 * 1024 * 1024 or _root_private_identity(path) != before:
            raise JournalError('historical attestation changed or oversized')
    return data, before


def _relative(name):
    path = Path(name)
    if (not isinstance(name, str) or not name or path.is_absolute() or
            path.as_posix() != name or any(part in ('.', '..') for part in path.parts)):
        raise JournalError('invalid historical member path')
    return path


def _digest(fd, cancel):
    digest, offset = hashlib.sha256(), 0
    while True:
        check_cancelled(cancel)
        chunk = os.pread(fd, 1024 * 1024, offset)
        if not chunk: return digest.hexdigest()
        digest.update(chunk); offset += len(chunk)


class HistoricalIncompleteRegistry:
    """Optional immutable root-issued registry, with fail-closed held membership."""
    def __init__(self, root: Path, path: Path, *, cancel=None):
        self.root, self.path, self.cancel = root.absolute(), Path(path), cancel
        if self.path == self.root or self.root in self.path.parents:
            raise JournalError('historical registry must be outside source tree')
        self.entries = {}
        self.held = {}
        self._metadata = {}
        raw, info = _root_private_read(self.path)
        value = json.loads(raw, object_pairs_hook=_unique)
        if (not isinstance(value, dict) or set(value) != {'schemaVersion', 'kind', 'captures'} or
                type(value['schemaVersion']) is not int or value['schemaVersion'] != 1 or
                value['kind'] != KIND or not isinstance(value['captures'], list)):
            raise JournalError('invalid historical registry schema')
        self.registry_sha256 = hashlib.sha256(raw).hexdigest()
        self._metadata[self.path] = info
        for entry in value['captures']:
            self._validate_entry(entry)
            if entry['captureId'] in self.entries: raise JournalError('duplicate historical capture')
            self.entries[entry['captureId']] = entry
        self._verified = False

    def _validate_entry(self, entry):
        if (not isinstance(entry, dict) or set(entry) != {'captureId', 'canonicalPath', 'classification',
                'recordingComplete', 'stopReceipt', 'files', 'directories', 'gapMarkers'}):
            raise JournalError('invalid historical capture schema')
        game = entry['captureId']
        if not isinstance(game, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', game):
            raise JournalError('invalid historical capture identity')
        if entry['canonicalPath'] != str(self.root / game):
            raise JournalError('historical capture outside exact canonical route')
        if entry['classification'] != CLASSIFICATION or entry['recordingComplete'] is not False:
            raise JournalError('historical capture must remain acknowledged incomplete')
        receipt = entry['stopReceipt']
        if (not isinstance(receipt, dict) or set(receipt) != {'path', 'sha256'} or
                not isinstance(receipt['path'], str) or not Path(receipt['path']).is_absolute() or
                not isinstance(receipt['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', receipt['sha256'])):
            raise JournalError('invalid historical stop receipt reference')
        if (not isinstance(entry['files'], dict) or not set(LOCKS) <= set(entry['files']) or
                'native-000000.ndjson' not in entry['files'] or 'manifest.json' in entry['files'] or
                not isinstance(entry['directories'], dict) or '' not in entry['directories']):
            raise JournalError('historical inventory missing sources or ownership locks')
        for name, meta in entry['files'].items():
            _relative(name)
            if (not isinstance(meta, dict) or set(meta) != FILE_KEYS or
                    any(type(meta[key]) is not int or meta[key] < 0 for key in FILE_KEYS - {'sha256'}) or
                    not isinstance(meta['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', meta['sha256']) or
                    meta['mode'] != 0o600):
                raise JournalError('invalid historical file custody inventory')
        for name, meta in entry['directories'].items():
            if name: _relative(name)
            if (not isinstance(meta, dict) or set(meta) != IDENTITY_KEYS or
                    any(type(value) is not int or value < 0 for value in meta.values()) or meta['mode'] != 0o700):
                raise JournalError('invalid historical directory custody inventory')
        gaps = entry['gapMarkers']
        actual = {name: meta['sha256'] for name, meta in entry['files'].items()
                  if re.fullmatch(r'native-gap-[A-Za-z0-9_.-]+\.json', name)}
        if not isinstance(gaps, dict) or not gaps or gaps != actual:
            raise JournalError('historical capture gap binding differs from full inventory')

    def _receipt(self, entry):
        reference = entry['stopReceipt']; path = Path(reference['path'])
        if path == self.root or self.root in path.parents:
            raise JournalError('historical stop receipt must be outside source tree')
        raw, info = _root_private_read(path)
        if hashlib.sha256(raw).hexdigest() != reference['sha256']:
            raise JournalError('historical stop receipt hash mismatch')
        receipt = json.loads(raw, object_pairs_hook=_unique)
        files = {name: {key: meta[key] for key in ('size', 'sha256', 'mtime_ns')}
                 for name, meta in entry['files'].items()}
        if (not isinstance(receipt, dict) or receipt.get('capture_id') != entry['captureId'] or
                receipt.get('classification') != CLASSIFICATION or receipt.get('recording_complete') is not False or
                'winner' not in receipt or receipt['winner'] is not None or receipt.get('source_files_unchanged') is not True or
                receipt.get('files_before') != files or receipt.get('files_after') != files):
            raise JournalError('historical stop receipt does not attest preserved incomplete capture')
        self._metadata[path] = info

    def _members(self, entry):
        directory = self.root / entry['captureId']
        dirs, files = {}, {}
        root_info = directory.lstat()
        if not stat.S_ISDIR(root_info.st_mode): raise JournalError('historical capture directory replaced')
        dirs[''] = identity(root_info)
        for path in directory.rglob('*'):
            check_cancelled(self.cancel)
            info = path.lstat(); name = path.relative_to(directory).as_posix()
            if stat.S_ISDIR(info.st_mode): dirs[name] = identity(info)
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                files[name] = {**identity(info), 'size': info.st_size, 'mtime_ns': info.st_mtime_ns}
            else: raise JournalError('historical capture member replaced or linked')
        expected = {name: {key: value for key, value in meta.items() if key != 'sha256'}
                    for name, meta in entry['files'].items()}
        if dirs != entry['directories'] or files != expected:
            raise JournalError('historical capture membership or custody changed')
        if any(meta['uid'] != root_info.st_uid for meta in [*dirs.values(), *files.values()]):
            raise JournalError('historical capture ownership mismatch')

    def _acquire(self, entry):
        directory = self.root / entry['captureId']; descriptors = {}
        try:
            # Gym first: another registry in this process must fail before opening
            # the native inode. Closing ANY descriptor for that inode releases
            # process-scoped POSIX locks, even when a different fd holds the lock.
            for name in LOCKS:
                fd = os.open(directory / name, os.O_RDWR | os.O_NOFOLLOW)
                descriptors[name] = fd
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or identity(info) != {
                        key: entry['files'][name][key] for key in IDENTITY_KEYS}:
                    raise JournalError('historical writer lock inode or custody mismatch')
                if name == '.writer.lock': fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                else: fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB, 0, 0, os.SEEK_SET)
            return descriptors
        except BaseException:
            for fd in descriptors.values(): os.close(fd)
            raise

    def verify(self):
        try:
            for path, expected in self._metadata.items():
                if _root_private_identity(path) != expected: raise JournalError('historical attestation replaced or changed')
            for game, entry in self.entries.items():
                if game not in self.held:
                    self._receipt(entry)
                    descriptors = self._acquire(entry)
                    self.held[game] = descriptors
                    self._members(entry)
                    for name, meta in entry['files'].items():
                        if name in descriptors:
                            actual = _digest(descriptors[name], self.cancel)
                        else:
                            fd = os.open(self.root / game / name, os.O_RDONLY | os.O_NOFOLLOW)
                            try: actual = _digest(fd, self.cancel)
                            finally: os.close(fd)
                        if actual != meta['sha256']: raise JournalError('historical capture hash mismatch')
                self._members(entry)
                for name, fd in self.held[game].items():
                    if identity(os.fstat(fd)) != {key: entry['files'][name][key] for key in IDENTITY_KEYS}:
                        raise JournalError('historical held writer inode changed')
            self._verified = True
        except BaseException:
            self.close()
            raise

    def contains(self, game):
        return self._verified and game in self.held

    def close(self):
        self._verified = False
        for descriptors in self.held.values():
            for fd in descriptors.values(): os.close(fd)
        self.held.clear()
