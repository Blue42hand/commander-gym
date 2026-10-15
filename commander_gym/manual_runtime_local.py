"""Explicit optional local role in the sealed manual runtime; never a cloud override."""
from dataclasses import asdict
import os
from pathlib import Path

from .manual_runtime_profile import canonical, decode, require, relative_path, protected_bytes
from .lmstudio_provider import LMStudioConfig

ARTIFACTS = frozenset({'localConfig', 'localCatalog'})
GATES = frozenset({'localProviderIsolation', 'localRecorderPeer', 'localFailureCancellation'})
UNIT = 'argentum-local-sidecar.service'


def validate_local(profile):
    local = profile['localInference']
    require(type(local) is dict and set(local) == {'uid', 'gid', 'port', 'config', 'catalog'}, 'local_fields')
    require(type(local['uid']) is int and local['uid'] > 0
            and local['uid'] not in profile['identities'].values(), 'local_distinct_identity')
    require(type(local['gid']) is int and local['gid'] > 0, 'local_group_identity')
    require(type(local['port']) is int and local['port'] == 8084, 'local_private_listener')
    try:
        config = LMStudioConfig(**local['config'])
    except (TypeError, ValueError):
        require(False, 'local_inference_config')
    require(asdict(config) == local['config'], 'local_config_explicit_defaults')
    # Config is part of both the runtime identity and an exact byte-sealed file.
    import hashlib
    require(profile['artifacts']['localConfig']['files'] == {
        'inference.json': hashlib.sha256(canonical(local['config'])).hexdigest()}, 'local_config_seal')
    catalog = local['catalog']
    require(type(catalog) is dict and set(catalog) == {'roster', 'manifest', 'bindings', 'pilotFingerprint', 'componentDigest'}, 'local_catalog_fields')
    for key in ('roster', 'manifest'):
        relative_path(catalog[key])
        require(catalog[key] in profile['artifacts']['localCatalog']['files'], 'local_catalog_seal')
    from .manual_runtime_profile import _sha
    require(type(catalog['bindings']) is dict and 1 <= len(catalog['bindings']) <= 256
            and all(type(k) is str and k and _sha(v) for k, v in catalog['bindings'].items()), 'local_binding_pins')
    require(not set(catalog['bindings']) & set(profile['catalog']['bindings']), 'local_binding_collision')
    require(_sha(catalog['pilotFingerprint']) and catalog['componentDigest'] == 'sha256:' + hashlib.sha256(canonical(local['config'])).hexdigest(),
            'local_component_pin')
    require({'scripts/local_sidecar_entry.py', 'commander_gym/manual_runtime_local.py',
             'commander_gym/lmstudio_provider.py', 'commander_gym/lmstudio_http_worker.py'}.issubset(profile['artifacts']['launcher']['files']),
            'local_entry_closure_not_sealed')
    roots = [Path(a['root']) for a in profile['artifacts'].values()]
    for role in ARTIFACTS:
        root = Path(profile['artifacts'][role]['root'])
        require(all(root != other and not root.is_relative_to(other) and not other.is_relative_to(root)
                    for other in roots if other != root)
                and sum(other == root for other in roots) == 1, 'local_artifact_overlap')


