"""Local analysis claims and receipts over verified game evidence; never uploads."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from .game_journal import (
    JournalError, _atomic_private, _json, _private_dir, _safe,
    verify_finalized_manifest,
)


def _paths(directory: Path, version: str) -> tuple[Path, Path]:
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', version):
        raise JournalError('analysis version must be a portable identifier')
    for path in (directory / 'analysis', directory / 'analysis' / version):
        _private_dir(path, create=True)
    base = directory / 'analysis' / version
    return base / 'review.json', base / 'report.json'


@contextmanager
def _locked(directory: Path) -> Iterator[None]:
    fd = os.open(directory / '.analysis.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def claim_analysis(directory: Path, analysis_version: str, *, lease_seconds: int = 3600) -> dict[str, Any]:
    """Atomic lease: completed verified reports are idempotent, stale work resumes."""
    manifest = verify_finalized_manifest(directory)
    if type(lease_seconds) is not int or not 1 <= lease_seconds <= 86400:
        raise JournalError('invalid analysis lease')
    with _locked(directory):
        ledger, report = _paths(directory, analysis_version)
        key = {'run_id': manifest['run_id'], 'artifact_hash': manifest['artifact_hash'],
               'analysis_version': analysis_version}
        now = time.time()
        previous = json.loads(ledger.read_bytes()) if ledger.exists() else None
        if previous is not None:
            if any(previous.get(k) != v for k, v in key.items()):
                raise JournalError('analysis identity mismatch')
            if previous.get('status') == 'completed':
                if report.is_symlink() or not report.exists() or hashlib.sha256(report.read_bytes()).hexdigest() != previous.get('report_sha256'):
                    raise JournalError('completed analysis report is missing or changed')
                return {**previous, 'claim_status': 'already_completed'}
            if previous.get('lease_expires_at', 0) > now:
                return {**key, 'claim_status': 'busy'}
        receipt = {'schema_version': 1, **key, 'status': 'running',
                   'claim_id': str(uuid.uuid4()), 'lease_expires_at': now + lease_seconds,
                   'updated_at': now, 'resuming': previous is not None}
        _atomic_private(ledger, _json(receipt) + b'\n')
        return {**receipt, 'claim_status': 'claimed'}


def finish_analysis(directory: Path, analysis_version: str, claim_id: str,
                    report_content: Mapping[str, Any]) -> dict[str, Any]:
    """Mark complete only after private report fsync and hash verification.

    Reports are independently versioned annotations, not edits to raw evidence.
    A different report at the same version requires a new analysis version.
    """
    manifest = verify_finalized_manifest(directory)
    _safe(report_content)
    with _locked(directory):
        ledger, report = _paths(directory, analysis_version)
        receipt = json.loads(ledger.read_bytes())
        key = {'run_id': manifest['run_id'], 'artifact_hash': manifest['artifact_hash'],
               'analysis_version': analysis_version}
        if (receipt.get('status') != 'running' or receipt.get('claim_id') != claim_id or
                any(receipt.get(k) != v or report_content.get(k) != v for k, v in key.items())):
            raise JournalError('analysis claim or report identity mismatch')
        data = _json(dict(report_content)) + b'\n'
        if report.exists() and (report.is_symlink() or report.read_bytes() != data):
            raise JournalError('existing analysis report differs; increment analysis version')
        _atomic_private(report, data)
        digest = hashlib.sha256(data).hexdigest()
        if hashlib.sha256(report.read_bytes()).hexdigest() != digest:
            raise JournalError('analysis report verification failed')
        receipt.update(status='completed', report_sha256=digest, updated_at=time.time(),
                       report_path=f'analysis/{analysis_version}/report.json')
        receipt.pop('lease_expires_at', None)
        _atomic_private(ledger, _json(receipt) + b'\n')
        return receipt
