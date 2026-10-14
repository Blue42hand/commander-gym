"""Validated Git data snapshots; publication never imports code from the checkout."""
from __future__ import annotations

import hashlib
from collections import OrderedDict
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import threading

from .binding_catalog import load_binding_catalog
from .game_server_bindings import GameServerBindingRegistry

MAX_TREE_BYTES = 128 * 1024**2
MAX_FILES = 10000
SHA = re.compile(r'[0-9a-f]{64}\Z')
PROFILE = re.compile(r's_([0-9a-f]{64})_([0-9]{1,5})\Z')


class CatalogReloadError(ValueError):
    """Stable operational diagnosis, without private catalog contents."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def relative(value):
    if not isinstance(value, str) or not value or '\\' in value or '\x00' in value:
        raise CatalogReloadError('catalog_path')
    p = PurePosixPath(value)
    if p.is_absolute() or any(part in ('', '.', '..') for part in value.split('/')):
        raise CatalogReloadError('catalog_path')
    return value


def atomic(path, raw):
    fd, name = tempfile.mkstemp(prefix='.publish-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(raw); f.flush(); os.fsync(f.fileno())
        os.chmod(name, 0o640)
        os.replace(name, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        if os.path.exists(name): os.unlink(name)


def git(checkout, *args, output=None):
    command = ['git', '--no-replace-objects', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null',
               '-c', 'safe.directory=' + str(checkout),
               '-C', str(checkout), *args]
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=output or subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=30, check=True)
    except (OSError, subprocess.SubprocessError):
        raise CatalogReloadError('git_unavailable') from None
    return result.stdout


def clean_head(checkout):
    if git(checkout, 'status', '--porcelain=v1', '--untracked-files=all').strip():
        raise CatalogReloadError('checkout_not_clean')
    lock = Path(git(checkout, 'rev-parse', '--git-path', 'index.lock').decode().strip())
    if not lock.is_absolute(): lock = checkout / lock
    if lock.exists(): raise CatalogReloadError('pull_in_progress')
    head = git(checkout, 'rev-parse', '--verify', 'HEAD^{commit}').decode().strip()
    if not re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}', head):
        raise CatalogReloadError('git_identity')
    return head


def validated_registry(root, roster, pilot_factory, allowed_pilots):
    def approved(pilot, binding):
        if pilot.fingerprint() not in allowed_pilots:
            raise CatalogReloadError('pilot_not_installed')
        return pilot_factory(pilot, binding)
    loaded = load_binding_catalog(root / relative(roster), instance_root=root, pilot_factory=approved)
    return GameServerBindingRegistry(loaded.resolver, loaded.binding_ids)


def publish_checkout(checkout: Path, store: Path, roster: str, *, pilot_factory, allowed_pilots, validate_decks=None):
    """Publish one clean committed tree, or leave the current selection untouched.

    Caller serializes publication. Git hooks/filters are never used for extraction.
    The archive is a data tree, with no symlinks, special files or executable hooks.
    """
    checkout = checkout.resolve(); store = store.resolve(); relative(roster)
    store.mkdir(parents=True, exist_ok=True, mode=0o750)
    snapshots = store / 'snapshots'; snapshots.mkdir(exist_ok=True, mode=0o750)
    head = clean_head(checkout)
    current = store / 'current.json'
    if current.exists():
        selected = json.loads(current.read_bytes())
        if selected.get('commit') == head:
            directory, metadata = verify_snapshot(store, selected['snapshot'])
            validated_registry(directory, metadata['roster'], pilot_factory, allowed_pilots)
            return {'state': 'unchanged', **selected}
    # Bound the archive before Git writes it, including per-entry tar overhead.
    inventory = git(checkout, 'ls-tree', '-rlz', head).split(b'\0')
    count, estimated = 0, 10240
    for row in inventory:
        if not row: continue
        header, name = row.split(b'\t', 1)
        mode, kind, object_id, size = header.split()
        if mode != b'100644' or kind != b'blob':
            raise CatalogReloadError('catalog_requires_data_files')
        count += 1
        estimated += ((int(size) + 511) // 512 + 4) * 512 + len(name)
        if count > MAX_FILES or estimated > MAX_TREE_BYTES:
            raise CatalogReloadError('catalog_size')
    stage = Path(tempfile.mkdtemp(prefix='.candidate-', dir=store))
    try:
        with tempfile.TemporaryFile() as archive:
            git(checkout, 'archive', '--format=tar', head, output=archive)
            if archive.tell() > MAX_TREE_BYTES: raise CatalogReloadError('catalog_size')
            archive.seek(0)
            files, total = {}, 0
            with tarfile.open(fileobj=archive, mode='r:') as tree:
                for entry in tree:
                    name = relative(entry.name.rstrip('/'))
                    if entry.isdir(): continue
                    if name == 'snapshot.json': raise CatalogReloadError('reserved_snapshot_path')
                    if not entry.isfile() or entry.mode & 0o111:
                        raise CatalogReloadError('catalog_requires_data_files')
                    total += entry.size
                    if total > MAX_TREE_BYTES or len(files) >= MAX_FILES or name in files:
                        raise CatalogReloadError('catalog_size_or_duplicate')
                    source = tree.extractfile(entry)
                    if source is None: raise CatalogReloadError('catalog_file')
                    raw = source.read(MAX_TREE_BYTES + 1)
                    if len(raw) != entry.size: raise CatalogReloadError('catalog_file')
                    target = stage / name; target.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
                    with target.open('xb') as out:
                        out.write(raw); out.flush(); os.fsync(out.fileno())
                    target.chmod(0o440); files[name] = sha(raw)
        registry = validated_registry(stage, roster, pilot_factory, allowed_pilots)
        if len(registry.profiles) > 256: raise CatalogReloadError('catalog_profile_limit')
        if validate_decks is not None: validate_decks(registry)
        metadata = {'version': 1, 'commit': head, 'roster': roster, 'files': files,
                    'bindings': [p.binding_id for p in registry.profiles]}
        snapshot = sha(canonical(metadata))
        atomic(stage / 'snapshot.json', canonical(metadata))
        (stage / 'snapshot.json').chmod(0o440)
        stage.chmod(0o750)
        for directory in [p for p in stage.rglob('*') if p.is_dir()] + [stage]:
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(fd)
            finally: os.close(fd)
        if clean_head(checkout) != head: raise CatalogReloadError('checkout_changed')
        destination = snapshots / snapshot
        if destination.exists():
            # A prior interrupted publisher may have installed this immutable tree.
            verify_snapshot(store, snapshot)
        else:
            os.rename(stage, destination)
            fd = os.open(snapshots, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(fd)
            finally: os.close(fd)
        selected = {'snapshot': snapshot, 'commit': head}
        atomic(current, canonical(selected))
        return {'state': 'published', **selected}
    finally:
        if stage.exists(): shutil.rmtree(stage)


def verify_snapshot(store, snapshot):
    store = Path(store).resolve()
    if not isinstance(snapshot, str) or not SHA.fullmatch(snapshot):
        raise CatalogReloadError('snapshot_identity')
    directory = store / 'snapshots' / snapshot
    if directory.is_symlink() or not directory.is_dir(): raise CatalogReloadError('snapshot_missing')
    manifest = directory / 'snapshot.json'
    if manifest.is_symlink(): raise CatalogReloadError('snapshot_custody')
    metadata = json.loads(manifest.read_bytes())
    if sha(canonical(metadata)) != snapshot or metadata.get('version') != 1:
        raise CatalogReloadError('snapshot_digest')
    for name, digest in metadata['files'].items():
        path = directory / relative(name)
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise CatalogReloadError('snapshot_custody')
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022 or sha(path.read_bytes()) != digest:
            raise CatalogReloadError('snapshot_file_changed')
    return directory, metadata


class LiveBindingRegistry:
    """Current profiles for discovery; immutable old selections remain resolvable."""
    def __init__(self, store, *, pilot_factory, allowed_pilots):
        self.store = Path(store).resolve()
        self.pilot_factory = pilot_factory
        self.allowed_pilots = frozenset(allowed_pilots)
        self._loaded = OrderedDict()
        self._metadata = {}
        self._current = None
        self._lock = threading.RLock()
        self.last_error = None
        self.refresh()
        if self._current is None: raise CatalogReloadError('no_valid_catalog')

    def _load(self, snapshot):
        with self._lock:
            if snapshot not in self._loaded:
                directory, metadata = verify_snapshot(self.store, snapshot)
                registry = validated_registry(directory, metadata['roster'], self.pilot_factory, self.allowed_pilots)
                if [p.binding_id for p in registry.profiles] != metadata['bindings']:
                    raise CatalogReloadError('snapshot_bindings')
                self._loaded[snapshot] = registry
                self._metadata[snapshot] = metadata
                while len(self._loaded) > 4:
                    evicted, _ = self._loaded.popitem(last=False)
                    self._metadata.pop(evicted)
            self._loaded.move_to_end(snapshot)
            return self._loaded[snapshot]

    def refresh(self):
        try:
            selected = json.loads((self.store / 'current.json').read_bytes())
            snapshot = selected['snapshot']; self._load(snapshot)
            with self._lock:
                self._current = snapshot; self.last_error = None
        except (OSError, ValueError, KeyError, TypeError):
            self.last_error = 'catalog_reload_rejected'

    def profile_payloads(self):
        self.refresh()
        with self._lock:
            snapshot = self._current
            return tuple({**p, 'id': f's_{snapshot}_{i}'}
                         for i, p in enumerate(self._load(snapshot).profile_payloads()))

    def selection(self, profile_id):
        match = PROFILE.fullmatch(profile_id) if isinstance(profile_id, str) else None
        if match is None: raise CatalogReloadError('snapshot_profile')
        snapshot, index = match.group(1), int(match.group(2))
        registry = self._load(snapshot)
        if index >= len(registry.profiles): raise CatalogReloadError('snapshot_profile')
        return registry, registry.profiles[index].binding_id

    def contains(self, profile_id):
        try: self.selection(profile_id); return True
        except (OSError, ValueError, KeyError, TypeError): return False

    def create_seat(self, player_id, profile_id, **kwargs):
        with self._lock:
            registry, binding_id = self.selection(profile_id)
            snapshot = PROFILE.fullmatch(profile_id).group(1)
            lineage = {'sha256': snapshot, 'commit': self._metadata[snapshot]['commit']}
        return registry.create_seat(player_id, binding_id, catalog_snapshot=lineage, **kwargs)
