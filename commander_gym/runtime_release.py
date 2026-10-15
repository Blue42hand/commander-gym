"""Pinned release transport and idle operations. No builds or provider calls."""
from __future__ import annotations

import hashlib
import grp
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import time

from .manual_runtime_profile import (
    ManualRuntimeError, ValidatedRuntime, canonical, decode, digest, relative_path,
    require, validate_profile, validate_qualification, validate_approval,
)
from .manual_runtime_rollout import COUNTERS, _promote, idle_state, promote

MAX_BUNDLE_BYTES = 8 * 1024**3


def file_hash(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def data_file(root, name):
    path = root / relative_path(name)
    require(not root.is_symlink() and root.is_dir(), 'bundle_root')
    for entry in (path, *list(path.parents)[:len(path.relative_to(root).parts)-1]):
        require(not entry.is_symlink(), 'bundle_symlink')
    require(path.is_file(), 'bundle_file')
    return path


@contextmanager
def open_data(root, name):
    """Pin each untrusted path component while copying a reviewed file."""
    parts = relative_path(name).split('/')
    descriptors = []
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW); descriptors.append(fd)
        for part in parts[:-1]:
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            descriptors.append(fd)
        leaf = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(leaf, 'rb') as stream:
            require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), 'bundle_regular_file')
            yield stream
    finally:
        for fd in reversed(descriptors): os.close(fd)


def inspect_bundle(root: Path, expected_sha: str, *, now=None):
    """Verify a review-pinned manifest and every byte before privileged staging."""
    root = Path(root).absolute()
    manifest_path = data_file(root, 'release.json')
    require(manifest_path.stat().st_size <= 32*1024*1024, 'bundle_manifest_size')
    raw = manifest_path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == expected_sha, 'bundle_review_digest')
    manifest = decode(raw)
    require(set(manifest) == {'schemaVersion', 'platform', 'profile', 'qualification', 'approval', 'files'}, 'bundle_fields')
    require(manifest['schemaVersion'] == 1 and manifest['platform'] == 'linux-x86_64', 'bundle_platform')
    profile, receipt, approval = (manifest[k] for k in ('profile', 'qualification', 'approval'))
    validate_profile(profile)
    require(profile['purpose'] == 'native-manual-play', 'production_bundle_required')
    validate_qualification(profile, receipt, now=time.time() if now is None else now)
    validate_approval(profile, receipt, approval, previous_id=approval['previousRuntimeId'], sequence=approval['sequence'])
    expected = {'artifacts/' + role + '/' + name: sha
                for role, artifact in profile['artifacts'].items() for name, sha in artifact['files'].items()}
    expected['recording-registry.json'] = profile['recordingAuthority']['registrySha256']
    require(type(manifest['files']) is dict and set(manifest['files']) == set(expected), 'bundle_inventory')
    total = 0
    for name, sha in expected.items():
        row = manifest['files'][name]
        require(type(row) is dict and set(row) == {'sha256', 'executable'} and row['sha256'] == sha
                and type(row['executable']) is bool, 'bundle_file_metadata')
        path = data_file(root, name)
        total += path.stat().st_size
        require(total <= MAX_BUNDLE_BYTES and file_hash(path) == sha, 'bundle_file_digest')
    return manifest


def pack_bundle(profile, qualification, approval, destination):
    """Create a private directory bundle from already qualified installed artifacts."""
    validate_profile(profile)
    destination = Path(destination)
    require(not destination.exists(), 'bundle_destination_exists')
    destination.mkdir(mode=0o700, parents=True)
    files = {}
    sources = {'artifacts/' + role + '/' + name: (Path(artifact['root']) / name, sha)
               for role, artifact in profile['artifacts'].items() for name, sha in artifact['files'].items()}
    sources['recording-registry.json'] = (Path(profile['paths']['registryPath']), profile['recordingAuthority']['registrySha256'])
    try:
        for name, (source, sha) in sources.items():
            require(source.is_file() and not source.is_symlink() and file_hash(source) == sha, 'source_digest')
            target = destination / name; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            require(file_hash(target) == sha, 'copy_digest')
            executable = bool(source.stat().st_mode & 0o111)
            target.chmod(0o700 if executable else 0o600)
            files[name] = {'sha256': sha, 'executable': executable}
        raw = canonical({'schemaVersion': 1, 'platform': 'linux-x86_64', 'profile': profile,
                         'qualification': qualification, 'approval': approval, 'files': files})
        (destination / 'release.json').write_bytes(raw)
        return hashlib.sha256(raw).hexdigest()
    except Exception:
        # Leave the incomplete directory for inspection; no valid release manifest.
        raise


