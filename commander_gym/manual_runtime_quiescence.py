"""Root-owned stop evidence derived from exact drained state and durable bytes.

No terminal is invented and no journal is rewritten. A changed capture during
shutdown is an operator hold, even if that change would otherwise be harmless.
Only the root-level native heartbeat is excluded; it is bound through the fresh
closed lifecycle state before stopping. Per-game receipts and originals are hashed.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import stat

from .manual_runtime_profile import digest, protected_bytes, require
from .manual_runtime_rollout import idle_state


def capture_inventory(root: Path, *, uid: int) -> dict:
    require(root.is_absolute(), 'capture_root_absolute')
    files = {}
    for path in (root, *root.rglob('*')):
        info = path.lstat()
        require(info.st_uid == uid and info.st_mode & 0o022 == 0, 'capture_custody')
        if stat.S_ISDIR(info.st_mode):
            continue
        require(stat.S_ISREG(info.st_mode), 'capture_special_file')
        if path == root / '.native-health.json':
            continue
        files[str(path.relative_to(root))] = hashlib.sha256(protected_bytes(path, uid=uid, max_bytes=512*1024*1024)).hexdigest()
    return files


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
