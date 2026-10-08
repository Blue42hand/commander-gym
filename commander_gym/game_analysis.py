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


def claim_analysis(directory: Path, analysis_version: str, *, lease_seconds: int = 3600,
                   worker_id: str = "local-analysis-worker") -> dict[str, Any]:
    """Atomic lease: completed verified reports are idempotent, stale work resumes."""
    manifest = verify_finalized_manifest(directory)
    if not isinstance(worker_id, str) or not worker_id:
        raise JournalError('worker_id is required')
    _safe({'worker_id': worker_id})
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
                   'worker_id': worker_id,
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
                       report_path=f'analysis/{analysis_version}/report.json',
                       notification={'status': 'pending', 'report_sha256': digest})
        receipt.pop('lease_expires_at', None)
        _atomic_private(ledger, _json(receipt) + b'\n')
        return receipt


def renew_analysis(directory: Path, analysis_version: str, claim_id: str, worker_id: str,
                   *, lease_seconds: int = 3600) -> dict[str, Any]:
    verify_finalized_manifest(directory)
    if type(lease_seconds) is not int or not 1 <= lease_seconds <= 86400:
        raise JournalError('invalid analysis lease')
    with _locked(directory):
        ledger, _ = _paths(directory, analysis_version)
        receipt = json.loads(ledger.read_bytes())
        if (receipt.get('status') != 'running' or receipt.get('claim_id') != claim_id or
                receipt.get('worker_id') != worker_id or receipt.get('lease_expires_at', 0) <= time.time()):
            raise JournalError('renewal requires an owned live claim')
        receipt.update(lease_expires_at=time.time() + lease_seconds, updated_at=time.time())
        _atomic_private(ledger, _json(receipt) + b'\n')
        return receipt


def fail_analysis(directory: Path, analysis_version: str, claim_id: str, worker_id: str,
                  failure_code: str) -> dict[str, Any]:
    verify_finalized_manifest(directory)
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', failure_code):
        raise JournalError('failure_code must be a stable code, not raw error text')
    _safe({'failure_code': failure_code})
    with _locked(directory):
        ledger, _ = _paths(directory, analysis_version)
        receipt = json.loads(ledger.read_bytes())
        if (receipt.get('status') != 'running' or receipt.get('claim_id') != claim_id or
                receipt.get('worker_id') != worker_id):
            raise JournalError('failure requires an owned claim')
        receipt.update(status='failed', failure_code=failure_code, updated_at=time.time())
        receipt.pop('lease_expires_at', None)
        _atomic_private(ledger, _json(receipt) + b'\n')
        return receipt


def prepare_notification(directory: Path, analysis_version: str, worker_id: str) -> dict[str, Any]:
    """Reserve once before sending. An uncertain attempt is never automatically retried."""
    manifest = verify_finalized_manifest(directory)
    if not isinstance(worker_id, str) or not worker_id:
        raise JournalError('worker_id is required')
    _safe({'worker_id': worker_id})
    with _locked(directory):
        ledger, report = _paths(directory, analysis_version)
        receipt = json.loads(ledger.read_bytes())
        if (receipt.get('status') != 'completed' or receipt.get('artifact_hash') != manifest['artifact_hash']
                or not report.exists() or report.is_symlink() or
                hashlib.sha256(report.read_bytes()).hexdigest() != receipt.get('report_sha256')):
            raise JournalError('notification requires a completed verified report')
        delivery = receipt['notification']
        if delivery['status'] != 'pending':
            return {**delivery, 'send_allowed': False}
        if delivery.get('delivery_id'):
            attempts = delivery.setdefault('attempts', [])
            attempts.append({k: v for k, v in delivery.items() if k != 'attempts'})
            delivery.pop('receipt', None)
            delivery.pop('updated_at', None)
        delivery.update(status='uncertain', delivery_id=str(uuid.uuid4()), worker_id=worker_id,
                        prepared_at=time.time())
        _atomic_private(ledger, _json(receipt) + b'\n')
        return {**delivery, 'send_allowed': True}


def record_notification(directory: Path, analysis_version: str, delivery_id: str, *,
                        outcome: str, delivery_receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Caller records observed delivery evidence, never assumes a send succeeded.

    not_sent requires affirmative evidence of no external send. uncertain blocks
    automatic retry; reconciliation must inspect the actual delivery system.
    """
    verify_finalized_manifest(directory)
    if outcome not in {'delivered', 'not_sent', 'uncertain'} or not delivery_receipt:
        raise JournalError('delivery outcome and observed receipt are required')
    _safe(delivery_receipt)
    with _locked(directory):
        ledger, _ = _paths(directory, analysis_version)
        receipt = json.loads(ledger.read_bytes())
        delivery = receipt['notification']
        if delivery.get('delivery_id') != delivery_id or delivery['status'] == 'delivered':
            raise JournalError('delivery attempt identity mismatch or already delivered')
        delivery.update(status='pending' if outcome == 'not_sent' else outcome,
                        receipt=dict(delivery_receipt), updated_at=time.time())
        _atomic_private(ledger, _json(receipt) + b'\n')
        return delivery
