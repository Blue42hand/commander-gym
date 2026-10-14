"""Standalone Linux/systemd bootstrap for an exact reviewed runtime bundle."""
from __future__ import annotations
import grp
import os
from pathlib import Path
import platform
import pwd
import secrets
import shutil
import subprocess
import time

from .manual_runtime_profile import canonical, digest, precredential_validate, require
from .runtime_release import stage_bundle


def command(*args):
    subprocess.run(args, check=True, timeout=180, stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def accounts(profile, *, create=False):
    # Inspect every identity before creating any; never reassign existing accounts.
    missing = []
    for field, name in (('nativeUid', 'argentum-play'), ('sidecarUid', 'commander-prod'), ('proxyUid', 'argentum-web')):
        uid = profile['identities'][field]
        try:
            user = pwd.getpwnam(name)
            require(user.pw_uid == uid, 'service_uid_conflict')
        except KeyError:
            try: pwd.getpwuid(uid)
            except KeyError: missing.append((name, uid))
            else: require(False, 'service_uid_in_use')
    if create:
        for name, uid in missing:
            command('/usr/sbin/useradd', '--system', '--user-group', '--uid', str(uid),
                    '--no-create-home', '--home-dir', '/nonexistent', '--shell', '/usr/sbin/nologin', name)


def directory(path, uid=0, gid=0, mode=0o755):
    path = Path(path)
    if path.exists():
        info = path.lstat()
        require(path.is_dir() and not path.is_symlink() and info.st_uid == uid
                and info.st_gid == gid and info.st_mode & 0o022 == 0, 'install_directory_conflict')
    else:
        path.mkdir(parents=True, mode=mode); os.chown(path, uid, gid); path.chmod(mode)


def bootstrap_units(profile):
    root = profile['artifacts']['launcher']['root']
    deps = profile['artifacts']['dependencies']['root']
    python = deps + '/python/bin/python3.12'
    dispatch = python + ' -I -S -B ' + root + '/scripts/run_manual_runtime.py'
    common = '''NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
UMask=0077
Restart=on-failure
RestartSec=5
KillMode=control-group
SendSIGKILL=no
TimeoutStopSec=150
StandardOutput=null
StandardError=null
'''
    # Base units are installed only on a new host. Release-owned drop-ins override
    # entry points and exact credential declarations on every subsequent update.
    units = {
        'argentum-play.service': f'''[Unit]
Description=Private Argentum game server
After=network-online.target
Wants=network-online.target
[Service]
Type=exec
User=argentum-play
Group=argentum-play
WorkingDirectory=/var/lib/argentum-play
StateDirectory=argentum-play
StateDirectoryMode=0700
RuntimeDirectory=argentum-play
RuntimeDirectoryMode=0700
ExecStart={python} -I -S -B {root}/scripts/manual_service_entry.py native
{common}ReadWritePaths=/var/lib/commander-gym/runs/native /run/argentum-play
InaccessiblePaths=/srv/commander-runtime /var/lib/commander-gym/state /var/lib/commander-gym/imports
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
MemoryMax=8G
CPUQuota=200%
TasksMax=512
[Install]
WantedBy=multi-user.target
''',
        'argentum-web.service': f'''[Unit]
Description=Private Argentum web frontend
After=argentum-play.service
Wants=argentum-play.service
[Service]
Type=exec
User=argentum-web
Group=argentum-web
RuntimeDirectory=argentum-web
RuntimeDirectoryMode=0700
LogsDirectory=argentum-web
LogsDirectoryMode=0700
ExecStart={deps}/qa-proxy/nginx -c /etc/argentum-play/nginx.conf -g 'daemon off;'
{common}InaccessiblePaths=/var/lib/commander-gym /srv/commander-runtime
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
MemoryMax=256M
TasksMax=64
[Install]
WantedBy=multi-user.target
''',
        'argentum-admission.service': f'''[Unit]
Description=Restore admission after verified recovery
After=argentum-play.service
[Service]
Type=oneshot
ExecStart={dispatch} admit
TimeoutStartSec=660
UMask=0077
StandardOutput=null
StandardError=null
''',
        'argentum-admission.timer': '''[Unit]
Description=Keep healthy same-release admission available
[Timer]
OnBootSec=15
OnUnitInactiveSec=15
Unit=argentum-admission.service
[Install]
WantedBy=timers.target
''',
    }
    return units


def install(root, manifest):
    from .manual_runtime_host import CONFIG, STATE, ProtectedRuntimeHost, atomic_root, root_json
    require(platform.system() == 'Linux' and platform.machine() == 'x86_64' and os.geteuid() == 0, 'linux_x86_64_root_required')
    profile, receipt, approval = (manifest[k] for k in ('profile', 'qualification', 'approval'))
    require(approval['previousRuntimeId'] == '0'*64 and approval['sequence'] == 1, 'fresh_install_approval')
    runtime_id = digest(profile)
    journal = STATE / 'installation.json'
    continuing = journal.exists() and root_json(journal) == {'runtimeId': runtime_id, 'phase': 'prepared'}
    if (CONFIG / 'current.json').exists():
        require(continuing and root_json(CONFIG / 'current.json')['runtimeId'] == runtime_id,
                'already_installed_use_update')
    for name, text in bootstrap_units(profile).items():
        path = Path('/etc/systemd/system') / name
        if path.exists() or path.is_symlink():
            require(continuing and not path.is_symlink() and path.read_bytes() == text.encode(), 'existing_unit_requires_update')
    require(Path('/run/systemd/system').is_dir(), 'systemd_required')
    require(shutil.which('git') is not None, 'git_required')
    accounts(profile)
    require('liveCatalog' in profile and Path(profile['liveCatalog']['checkoutRoot']).is_dir(), 'binding_checkout_required')
    # Do not provision or inspect provider secret bytes. The operator places the
    # existing credential in its protected destination before activation.
    key = Path('/srv/commander-runtime/.config/commander-gym/openai.env')
    require(key.is_file() and not key.is_symlink() and key.stat().st_mode & 0o077 == 0, 'provider_credential_required')
    require('qa-proxy/nginx' in profile['artifacts']['dependencies']['files'], 'proxy_dependency_required')
    require(len(profile['ingress']['origins']) == 1, 'fresh_install_tailnet_https_required')
    require(shutil.which('tailscale') is not None, 'tailscale_https_required')
    directory(str(STATE))
    if journal.exists(): require(continuing, 'unfinished_installation')
    else: atomic_root(journal, canonical({'runtimeId': runtime_id, 'phase': 'prepared'}))
    accounts(profile, create=True)
    native = pwd.getpwnam('argentum-play'); sidecar = pwd.getpwnam('commander-prod')
    for path in (str(CONFIG), str(STATE), '/etc/argentum-play', '/run/argentum-luna-runtime', '/srv/argentum-luna/artifacts'):
        directory(path)
    directory('/var/lib/argentum-play', native.pw_uid, native.pw_gid, 0o700)
    for path in ('/var/lib/commander-gym', '/var/lib/commander-gym/runs', '/var/lib/commander-gym/state', '/var/lib/commander-gym/imports'):
        directory(path, sidecar.pw_uid, sidecar.pw_gid)
    directory(profile['paths']['recordingRoot'], native.pw_uid, native.pw_gid, 0o700)
    stage_bundle(root, manifest)
    checked = precredential_validate(profile, receipt, approval, previous_id='0'*64, sequence=1, now=time.time())
    # Schema 1 uses append-only recording files; no destructive migration exists.
    atomic_root(STATE / 'storage-schema.json', canonical({'version': 1, 'recordingSchema': 1}))
    atomic_root(CONFIG / 'operator-policy.json', canonical({'schemaVersion': 1, 'implementationApproved': True,
                'activationApproved': True, 'minFreeBytes': 32*1024**3}))
    atomic_root(CONFIG / 'anchor-profile.json', canonical(profile))
    if not (CONFIG / 'sidecar.token').exists():
        atomic_root(CONFIG / 'sidecar.token', secrets.token_urlsafe(48).encode())
    for name, text in bootstrap_units(profile).items():
        path = Path('/etc/systemd/system') / name
        atomic_root(path, text.encode(), mode=0o644)
    nginx = b'pid /run/argentum-web/nginx.pid;\nerror_log /dev/null;\nevents {}\nhttp { types { text/html html; text/css css; application/javascript js mjs; application/json json; image/svg+xml svg; image/png png; image/webp webp; font/woff2 woff2; } include /etc/argentum-play/argentum-play.nginx.conf; }\n'
    atomic_root(Path('/etc/argentum-play/nginx.conf'), nginx, mode=0o644)
    atomic_root(CONFIG / 'current.json', canonical({'kind': 'manual-luna-v1', 'runtimeId': checked.runtime_id,
                'sequence': 1, 'previousRuntimeId': checked.runtime_id}))
    # Configure private ingress before any game can be admitted. A failure leaves
    # an explicit prepared installation that the same command can resume.
    command(shutil.which('tailscale'), 'serve', '--bg', 'http://127.0.0.1:8180')
    host = ProtectedRuntimeHost(profile)
    with host.exclusive_lock():
        host._select_files(checked); host._reload(); host.verify_snapshot(host.current())
        host.start_closed()
        state = host.status()
        atomic_root(journal, canonical({'runtimeId': runtime_id, 'phase': 'completed'}))
        host.resume(state['bootId'], checked.runtime_id)
    command('/usr/bin/systemctl', 'enable', 'argentum-play.service', 'argentum-web.service')
    command('/usr/bin/systemctl', 'enable', '--now', 'argentum-admission.timer')
    return {'state': 'installed', 'runtimeId': checked.runtime_id}