def local_recording_registry(profile):
    """Validate every canonical local identity without constructing a provider."""
    from .binding_catalog import load_binding_catalog
    from .game_server_bindings import GameServerBindingRegistry
    from .game_server_binding_lmstudio_sidecar import local_component_ref, LocalBindingComponentResolver
    from .pilot_composition import compose_pilot_runtime
    from .recorder_bridge import _RecordingPilot
    local = profile['localInference']; config = LMStudioConfig(**local['config'])
    artifact = profile['artifacts']['localCatalog']; root = Path(artifact['root']); catalog = local['catalog']
    manifest = decode(protected_bytes(root / catalog['manifest'], uid=artifact['uid']))
    require(type(manifest.get('files')) is dict and manifest['files']
            and all(artifact['files'].get(k) == v for k, v in manifest['files'].items()), 'local_manifest_seal')
    require(manifest.get('provider') == 'lmstudio' and manifest.get('model') == config.model
            and set(manifest.get('profiles', [])) == set(catalog['bindings']), 'local_manifest_identity')
    def pilot_factory(pilot, binding):
        require(pilot.generalist_base == local_component_ref(config) and pilot.escalation_provider is None
                and pilot.skill_set is None and not pilot.specialists, 'local_cloud_fallback_forbidden')
        # Resolve the exact registered deterministic/provider refs; construction makes no calls.
        compose_pilot_runtime(pilot, LocalBindingComponentResolver(config))
        return _RecordingPilot(pilot)
    loaded = load_binding_catalog(root / catalog['roster'], instance_root=root, pilot_factory=pilot_factory)
    registry = GameServerBindingRegistry(loaded.resolver, loaded.binding_ids)
    require(registry.binding_fingerprints == catalog['bindings']
            and registry.pilot_fingerprints == frozenset({catalog['pilotFingerprint']}), 'local_canonical_identity')
    return registry


class RecordingRegistries:
    """Native registration dispatches by disjoint pinned profile identity."""
    def __init__(self, cloud, local, local_ids):
        self.cloud, self.local, self.local_ids = cloud, local, frozenset(local_ids)
        require(not any(self._cloud_contains(key) for key in self.local_ids), 'local_binding_collision')

    def _cloud_contains(self, key):
        if hasattr(self.cloud, 'contains'): return self.cloud.contains(key)
        return key in self.cloud.binding_fingerprints

    def create_seat(self, player_id, profile_id, **kwargs):
        # Live Luna catalogs must not take ownership of a pinned local profile later.
        if profile_id in self.local_ids:
            require(not self._cloud_contains(profile_id), 'local_binding_collision')
            return self.local.create_seat(player_id, profile_id, **kwargs)
        return self.cloud.create_seat(player_id, profile_id, **kwargs)


def local_environment(runtime, *, credential_directory, uid, credential_reader=None):
    from .manual_runtime_credentials import credential
    from .manual_runtime_launch import dependency_lock
    from .manual_runtime_profile import verify_artifacts
    profile = runtime.profile
    require('localInference' in profile, 'local_role_disabled')
    verify_artifacts(profile, roles=frozenset({'sidecar', 'dependencies', 'launcher'}) | ARTIFACTS)
    require(uid == profile['localInference']['uid'], 'local_identity')
    local_recording_registry(profile)
    token = (credential_reader or credential)(credential_directory, 'commander-gym.local.token', uid=uid, limit=1024, role='local').strip()
    require(32 <= len(token) <= 512 and all(c.isascii() and (c.isalnum() or c in '._~-') for c in token), 'local_token_shape')
    root = Path(profile['artifacts']['dependencies']['root']); lock = dependency_lock(profile)
    return {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8',
            'PYTHONPATH': os.pathsep.join([profile['artifacts']['sidecar']['root'], *[str(root / p) for p in lock['pythonPath']]]),
            'PYTHONNOUSERSITE': '1', 'PYTHONSAFEPATH': '1', 'PYTHONDONTWRITEBYTECODE': '1',
            'COMMANDER_GYM_LOCAL_SIDECAR_TOKEN': token,
            'COMMANDER_GYM_RECORDER_SOCKET': str(local_ipc(profile) / 'local.sock'),
            'COMMANDER_GYM_RECORDER_UID': str(profile['identities']['nativeUid'])}


def local_arguments(profile):
    require('localInference' in profile, 'local_role_disabled')
    catalog = profile['artifacts']['localCatalog']['root']
    return ['--inference-config', str(Path(profile['artifacts']['localConfig']['root']) / 'inference.json'),
            '--catalog', str(Path(catalog) / profile['localInference']['catalog']['roster']),
            '--instance-root', catalog, '--port', str(profile['localInference']['port'])]


def local_ipc(profile):
    if profile['purpose'] == 'native-manual-play':
        return Path('/run/argentum-local-ipc')
    return Path(profile['paths']['recorderSocket']).parent / 'local-ipc'
