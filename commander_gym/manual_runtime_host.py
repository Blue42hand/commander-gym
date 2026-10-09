"""Protected Linux file/service adapter for later explicitly approved activation.

Nothing is installed by importing this module. The root dispatcher refuses absent
policy/selector/receipts, preserves the keyless helpers, and never opens the provider
key. Unit credential materialization belongs to systemd AFTER this transaction's
root precredential validation. Live invocation is outside source qualification.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import fcntl
import importlib
import os
from pathlib import Path
import pwd
import stat
import subprocess
import sys
import tempfile
import time
import uuid

from .manual_runtime_legacy import locked_legacy_call
from .manual_runtime_profile import (
    ValidatedRuntime, canonical, decode, digest, precredential_validate,
    protected_bytes, require, validate_profile, verify_artifacts,
)
from .manual_runtime_rollout import RuntimeSnapshot
from .manual_runtime_quiescence import capture_inventory, stop_context, matches_stop_proof

CONFIG = Path('/etc/argentum-luna')
STATE = Path('/var/lib/argentum-updater/manual-runtime')
GATE = Path('/run/argentum-luna-runtime/precredential.json')
NATIVE_LIB = Path('/usr/local/libexec/argentum-native')
SOURCE_LIB = Path('/usr/local/libexec/argentum-source')
UNITS = ('argentum-web.service', 'argentum-play.service', 'argentum-luna-sidecar.service', 'argentum-recorder.service')
TARGETS = {
    'profile.json': CONFIG / 'service-profile.json',
    'lan-server.crt': CONFIG / 'lan-server.crt',
    'nginx.conf': Path('/etc/argentum-play/argentum-play.nginx.conf'),
    'units/argentum-play.conf': Path('/etc/systemd/system/argentum-play.service.d/manual-runtime.conf'),
    'units/argentum-recorder.conf': Path('/etc/systemd/system/argentum-recorder.service.d/manual-runtime.conf'),
    'units/argentum-web.conf': Path('/etc/systemd/system/argentum-web.service.d/manual-runtime.conf'),
    'units/argentum-source-feeder.conf': Path('/etc/systemd/system/argentum-source-feeder.service.d/manual-runtime.conf'),
    'units/argentum-updater.conf': Path('/etc/systemd/system/argentum-updater.service.d/manual-runtime.conf'),
    'units/argentum-admission.conf': Path('/etc/systemd/system/argentum-admission.service.d/manual-runtime.conf'),
    'units/argentum-luna-sidecar.service': Path('/etc/systemd/system/argentum-luna-sidecar.service'),
    'units/argentum-manual-preflight.service': Path('/etc/systemd/system/argentum-manual-preflight.service'),
}


def root_json(path: Path) -> dict:
    return decode(protected_bytes(path, uid=0, max_bytes=4 * 1024 * 1024))


def _directory(path: Path):
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and info.st_mode & 0o022 == 0, 'untrusted_root_directory')
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and info.st_mode & 0o022 == 0, 'untrusted_root_parent')


def atomic_root(path: Path, raw: bytes, *, mode=0o600):
    _directory(path.parent)
    if path.exists() or path.is_symlink():
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_mode & 0o022 == 0, 'untrusted_replace_target')
    fd, name = tempfile.mkstemp(prefix='.manual-', dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(name): os.unlink(name)


def load_bundle(runtime_id: str) -> tuple[dict, dict, dict]:
    require(type(runtime_id) is str and len(runtime_id) == 64 and all(c in '0123456789abcdef' for c in runtime_id), 'runtime_id')
    root = CONFIG / 'runtimes' / runtime_id
    profile, receipt, approval = (root_json(root / name) for name in ('profile.json', 'qualification.json', 'approval.json'))
    require(digest(profile) == runtime_id, 'runtime_directory_identity')
    return profile, receipt, approval


class ProtectedRuntimeHost:
    def __init__(self, anchor_profile: dict):
        require(sys.platform == 'linux' and os.geteuid() == 0, 'linux_root_required')
        validate_profile(anchor_profile)
        require(anchor_profile['purpose'] == 'native-manual-play', 'production_anchor_required')
        verify_artifacts(anchor_profile)
        for role, path in (('legacyNative', NATIVE_LIB), ('legacySource', SOURCE_LIB)):
            require(Path(anchor_profile['artifacts'][role]['root']) == path, 'legacy_layout_mismatch')
        for field, name in (('nativeUid', 'argentum-play'), ('sidecarUid', 'commander-prod'), ('proxyUid', 'argentum-web')):
            require(anchor_profile['identities'][field] == pwd.getpwnam(name).pw_uid, 'runtime_identity_changed')
        _directory(CONFIG); _directory(STATE)
        policy = root_json(CONFIG / 'operator-policy.json')
        require(set(policy) == {'schemaVersion', 'implementationApproved', 'activationApproved', 'minFreeBytes'}
                and type(policy['schemaVersion']) is int and policy['schemaVersion'] == 1
                and policy['implementationApproved'] is True and policy['activationApproved'] is True
                and type(policy['minFreeBytes']) is int and policy['minFreeBytes'] >= 32 * 1024**3, 'operator_activation_policy')
        self.policy = policy
        # No environment-controlled import path; legacy trees were fully sealed.
        for path in (NATIVE_LIB, SOURCE_LIB): sys.path.insert(0, str(path))
        self.legacy = importlib.import_module('idle_updater')
        self.native = importlib.import_module('host_adapter')
        self.base = self.legacy.IdleHost()
        self.locked = False
        self.pending = None
        self.anchor = anchor_profile

    @contextmanager
    def exclusive_lock(self):
        require(not self.locked, 'nested_runtime_lock')
        with self.base.exclusive_lock():
            self.locked = True
            try: yield
            finally: self.locked = False

    def _locked(self): require(self.locked, 'runtime_lock_required')

    def current(self):
        selector = root_json(CONFIG / 'current.json')
        require(set(selector) == {'kind', 'runtimeId', 'sequence', 'previousRuntimeId'}
                and type(selector['sequence']) is int and selector['sequence'] >= 1, 'selector_fields')
        if selector['kind'] == 'keyless':
            _, manifest = self.native.current_release()
            return RuntimeSnapshot(manifest['releaseId'], max(selector['sequence'], manifest['promotionSequence']), 'keyless', manifest['engineSha'], manifest['gymSha'], manifest['recordingSchemaVersion'])
        require(selector['kind'] == 'manual-luna-v1', 'unknown_active_profile')
        profile, _, _ = load_bundle(selector['runtimeId'])
        return RuntimeSnapshot(selector['runtimeId'], selector['sequence'], selector['kind'], profile['engineSha'], profile['gymSha'], profile['recordingSchemaVersion'])

    def assert_no_unfinished_transaction(self):
        self._locked(); self.base.assert_no_unfinished_transaction()
        path = STATE / 'transaction.json'
        if path.exists(): require(root_json(path).get('phase') == 'completed', 'manual_transaction_unfinished')

    def verify_snapshot(self, snapshot):
        self._locked()
        if snapshot.kind == 'keyless':
            path, manifest = self.native.current_release()
            require(manifest['releaseId'] == snapshot.runtime_id, 'keyless_snapshot_changed')
            self.native.verify_installed_release(path, manifest)
            self.base.smoke_closed(manifest)  # Read-only artifact smoke; no storage/drain helper.
        else:
            profile, receipt, approval = load_bundle(snapshot.runtime_id)
            # A previously activated exact immutable tuple does not expire at24h
            # on boot/rollback. Freshness is required only for a NEW promotion.
            validated = precredential_validate(profile, receipt, approval,
                previous_id=approval['previousRuntimeId'], sequence=approval['sequence'], now=receipt['observedUnix'])
            require(validated.runtime_id == snapshot.runtime_id, 'manual_snapshot_changed')
        volume = os.statvfs('/var/lib/commander-gym')
        require(volume.f_bavail * volume.f_frsize >= self.policy['minFreeBytes'], 'storage_reserve')

    def status(self): return self.base.status()
    def drain(self, boot_id):
        self.base.drain(boot_id)  # Legacy waits for acknowledgement, returns initial reply.
        return self.base.status()  # Return the actual fresh recording drain barrier.

    def _service(self, verb, unit):
        require(verb in ('start', 'stop') and unit in UNITS, 'fixed_service_operation')
        if verb == 'stop' and unit == 'argentum-luna-sidecar.service':
            load = subprocess.run(['/usr/bin/systemctl', 'show', unit, '-p', 'LoadState', '--value'],
                                  text=True, capture_output=True, timeout=10, check=False)
            if load.stdout.strip() == 'not-found': return
        subprocess.run(['/usr/bin/systemctl', verb, unit], check=True, timeout=150,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _reload(self):
        subprocess.run(['/usr/bin/systemctl', 'daemon-reload'], check=True, timeout=30,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _clear_gate(self):
        if GATE.exists() or GATE.is_symlink():
            protected_bytes(GATE, uid=0, max_bytes=4096)
            GATE.unlink()

    def begin(self, previous, candidate):
        self._locked(); self._clear_gate()
        bank = STATE / ('rollback-' + candidate.runtime_id)
        require(not bank.exists(), 'rollback_bank_exists')
        bank.mkdir(mode=0o700)
        entries = {}
        for index, path in enumerate(TARGETS.values()):
            if path.exists() or path.is_symlink():
                raw = protected_bytes(path, uid=0, max_bytes=1024*1024)
                name = str(index) + '.original'
                atomic_root(bank / name, raw)
                entries[str(path)] = {'original': name, 'sha256': hashlib.sha256(raw).hexdigest(), 'mode': stat.S_IMODE(path.stat().st_mode)}
            else: entries[str(path)] = {'original': None}
        atomic_root(bank / 'bank.json', canonical(entries))
        self.pending = {'previous': previous, 'candidate': candidate, 'bank': bank}
        atomic_root(STATE / 'transaction.json', canonical({'phase': 'activation-intent', 'previousRuntimeId': previous.runtime_id, 'candidateRuntimeId': candidate.runtime_id, 'sequence': candidate.sequence, 'bankSha256': digest(entries)}))

    def stop_closure(self):
        self._locked(); require(self.pending is not None, 'transaction_required')
        # Exact stopped generation is latched BEFORE any stop. A recent previous
        # boot/proof cannot satisfy reconciliation for a failing candidate.
        snapshot = self.current()
        state = self.status()
        from .manual_runtime_rollout import idle_state
        boot = idle_state(state, snapshot, now=time.time(), closed=True)
        root = Path(self.anchor['paths']['recordingRoot'])
        inventory = capture_inventory(root, uid=self.anchor['identities']['nativeUid'])
        state = self.status()
        require(state.get('bootId') == boot, 'stop_boot_changed')
        context = stop_context(snapshot, state, inventory, nonce=uuid.uuid4().hex, now=time.time())
        self.pending['stopContext'] = context
        self.pending['stopInventory'] = inventory
        atomic_root(STATE / 'stop-request.json', canonical(context))
        self._clear_gate()
        for unit in UNITS: self._service('stop', unit)

    def quiescence_barrier(self):
        self._locked()
        for unit in UNITS:
            if unit == 'argentum-luna-sidecar.service':
                load = subprocess.run(['/usr/bin/systemctl', 'show', unit, '-p', 'LoadState', '--value'],
                                      text=True, capture_output=True, timeout=10, check=False)
                if load.stdout.strip() == 'not-found': continue
            result = subprocess.check_output(['/usr/bin/systemctl', 'show', unit, '-p', 'ActiveState', '-p', 'MainPID', '-p', 'ControlPID'], text=True, timeout=10)
            fields = dict(row.split('=', 1) for row in result.splitlines())
            if fields.get('ActiveState') not in ('inactive', 'failed') or fields.get('MainPID') != '0' or fields.get('ControlPID') != '0': return False
        # Dead processes alone are insufficient. Exact fresh drained health and
        # byte-identical captures span this stop generation; changed bytes hold.
        require(self.pending is not None and 'stopContext' in self.pending, 'stop_context_required')
        context = self.pending['stopContext']
        require(root_json(STATE / 'stop-request.json') == context, 'stop_request_changed')
        inventory = capture_inventory(Path(self.anchor['paths']['recordingRoot']), uid=self.anchor['identities']['nativeUid'])
        if inventory != self.pending['stopInventory']: return False
        proof = {**context, 'reconciled': True, 'pendingRecordWrites': 0, 'verifiedUnix': time.time()}
        atomic_root(STATE / ('recorder-quiescence-' + context['stopNonce'] + '.json'), canonical(proof))
        return matches_stop_proof(root_json(STATE / ('recorder-quiescence-' + context['stopNonce'] + '.json')), context, now=time.time())

    def _select_files(self, runtime):
        profile = runtime.profile
        artifact = profile['artifacts']['config']
        require((set(TARGETS) - {'profile.json'}).issubset(artifact['files']), 'service_config_incomplete')
        source = Path(artifact['root'])
        for name, destination in TARGETS.items():
            if not destination.parent.exists():
                _directory(destination.parent.parent); destination.parent.mkdir(mode=0o755)
            raw = runtime.profile_bytes if name == 'profile.json' else protected_bytes(source / name, uid=0, max_bytes=1024*1024)
            atomic_root(destination, raw, mode=0o600 if name == 'profile.json' else 0o644)

    def _restore_files(self):
        require(self.pending is not None, 'missing_pending_transaction')
        bank = self.pending['bank']; entries = root_json(bank / 'bank.json')
        transaction = root_json(STATE / 'transaction.json')
        require(digest(entries) == transaction['bankSha256'], 'rollback_bank_changed')
        candidate = self.pending['candidate'].profile['artifacts']['config']
        candidate_files = {**candidate['files'], 'profile.json': hashlib.sha256(self.pending['candidate'].profile_bytes).hexdigest()}
        for name, destination in TARGETS.items():
            entry = entries[str(destination)]
            if destination.exists() or destination.is_symlink():
                actual = hashlib.sha256(protected_bytes(destination, uid=0, max_bytes=1024*1024)).hexdigest()
                require(actual in (entry.get('sha256'), candidate_files.get(name)), 'foreign_runtime_config')
            if entry['original'] is None:
                if destination.exists(): destination.unlink()
            else:
                raw = protected_bytes(bank / entry['original'], uid=0, max_bytes=1024*1024)
                require(hashlib.sha256(raw).hexdigest() == entry['sha256'], 'rollback_original_changed')
                atomic_root(destination, raw, mode=entry['mode'])

    def select(self, runtime):
        self._locked(); require(self.pending is not None, 'transaction_required')
        if isinstance(runtime, ValidatedRuntime):
            self._select_files(runtime)
            selector = {'kind': 'manual-luna-v1', 'runtimeId': runtime.runtime_id, 'sequence': runtime.sequence, 'previousRuntimeId': self.pending['previous'].runtime_id}
        else:
            self._restore_files()
            selector = {'kind': runtime.kind, 'runtimeId': runtime.runtime_id, 'sequence': self.pending['candidate'].sequence, 'previousRuntimeId': runtime.runtime_id}
        atomic_root(CONFIG / 'current.json', canonical(selector)); self._reload()

    def start_closed(self):
        self._locked()
        snapshot = self.current()
        if snapshot.kind == 'manual-luna-v1':
            profile, receipt, approval = load_bundle(snapshot.runtime_id)
            runtime = precredential_validate(profile, receipt, approval,
                previous_id=approval['previousRuntimeId'], sequence=approval['sequence'], now=receipt['observedUnix'])
            require(runtime.runtime_id == snapshot.runtime_id, 'precredential_identity')
            self._attest_precredential(snapshot)
            # No key source opened here; only after this gate can systemd start
            # the separately approved credential-bearing sidecar unit.
            for unit in ('argentum-recorder.service', 'argentum-luna-sidecar.service', 'argentum-play.service', 'argentum-web.service'):
                self._service('start', unit)
        else:
            for unit in ('argentum-recorder.service', 'argentum-play.service', 'argentum-web.service'): self._service('start', unit)

    def complete(self, runtime):
        self._locked()
        record = root_json(STATE / 'transaction.json')
        require(record['candidateRuntimeId'] == runtime.runtime_id and record['sequence'] == runtime.sequence, 'commit_identity')
        atomic_root(STATE / 'transaction.json', canonical({**record, 'phase': 'completed', 'outcome': 'manual-promoted'}))

    def complete_rollback(self, previous, consumed_sequence):
        self._locked()
        record = root_json(STATE / 'transaction.json')
        require(record['previousRuntimeId'] == previous.runtime_id and record['sequence'] == consumed_sequence, 'rollback_identity')
        atomic_root(STATE / 'transaction.json', canonical({**record, 'phase': 'completed', 'outcome': 'previous-restored'}))

    def resume(self, boot_id, runtime_id):
        self._locked()
        reply = self.base.rpc('resume', bootId=boot_id, releaseId=runtime_id)
        require(reply.get('bootId') == boot_id and reply.get('releaseId') == runtime_id and reply.get('acceptingNewGames') is True, 'resume_unconfirmed')

    def held(self, code):
        self._locked()
        record = root_json(STATE / 'transaction.json')
        atomic_root(STATE / 'transaction.json', canonical({**record, 'phase': 'operator-review', 'reason': code}))

    def delegate_keyless_update(self):
        self._locked(); require(self.current().kind == 'keyless', 'manual_cannot_delegate')
        result = locked_legacy_call(self.legacy, 'update', (), held_lock_probe=lambda: self.locked)
        current = self.current()
        atomic_root(CONFIG / 'current.json', canonical({'kind': 'keyless', 'runtimeId': current.runtime_id, 'sequence': current.sequence, 'previousRuntimeId': current.runtime_id}))
        return result

    def run_source(self):
        self._locked(); self.assert_no_unfinished_transaction()
        snapshot = self.current(); self.verify_snapshot(snapshot)
        if snapshot.kind != 'keyless':
            require(snapshot.kind == 'manual-luna-v1', 'unknown_active_profile')
            return {'state': 'held-manual-profile', 'runtimeId': snapshot.runtime_id}
        module = importlib.import_module('source_feeder')
        # Preserve its separate serialized feeder lock while borrowing the held
        # rollout lock. No helper hash/index is rewritten.
        path = Path('/etc/argentum-source/feeder.lock')
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_mode & 0o077 == 0, 'feeder_lock_custody')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = locked_legacy_call(module, 'feeder', (), held_lock_probe=lambda: self.locked)
            current = self.current()
            atomic_root(CONFIG / 'current.json', canonical({'kind': 'keyless', 'runtimeId': current.runtime_id,
                        'sequence': current.sequence, 'previousRuntimeId': current.runtime_id}))
            return result
        finally: os.close(fd)

    def _attest_precredential(self, snapshot):
        require(snapshot.kind == 'manual-luna-v1', 'manual_preflight_only')
        profile, receipt, approval = load_bundle(snapshot.runtime_id)
        runtime = precredential_validate(profile, receipt, approval,
            previous_id=approval['previousRuntimeId'], sequence=approval['sequence'], now=receipt['observedUnix'])
        require(runtime.runtime_id == snapshot.runtime_id, 'precredential_identity')
        _directory(GATE.parent)
        atomic_root(GATE, canonical({'runtimeId': snapshot.runtime_id, 'sequence': snapshot.sequence,
                                    'bootId': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}), mode=0o644)

    def ensure_preflight(self):
        # A promotion already holds the rollout lock when systemd follows unit
        # dependencies. Recheck its existing root attestation without reacquiring
        # that lock; on cold boot /run is empty and the normal lock is required.
        if GATE.exists():
            snapshot = self.current()
            self.verify_snapshot_unlocked(snapshot)
            gate = root_json(GATE)
            require(gate == {'runtimeId': snapshot.runtime_id, 'sequence': snapshot.sequence,
                             'bootId': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}, 'preflight_gate_changed')
            return {'state': 'manual-precredential-rechecked', 'runtimeId': snapshot.runtime_id}
        with self.exclusive_lock(): return self.preflight_current()

    def verify_snapshot_unlocked(self, snapshot):
        require(snapshot.kind == 'manual-luna-v1', 'manual_preflight_only')
        profile, receipt, approval = load_bundle(snapshot.runtime_id)
        runtime = precredential_validate(profile, receipt, approval,
            previous_id=approval['previousRuntimeId'], sequence=approval['sequence'], now=receipt['observedUnix'])
        require(runtime.runtime_id == snapshot.runtime_id, 'precredential_identity')

    def preflight_current(self):
        self._locked(); self.assert_no_unfinished_transaction()
        snapshot = self.current(); self.verify_snapshot(snapshot)
        require(snapshot.kind == 'manual-luna-v1', 'manual_preflight_only')
        self._attest_precredential(snapshot)
        return {'state': 'manual-precredential-validated', 'runtimeId': snapshot.runtime_id}

    def admit_current(self):
        self._locked(); self.assert_no_unfinished_transaction()
        snapshot = self.current(); self.verify_snapshot(snapshot)
        if snapshot.kind == 'keyless':
            return locked_legacy_call(self.native, 'admit', (), held_lock_probe=lambda: self.locked)
        from .manual_runtime_rollout import idle_state
        state = self.status()
        boot = idle_state(state, snapshot, now=time.time(), closed=False)
        if state['acceptingNewGames']:
            return {'state': 'already-open', 'runtimeId': snapshot.runtime_id}
        boot = idle_state(state, snapshot, now=time.time(), closed=True)
        self.resume(boot, snapshot.runtime_id)
        return {'state': 'admitted-current', 'runtimeId': snapshot.runtime_id}
