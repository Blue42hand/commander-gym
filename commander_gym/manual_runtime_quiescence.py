"""Root-owned stop evidence derived from exact drained state and durable bytes.

No terminal is invented and no journal is rewritten. A changed capture during
shutdown is an operator hold, even if that change would otherwise be harmless.
Only the root-level native heartbeat is excluded; it is bound through the fresh
closed lifecycle state before stopping. Per-game receipts and originals are hashed.
"""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
import stat

from .manual_runtime_profile import digest, require
from .manual_runtime_rollout import idle_state


# Limits apply to streamed bytes, not an in-memory copy of each recording.
MAX_CAPTURE_FILE_BYTES = 8 * 1024**3
MAX_CAPTURE_TOTAL_BYTES = 64 * 1024**3
MAX_CAPTURE_ENTRIES = 100_000
MAX_CAPTURE_DIRECTORIES = 256
CAPTURE_TIMEOUT_SECONDS = 300


def capture_inventory(root: Path, *, uid: int, ancestor_owners: dict[Path, int] | None = None) -> dict:
    """Hash a stable native-owned tree through pinned directory descriptors.

    Only explicitly named ancestors may belong to another declared service UID.
    That permission never extends into the recording root. Root-written analysis
    sidecars are also hashed; native captures must still be native-owned. All path components
    are opened without following symlinks and checked again before returning.
    """
    require(root.is_absolute() and root != Path('/') and '..' not in root.parts, 'capture_root_absolute')
    owners = {} if ancestor_owners is None else dict(ancestor_owners)
    require(all(isinstance(p, Path) and p in root.parents and p != Path('/')
                and type(owner) is int and owner >= 0 for p, owner in owners.items()), 'capture_ancestor_policy')
    deadline = time.monotonic() + CAPTURE_TIMEOUT_SECONDS
    handles = []
    links = []
    directories = []
    members = []
    files = {}
    total = 0
    entries = 0

    def within_deadline():
        require(time.monotonic() < deadline, 'capture_deadline')

    def custody(s):
        return (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid)

    def stamp(s):
        return (*custody(s), s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)

    def recording_owners(relative):
        parts = Path(relative).parts
        # Existing offline analyzers write these derived artifacts as root.
        # Include every byte in the barrier; never treat them as native captures.
        analysis = len(parts) >= 2 and (parts[1] == 'analysis'
                    or (len(parts) == 2 and parts[1] == '.analysis.lock'))
        return {0, uid} if analysis else {uid}

    def open_directory(parent, name, path, native_owners=None):
        within_deadline()
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        handles.append(fd)
        info = os.fstat(fd)
        allowed = native_owners if native_owners is not None else ({owners[path]} if path in owners else {0, uid})
        require(stat.S_ISDIR(info.st_mode) and info.st_uid in allowed and not info.st_mode & 0o022,
                'capture_custody' if native_owners is not None else 'capture_ancestor_custody')
        if parent is not None:
            require(custody(os.stat(name, dir_fd=parent, follow_symlinks=False)) == custody(info), 'capture_directory_changed')
            links.append((parent, name, fd, custody(info)))
        return fd

    try:
        fd = open_directory(None, '/', Path('/'))
        path = Path('/')
        for part in root.parts[1:]:
            path /= part
            fd = open_directory(fd, part, path, native_owners={uid} if path == root else None)
        root_device = os.fstat(fd).st_dev
        pending = [(fd, '')]
        while pending:
            within_deadline()
            parent, relative = pending.pop()
            names = set()
            with os.scandir(parent) as scan:
                for entry in scan:
                    within_deadline()
                    entries += 1
                    require(entries <= MAX_CAPTURE_ENTRIES, 'capture_entry_limit')
                    info = os.stat(entry.name, dir_fd=parent, follow_symlinks=False)
                    name = relative + '/' + entry.name if relative else entry.name
                    require(info.st_uid in recording_owners(name) and not info.st_mode & 0o022, 'capture_custody')
                    require(info.st_dev == root_device, 'capture_cross_device')
                    if name == '.native-health.json':
                        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'capture_special_file')
                        continue
                    names.add(entry.name)
                    if stat.S_ISDIR(info.st_mode):
                        require(len(directories) + len(pending) + 1 < MAX_CAPTURE_DIRECTORIES, 'capture_directory_limit')
                        child = open_directory(parent, entry.name, root / name, native_owners=recording_owners(name))
                        pending.append((child, name))
                        continue
                    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'capture_special_file')
                    require(info.st_size <= MAX_CAPTURE_FILE_BYTES, 'capture_file_limit')
                    require(total + info.st_size <= MAX_CAPTURE_TOTAL_BYTES, 'capture_total_limit')
                    child = os.open(entry.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                    try:
                        require(stamp(os.fstat(child)) == stamp(info), 'capture_file_changed')
                        h = hashlib.sha256()
                        count = 0
                        while True:
                            within_deadline()
                            data = os.read(child, min(1024 * 1024, info.st_size - count + 1))
                            if not data:
                                break
                            count += len(data)
                            require(count <= info.st_size, 'capture_file_changed')
                            h.update(data)
                        require(count == info.st_size and stamp(os.fstat(child)) == stamp(info), 'capture_file_changed')
                    finally:
                        os.close(child)
                    total += count
                    files[name] = h.hexdigest()
                    members.append((parent, entry.name, stamp(info)))
            directories.append((parent, relative, names))
        # Recheck membership, file identities, and every pinned path component.
        # Only the live root heartbeat is permitted to change during inventory.
        for parent, relative, names in directories:
            within_deadline()
            actual = set()
            with os.scandir(parent) as scan:
                for entry in scan:
                    require(len(actual) <= MAX_CAPTURE_ENTRIES, 'capture_entry_limit')
                    if relative or entry.name != '.native-health.json':
                        actual.add(entry.name)
            require(actual == names, 'capture_membership_changed')
        for parent, name, before in members:
            within_deadline()
            require(stamp(os.stat(name, dir_fd=parent, follow_symlinks=False)) == before, 'capture_file_changed')
        for parent, name, fd, before in reversed(links):
            within_deadline()
            require(custody(os.fstat(fd)) == before
                    and custody(os.stat(name, dir_fd=parent, follow_symlinks=False)) == before, 'capture_directory_changed')
        return files
    finally:
        for fd in reversed(handles):
            os.close(fd)

def stop_context(snapshot, state: dict, inventory: dict, *, nonce: str, now: float) -> dict:
    boot = idle_state(state, snapshot, now=now, closed=True)
    require(type(nonce) is str and len(nonce) == 32 and all(c in '0123456789abcdef' for c in nonce), 'stop_nonce')
    return {'runtimeId': snapshot.runtime_id, 'bootId': boot, 'stopNonce': nonce,
            'sequence': snapshot.sequence, 'engineSha': snapshot.engine_sha,
            'gymSha': snapshot.gym_sha, 'closedStateSha256': digest(state),
            'captureInventorySha256': digest(inventory), 'observedUnix': now}


def matches_stop_proof(proof: dict, context: dict, *, now: float) -> bool:
    return (type(proof) is dict and set(proof) == set(context) | {'reconciled', 'pendingRecordWrites', 'verifiedUnix'}
            and all(proof.get(key) == value for key, value in context.items())
            and proof['reconciled'] is True and type(proof['pendingRecordWrites']) is int and proof['pendingRecordWrites'] == 0
            and type(proof['verifiedUnix']) in (int, float) and 0 <= now - proof['verifiedUnix'] <= 10)