def stage_bundle(root, manifest):
    """Stage root-owned immutable trees; never overwrite a different installed tree."""
    from .manual_runtime_host import CONFIG, _directory, atomic_root
    from .manual_runtime_profile import protected_bytes, verify_artifacts
    root = Path(root)
    needed = sum(data_file(root, 'artifacts/' + role + '/' + name).stat().st_size
                 for role, artifact in manifest['profile']['artifacts'].items()
                 if not Path(artifact['root']).exists() for name in artifact['files'])
    volume = os.statvfs('/srv')
    require(volume.f_bavail * volume.f_frsize >= needed + 32*1024**3, 'staging_storage_reserve')
    for role, artifact in manifest['profile']['artifacts'].items():
        destination = Path(artifact['root'])
        if destination.exists():
            verify_artifacts(manifest['profile'], roles=frozenset({role})); continue
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        _directory(destination.parent)
        stage = Path(tempfile.mkdtemp(prefix='.install-', dir=destination.parent))
        try:
            for name, sha in artifact['files'].items():
                key = 'artifacts/' + role + '/' + name
                source = data_file(root, key)
                target = stage / name; target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                with open_data(root, key) as inp, target.open('xb') as out:
                    copied = 0
                    while chunk := inp.read(1024*1024):
                        copied += len(chunk)
                        require(copied <= MAX_BUNDLE_BYTES, 'staging_size')
                        out.write(chunk)
                    out.flush(); os.fsync(out.fileno())
                require(file_hash(target) == sha, 'staging_digest')
                target.chmod(0o555 if manifest['files'][key]['executable'] else 0o444)
            if role == 'catalog':
                group = grp.getgrnam('commander-prod').gr_gid
                for path in [stage, *stage.rglob('*')]:
                    os.chown(path, 0, group)
                    path.chmod(0o750 if path.is_dir() else 0o440)
            else: stage.chmod(0o755)
            os.rename(stage, destination)
            fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(fd)
            finally: os.close(fd)
        finally:
            if stage.exists(): shutil.rmtree(stage)
    profile = manifest['profile']
    registry = Path(profile['paths']['registryPath'])
    if registry.exists():
        require(hashlib.sha256(protected_bytes(registry, uid=0)).hexdigest() == profile['recordingAuthority']['registrySha256'], 'recording_registry_changed')
    else:
        registry.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        with open_data(root, 'recording-registry.json') as stream: raw = stream.read(4*1024*1024+1)
        require(len(raw) <= 4*1024*1024 and hashlib.sha256(raw).hexdigest() == profile['recordingAuthority']['registrySha256'], 'recording_registry_changed')
        # The real recorder rejects world-readable historical authority files.
        # Its effective group is commander-prod, including on a fresh host where
        # the numeric GID is chosen by account creation rather than Tolaria's GID.
        atomic_root(registry, raw, mode=0o640)
        os.chown(registry, 0, grp.getgrnam('commander-prod').gr_gid)
    bank = CONFIG / 'runtimes' / digest(profile)
    bank.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in ('profile', 'qualification', 'approval'):
        path = bank / (name + '.json'); raw = canonical(manifest[name])
        if path.exists(): require(protected_bytes(path, uid=0, max_bytes=32*1024*1024) == raw, 'installed_bundle_conflict')
        else: atomic_root(path, raw)
    verify_artifacts(profile)


def wait_and_promote(host, profile, receipt, approval, *, wait_seconds=0, clock=time.monotonic, sleep=time.sleep):
    """Wait without draining; the final locked transaction repeats the idle check."""
    require(type(wait_seconds) is int and 0 <= wait_seconds <= 86400, 'update_wait')
    deadline = clock() + wait_seconds
    while True:
        try: return promote(host, profile, receipt, approval)
        except ManualRuntimeError as error:
            if str(error) != 'not_idle' or clock() >= deadline: raise
            sleep(min(5, max(0, deadline-clock())))


def rollback(host, *, load_bundle):
    """Explicit idle rollback only to the recorded previous, previously approved tuple."""
    from .manual_runtime_host import CONFIG, root_json
    from .manual_runtime_profile import precredential_validate
    selector = root_json(CONFIG / 'current.json')
    target = selector['previousRuntimeId']
    require(target != selector['runtimeId'], 'no_previous_release')
    profile, receipt, approval = load_bundle(target)
    def validate(previous_id, sequence, now):
        # Re-read under the rollout lock; never approve an arbitrary old directory.
        current = root_json(CONFIG / 'current.json')
        require(current == selector and previous_id == selector['runtimeId'], 'rollback_selection_changed')
        require(host.current().recording_schema == profile['recordingSchemaVersion'], 'rollback_schema_incompatible')
        checked = precredential_validate(profile, receipt, approval, previous_id=approval['previousRuntimeId'],
                                         sequence=approval['sequence'], now=receipt['observedUnix'])
        return ValidatedRuntime(checked.profile_bytes, checked.runtime_id, sequence)
    return _promote(host, validate)


def diagnostics(host):
    """Allowlist operational metadata; never dump logs, environment, or private paths."""
    current = host.current()
    result = {'runtimeId': current.runtime_id, 'engineSha': current.engine_sha, 'gymSha': current.gym_sha,
              'sequence': current.sequence, 'recordingSchema': current.recording_schema}
    try:
        status = host.status()
        for field in (*COUNTERS, 'acceptingNewGames', 'recordingHealthy', 'recoveryComplete', 'drainAcknowledged'):
            value = status.get(field)
            if type(value) in (bool, int): result[field] = value
        from .manual_runtime_rollout import ready_state
        ready_state(status, current, now=time.time()); result['ready'] = True
    except (OSError, ValueError): result['ready'] = False
    from .manual_runtime_host import UNITS
    services = {}
    for unit in UNITS:
        try:
            response = subprocess.run(['/usr/bin/systemctl','show',unit,'-p','ActiveState','--value'],
                capture_output=True,text=True,timeout=5,check=True)
            state = response.stdout.strip()
            services[unit] = state if state in ('active','inactive','failed','activating','deactivating') else 'unknown'
        except (OSError, subprocess.SubprocessError): services[unit] = 'unknown'
    result['services'] = services
    result['ready'] = result['ready'] and all(state == 'active' for state in services.values())
    result['providerApi'] = 'not-probed'
    try:
        from .manual_runtime_host import root_json, load_bundle
        profile = load_bundle(current.runtime_id)[0]
        if 'liveCatalog' in profile:
            row = root_json(Path(profile['liveCatalog']['snapshotRoot']) / 'status.json')
            state = row.get('state')
            if state in ('published','unchanged','catalog-update-rejected'):
                result['catalog'] = {'state':state}
    except (OSError, ValueError, KeyError): pass
    return result
