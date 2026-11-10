#!/usr/bin/env python3
"""Prepare fixed isolated services. Does not enable services, start games, or read keys.
Run as root with --release /opt/commander-gym-web/<commit> --gym-sha <commit>.
"""
import argparse
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys


def write(path,text,mode=0o600):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text);path.chmod(mode)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release',required=True,type=Path);parser.add_argument('--gym-sha',required=True)
    parser.add_argument('--local-catalog',type=Path,help='Prepared local catalog root, containing catalog.json and inference.json')
    args=parser.parse_args()
    if os.getuid()!=0:parser.error('Root required for service preparation')
    release=args.release.resolve(strict=True)
    if release.parent!=Path('/opt/commander-gym-web') or release.name!=args.gym_sha or len(args.gym_sha)!=40:parser.error('An immutable commit release is required')
    sys.path.insert(0,str(release))
    from commander_gym.recorder_bridge import recording_registry
    from commander_gym.manual_runtime_profile import recording_pins, verify_artifacts
    profile=json.loads(Path('/etc/argentum-luna/service-profile.json').read_text())
    # Validate pinned installed inputs without loading a credential.
    verify_artifacts(profile,roles=frozenset({'server','adapter','dependencies','catalog','sidecar','recorder','launcher'}))
    for name in ('commander-batch-engine','commander-batch-provider'):
        try:pwd.getpwnam(name)
        except KeyError:subprocess.run(['/usr/sbin/useradd','--system','--no-create-home','--shell','/usr/sbin/nologin',name],check=True)
    engine=pwd.getpwnam('commander-batch-engine');provider=pwd.getpwnam('commander-batch-provider');panel=pwd.getpwnam('commander-panel')
    root=Path('/var/lib/commander-gym-batches');root.mkdir(mode=0o700,exist_ok=True)
    (root/'captures').mkdir(mode=0o700,exist_ok=True);(root/'active').mkdir(mode=0o700,exist_ok=True)
    write('/etc/tmpfiles.d/commander-gym-batches.conf', 'd /run/commander-gym-batches 0755 root root -\nd /run/commander-gym-batches/panel 0755 root root -\n', 0o644)
    runtime=Path('/run/commander-gym-batches');runtime.mkdir(mode=0o755,exist_ok=True);runtime.chmod(0o755)
    catalog=Path(profile['artifacts']['catalog']['root']);deps=Path(profile['artifacts']['dependencies']['root'])
    lock=json.loads((deps/'runtime-lock.json').read_text());python=deps/lock['python'];java=deps/lock['java']
    registry=recording_registry(catalog/profile['catalog']['roster'],catalog)
    pins={'engine':profile['engineSha'],'gym':args.gym_sha,'bindings':profile['catalog']['bindings'],
          'pilot':profile['catalog']['pilotFingerprint'],'catalog':profile['artifacts']['catalog']['sha256'] if 'sha256' in profile['artifacts']['catalog'] else catalog.name}
    batch_runtime=Path('/opt/commander-gym-batch-runtime')/args.gym_sha
    receipt=json.loads((batch_runtime/'receipt.json').read_text())
    if receipt['engineSha']!=profile['engineSha'] or receipt['gymSha']!=args.gym_sha or receipt['unchangedOtherArchiveMembers'] is not True:
        parser.error('Matching isolated runtime receipt required')
    pins.update(server_sha256=receipt['serverSha256'],adapter_sha256=receipt['adapterSha256'])
    verify={str(batch_runtime/'server.jar'):receipt['serverSha256']}
    for role in ('server','catalog'):
        artifact=profile['artifacts'][role]
        for name,sha in artifact['files'].items():verify[str(Path(artifact['root'])/name)]=sha
    import hashlib
    for file in release.rglob('*.py'):
        verify[str(file)]=hashlib.sha256(file.read_bytes()).hexdigest()
    for name in ('runtime-lock.json',lock['python'],lock['java']):verify[str(deps/name)]=profile['artifacts']['dependencies']['files'][name]
    config={'state_directory':str(root),'runtime_directory':str(runtime),'allowed_logins':['Blue42hand@github'],
            'engine_uid':engine.pw_uid,'engine_gid':engine.pw_gid,'provider_uid':provider.pw_uid,'provider_gid':provider.pw_gid,
            'panel_uid':panel.pw_uid,'panel_gid':panel.pw_gid,'native_port':8879,'provider_port':8880,
            'java':str(java),'python_packages':str(deps/lock['pythonPath'][0]),'roster':profile['catalog']['roster'],
            'catalog':list(registry.profile_payloads()),'pins':pins,'recording_pins':recording_pins(profile),'verify_files':verify}
    local_catalog=None
    if args.local_catalog:
        from dataclasses import asdict
        from commander_gym.lmstudio_provider import LMStudioConfig
        from commander_gym.local_catalog import validate_local_catalog
        local_catalog=args.local_catalog.resolve(strict=True)
        if local_catalog.is_relative_to(Path('/home')):parser.error('Local catalog must be readable below a service artifact root outside /home')
        inference=LMStudioConfig(**json.loads((local_catalog/'inference.json').read_text()))
        local_registry=validate_local_catalog(local_catalog/'catalog.json',local_catalog,inference)
        if set(local_registry.binding_fingerprints)&set(pins['bindings']):parser.error('Local/cloud Binding collision')
        pins['bindings']={**pins['bindings'],**local_registry.binding_fingerprints}
        pins['local_inference']=asdict(inference)
        config.update(local_inference=asdict(inference),local_roster='catalog.json')
        config['catalog'] += [{**entry,'execution_modes':['local']} for entry in local_registry.profile_payloads()]
        for file in local_catalog.rglob('*'):
            if file.is_file():verify[str(file)]=hashlib.sha256(file.read_bytes()).hexdigest()
    write('/etc/commander-gym-batches/config.json',json.dumps(config,indent=2))
    for role in ('native','recorder','provider','fixture','local'):
        role_root=Path('/var/lib/commander-gym-batch-'+role+'-root');role_root.mkdir(mode=0o755,exist_ok=True)
        for name,target in [('lib','usr/lib'),('lib64','usr/lib64'),('bin','usr/bin')]:
            link=role_root/name
            if link.is_symlink():
                if os.readlink(link)!=target:raise ValueError('Unexpected role root symlink')
            elif not link.exists():link.symlink_to(target)
        is_provider=role in ('provider','fixture','local')
        user='commander-batch-provider' if is_provider else 'commander-batch-engine'
        binds=[f'BindReadOnlyPaths=/usr {deps} {release}:/app {catalog}:/catalog',
               f'BindReadOnlyPaths={root}/active/role.json:/config/role.json',
               'BindReadOnlyPaths=/etc/passwd /etc/group /etc/nsswitch.conf /etc/resolv.conf /etc/ssl/certs']
        if local_catalog and role in ('recorder','local'):
            binds += [f'BindReadOnlyPaths={local_catalog}:/local-catalog']
        if role=='native':
            server=batch_runtime
            binds += [f'BindReadOnlyPaths={server}:/server {root}/active/native-config:/native-config',
                      f'BindPaths={root}/active/captures:/captures {runtime}/native:/ipc/native']
        elif role=='recorder':
            binds += [f'BindPaths={root}/active/captures:/captures {runtime}/native:/ipc/native {runtime}/provider:/ipc/provider']
        else:
            binds += [f'BindReadOnlyPaths={root}/active/provider-config:/provider-config',
                      f'BindPaths={root}/active/budget:/budget {root}/active/progress:/progress {runtime}/provider:/ipc/provider']
        credentials='LoadCredential=provider.env:/srv/commander-runtime/.config/commander-gym/openai.env\n' if role=='provider' else ''
        network='IPAddressDeny=any\nIPAddressAllow=localhost\n' if not is_provider else ''
        if role=='local':
            from urllib.parse import urlsplit
            endpoint_ip=urlsplit(config['local_inference']['endpoint']).hostname if local_catalog else '127.0.0.1'
            network=f'IPAddressDeny=any\nIPAddressAllow=localhost\nIPAddressAllow={endpoint_ip}\n'
        supplement='SupplementaryGroups=commander-batch-provider\n' if role=='recorder' else ''
        unit=f'''[Unit]
Description=Isolated Commander Gym batch {role}
BindsTo=commander-gym-batches.service
After=commander-gym-batches.service
[Service]
Type=exec
User={user}
Group={user}
{supplement}{credentials}RootDirectory={role_root}
MountAPIVFS=yes
WorkingDirectory=/app
ExecStart={python} -I -S -B /app/scripts/batch_role_entry.py {'local' if role=='local' else 'provider' if is_provider else role}
Environment=PYTHONPATH=/app:{deps/lock['pythonPath'][0]}
{chr(10).join(binds)}
CapabilityBoundingSet=CAP_DAC_READ_SEARCH
AmbientCapabilities=CAP_DAC_READ_SEARCH
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectProc=invisible
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
RestrictRealtime=yes
RestrictNamespaces=yes
LockPersonality=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
{network}UMask=0077
MemoryMax={'3G' if role=='native' else '1G'}
TasksMax={'128' if role=='native' else '64'}
CPUQuota=150%
Nice=10
KillMode=control-group
TimeoutStopSec=15
Restart=no
StandardOutput=null
StandardError=journal
'''
        write('/etc/systemd/system/commander-gym-batch-'+role+'.service',unit,0o644)
    unit=f'''[Unit]
Description=Private Commander Gym durable batch supervisor
After=network.target
[Service]
Type=simple
User=root
WorkingDirectory={release}
ExecStart=/usr/bin/python3 -B -m commander_gym.batch_supervisor --config /etc/commander-gym-batches/config.json
RuntimeDirectory=commander-gym-batches
RuntimeDirectoryMode=0755
RuntimeDirectoryPreserve=yes
ReadWritePaths={root} /run/commander-gym-batches
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
RestrictRealtime=yes
RestrictNamespaces=yes
RestrictAddressFamilies=AF_UNIX AF_INET
IPAddressDeny=any
IPAddressAllow=localhost
CapabilityBoundingSet=CAP_CHOWN CAP_DAC_OVERRIDE CAP_DAC_READ_SEARCH CAP_FOWNER
NoNewPrivileges=yes
UMask=0077
MemoryMax=256M
TasksMax=32
CPUQuota=50%
Nice=10
TimeoutStopSec=140
Restart=on-failure
RestartSec=5
[Install]
WantedBy=multi-user.target
'''
    write('/etc/systemd/system/commander-gym-batches.service',unit,0o644)
    print('Prepared fixed services and pinned catalog. No services enabled, no games started, no provider credential read.')

if __name__=='__main__':main()
