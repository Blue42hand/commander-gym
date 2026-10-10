"""Root-authenticated, credential-free entries for the isolated QA guest.

No production launcher, provider constructor or transaction lock is used. The
controller holds the transaction lock while starting these children. Root checks
the selected bank and copies its authenticated launch message through an inherited
pipe to a byte-sealed, unprivileged launcher. This module never starts a game.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import fcntl
import os
from pathlib import Path
import pwd
import signal
import stat
import threading
import time

from .manual_runtime_profile import (
    ValidatedRuntime, canonical, decode, digest, protected_bytes, require,
    validate_profile, verify_artifacts,
)
from .manual_runtime_rollout import RuntimeSnapshot

ROLES = ('recorder', 'sidecar', 'native', 'proxy')
TOKEN = 'commander-gym-isolated-qa-placeholder-only'
MAX_MESSAGE = 4 * 1024 * 1024


def selected_runtime(authority, candidate, profile, selector, policy, *, now):
    """Pure selected-state check; authority freshness/closure is checked by root."""
    validate_profile(profile)
    require(profile['purpose'] == 'qualification', 'qa_role_purpose')
    require(authority['issuedUnix'] <= now <= authority['expiresUnix'], 'qa_role_expired')
    require(policy == dict(acceptingNewGames=False, nonce=authority['nonce'],
                           providerMode='fake', credentialSource='qa-placeholder-only'), 'qa_role_policy')
    runtime_id = digest(profile)
    require(runtime_id in (authority['previousProfileSha256'], digest(candidate)), 'qa_role_profile')
    require(type(selector.get('sequence')) is int and selector['sequence'] > 0, 'qa_role_sequence')
    expected = asdict(RuntimeSnapshot(runtime_id, selector['sequence'], 'manual-luna-v1',
        profile['engineSha'], profile['gymSha'], profile['recordingSchemaVersion']))
    require(selector == expected, 'qa_role_selector')
    if runtime_id == digest(candidate):
        require(selector['sequence'] == authority['sequence'], 'qa_role_candidate_sequence')
    else:
        require(selector['sequence'] in (authority['sequence'] - 1, authority['sequence']), 'qa_role_previous_sequence')
    return ValidatedRuntime(canonical(profile), runtime_id, selector['sequence'])


def authenticate(role):
    from .manual_runtime_qa import QAFileHost, ROOT
    require(role in ROLES, 'qa_role_name')
    host = QAFileHost()
    host._fresh()  # deliberately no exclusive_lock: parent owns it during startup
    require(not any(host.state.glob('*.next')), 'qa_role_pending_write')
    profile = host._json('service-profile.json')
    runtime = selected_runtime(host.authority, host.qa, profile, host._json('selector.json'),
                               host._json('start-policy.json'), now=time.time())
    if runtime.runtime_id == host.authority['previousProfileSha256']:
        from .manual_runtime_qa import validate_previous
        validate_previous(profile, host.qa)
    verify_artifacts(profile)
    # The child executes the new matched launcher artifact, never ambient code.
    launcher = profile['artifacts']['launcher']
    require('qa_role_child_entry.py' in launcher['files'], 'qa_role_child_not_sealed')
    for artifact_role in ('launcher', 'recorder', 'sidecar'):
        artifact = profile['artifacts'][artifact_role]
        name = 'commander_gym/manual_runtime_qa_roles.py'
        require(name in artifact['files'] and artifact['files'][name] ==
                hashlib.sha256(protected_bytes(Path(__file__), uid=0)).hexdigest(), 'qa_role_source_mismatch')
    require(Path(__file__).resolve() == ROOT/'implementation/commander_gym/manual_runtime_qa_roles.py', 'qa_role_import')
    return runtime


def child_spec(role, runtime):
    from .manual_runtime_launch import dependency_lock, LaunchSpec
    require(role in ROLES, 'qa_role_name')
    profile = runtime.profile
    lock = dependency_lock(profile)
    dependencies = Path(profile['artifacts']['dependencies']['root'])
    source_role = 'recorder' if role == 'recorder' else 'sidecar'
    source = profile['artifacts'][source_role]['root']
    paths = [source, *[str(dependencies/path) for path in lock['pythonPath']]]
    launcher = Path(profile['artifacts']['launcher']['root'])/'qa_role_child_entry.py'
    message = dict(role=role, profile=profile, runtimeId=runtime.runtime_id,
                   sequence=runtime.sequence, importPaths=paths)
    return LaunchSpec((str(dependencies/lock['python']), '-I', '-S', '-B', str(launcher)),
                      {'PATH':'/usr/bin:/bin', 'LANG':'C.UTF-8', 'PYTHONDONTWRITEBYTECODE':'1'}), message


def root_main(role):
    """Four fixed role entrypoints; executed only by sealed guest units as root."""
    runtime = authenticate(role)
    spec, message = child_spec(role, runtime)
    profile = runtime.profile
    identity = 'nativeUid' if role in ('native', 'recorder') else role+'Uid'
    account = pwd.getpwuid(profile['identities'][identity])
    sidecar_gid = pwd.getpwuid(profile['identities']['sidecarUid']).pw_gid
    groups = sorted({account.pw_gid, *([sidecar_gid] if role in ('native','recorder') else [])})
    message['sidecarGid'] = sidecar_gid
    raw = canonical(message)
    require(len(raw) <= MAX_MESSAGE, 'qa_role_message_size')
    # A regular private temporary file avoids a blocking pipe write before exec.
    # The descriptor is anonymous after unlink; no nonroot state copy persists.
    import tempfile
    with tempfile.TemporaryFile(dir='/opt/commander-gym-qa/qa/state') as stream:
        stream.write(raw); stream.flush(); stream.seek(0)
        reader = readonly_message_fd(stream.fileno())
        try: os.dup2(reader, 3, inheritable=True)
        finally: os.close(reader)
        os.setgroups(groups); os.setgid(account.pw_gid); os.setuid(account.pw_uid)
        os.umask(0o077)
        os.chdir('/')
        os.execve(spec.arguments[0], spec.arguments, spec.environment)


def read_child_message(fd=3):
    info = os.fstat(fd)
    require(fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY
            and stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 0
            and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= MAX_MESSAGE, 'qa_role_descriptor')
    with os.fdopen(fd, 'rb') as stream:
        raw = stream.read(MAX_MESSAGE + 1)
    require(len(raw) <= MAX_MESSAGE, 'qa_role_message_size')
    return decode(raw)


def readonly_message_fd(writer):
    """Reopen our anonymous Linux inode read-only, verifying descriptor identity."""
    before = os.fstat(writer)
    reader = os.open('/proc/self/fd/'+str(writer), os.O_RDONLY|os.O_CLOEXEC)
    try:
        after = os.fstat(reader)
        require((before.st_dev,before.st_ino,before.st_size) ==
                (after.st_dev,after.st_ino,after.st_size)
                and fcntl.fcntl(reader,fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY,
                'qa_role_descriptor_identity')
    except BaseException:
        os.close(reader); raise
    return reader


def run_child(message):
    require(set(message) == {'role','profile','runtimeId','sequence','importPaths','sidecarGid'}, 'qa_role_message')
    role, profile = message['role'], message['profile']
    require(role in ROLES, 'qa_role_name')
    validate_profile(profile)
    require(profile['purpose'] == 'qualification' and digest(profile) == message['runtimeId'], 'qa_role_child_profile')
    identity = 'nativeUid' if role in ('native', 'recorder') else role+'Uid'
    require(os.getuid() == os.geteuid() == profile['identities'][identity], 'qa_role_child_uid')
    # Root selected paths. Do not retain provider/credential values from caller env.
    require(not any('OPENAI' in k or 'TOKEN' in k or 'CREDENTIAL' in k for k in os.environ), 'qa_role_child_environment')
    runtime = ValidatedRuntime(canonical(profile), message['runtimeId'], message['sequence'])
    stopping = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopping.set())
    if role == 'recorder':
        from .manual_runtime_recorder import run_recorder
        from .native_game_capture import NativeGameCapture
        require(message['sidecarGid'] in os.getgroups(), 'qa_role_recorder_group')
        def qa_capture(root, pins, **kwargs):
            # The selected catalog still declares the production Pilot identity;
            # these pins describe the actual fixture execution, not a model call.
            pins = {**pins, 'models': {'provider':'qa-native-legal-fixture','model':'none',
                                     'configured':pins['models']},
                    'config': {**pins['config'], 'qaExecution': {
                        'providerMode':'fake','credentialSource':'qa-placeholder-only'}}}
            return NativeGameCapture(root, pins, **kwargs)
        run_recorder(runtime, sidecar_gid=message['sidecarGid'], stopping=stopping.is_set,
                     capture_factory=qa_capture)
    elif role == 'sidecar':
        from .game_server_binding_openai_sidecar import BindingOpenAIGameServerConfig, build_binding_openai_game_server_sidecar
        from .game_server_openai_sidecar import OpenAIGameServerSidecarConfig
        from .recorder_bridge import RemoteCaptureSink
        from .qa_legal_responses import NativeLegalFixtureClient
        catalog = Path(profile['artifacts']['catalog']['root'])
        config = BindingOpenAIGameServerConfig(OpenAIGameServerSidecarConfig(TOKEN, 'qa-no-provider',
            timeout=90, max_attempts=2), catalog/profile['catalog']['roster'], catalog, manual_uncapped=True)
        capture = RemoteCaptureSink(Path(profile['paths']['recorderSocket']).parent/'sidecar.sock',
                                    recorder_uid=profile['identities']['nativeUid'])
        server = build_binding_openai_game_server_sidecar(config, client=NativeLegalFixtureClient(), game_capture=capture)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            while thread.is_alive() and not stopping.wait(0.2): pass
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=10)
    elif role == 'native':
        from .manual_runtime_launch import native_spec
        from .manual_runtime_qa import ROOT
        config = ROOT/'qa/native/configtree'
        _private_role_directory(config, os.getuid())
        require(set(p.name for p in config.iterdir()) <= {'commander-gym.sidecar.token'}, 'qa_role_configtree_contents')
        token_path = config/'commander-gym.sidecar.token'
        _literal_file(token_path, TOKEN.encode())
        spec = native_spec(runtime, credential_directory=config)
        from dataclasses import replace
        spec = replace(spec, arguments=(*spec.arguments, "--native.qa.callback-gate-enabled=true"))
        os.execve(spec.arguments[0], spec.arguments, spec.environment)
    else:
        from .qa_proxy_launch import proxy_spec
        spec = proxy_spec(runtime)
        os.execve(spec.arguments[0], spec.arguments, spec.environment)


def _private_role_directory(path, uid):
    require(path.is_absolute() and path.is_relative_to(Path('/opt/commander-gym-qa/qa')), 'qa_role_path')
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == uid and stat.S_IMODE(info.st_mode) == 0o700, 'qa_role_directory')


def _literal_file(path, raw):
    if path.exists() or path.is_symlink():
        require(protected_bytes(path, uid=os.getuid(), max_bytes=1024*1024) == raw
                and stat.S_IMODE(path.lstat().st_mode) == 0o600, 'qa_role_literal_changed')
        return
    fd = os.open(path, os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd,'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
