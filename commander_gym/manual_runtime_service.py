"""Service-role checks against a root precredential attestation.

The context credential contains no secret values. Root must attest the complete
bundle before systemd loads any provider/token credential. A native/recorder
process need not read the sidecar's private catalog: root verified it, and the
sidecar rechecks it. There is no paid launch using a QA context.
"""
from __future__ import annotations

import os
from pathlib import Path

from .manual_runtime_launch import _credential, dependency_lock
from .manual_runtime_profile import (
    ROLES, ValidatedRuntime, decode, digest, protected_bytes, require,
    validate_profile, verify_artifacts,
)

GATE = Path('/run/argentum-luna-runtime/precredential.json')


def service_runtime(role: str, credential_directory: Path, *, boot_id: str) -> ValidatedRuntime:
    require(role in ('native', 'sidecar', 'recorder'), 'fixed_service_role')
    # This root-owned boot-specific attestation is inspected before even opening
    # the nonsecret context credential. No provider source/token is read here.
    gate = decode(protected_bytes(GATE, uid=0, max_bytes=4096))
    require(set(gate) == {'runtimeId', 'sequence', 'bootId'} and gate['bootId'] == boot_id
            and type(gate['sequence']) is int and gate['sequence'] >= 1, 'precredential_gate')
    raw = _credential(credential_directory, 'profile.json', uid=os.getuid(), limit=4*1024*1024)
    profile = decode(raw.encode())
    validate_profile(profile)
    require(profile['purpose'] == 'native-manual-play' and gate['runtimeId'] == digest(profile), 'attested_profile')
    uid_field = 'sidecarUid' if role == 'sidecar' else 'nativeUid'
    require(profile['identities'][uid_field] == os.getuid(), 'service_role_identity')
    required = {'native': {'server', 'adapter', 'dependencies', 'launcher'},
                'sidecar': {'sidecar', 'dependencies', 'launcher', 'catalog'},
                'recorder': {'recorder', 'sidecar', 'dependencies', 'launcher', 'catalog'}}
    verify_artifacts(profile, roles=frozenset(required[role]))
    dependency_lock(profile)
    return ValidatedRuntime(raw.encode(), gate['runtimeId'], gate['sequence'])


def sealed_python_paths(runtime: ValidatedRuntime) -> tuple[str, ...]:
    profile = runtime.profile
    root = Path(profile['artifacts']['dependencies']['root'])
    lock = dependency_lock(profile)
    return (profile['artifacts']['sidecar']['root'], *[str(root / path) for path in lock['pythonPath']])


def verify_running_python(runtime: ValidatedRuntime, executable: str, base_prefix: str):
    profile = runtime.profile
    root = Path(profile['artifacts']['dependencies']['root'])
    lock = dependency_lock(profile)
    expected = root / lock['python']
    require(Path(executable).resolve() == expected.resolve(), 'unsealed_python_interpreter')
    require(Path(base_prefix).resolve().is_relative_to(root.resolve()), 'unsealed_python_stdlib')


def verify_dependency_runtime(runtime: ValidatedRuntime):
    """Inspect the sealed installed distribution metadata without constructing clients."""
    import importlib.metadata
    import sys
    verify_running_python(runtime, sys.executable, sys.base_prefix)
    profile = runtime.profile
    root = Path(profile['artifacts']['dependencies']['root'])
    lock = dependency_lock(profile)
    paths = [str(root / name) for name in lock['pythonPath']]
    installed = {}
    for distribution in importlib.metadata.distributions(path=paths):
        name = distribution.metadata['Name'].lower().replace('_', '-')
        require(name not in installed, 'duplicate_dependency_distribution')
        installed[name] = distribution.version
    require(installed == {name: row['version'] for name, row in lock['packages'].items()}, 'installed_dependency_lock_mismatch')
    import openai
    require(openai.__version__ == lock['packages']['openai']['version']
            and Path(openai.__file__).resolve().is_relative_to(root.resolve()), 'sdk_runtime_mismatch')
