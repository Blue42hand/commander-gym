#!/usr/bin/env python3
"""Prepare a read-only collector and wire its snapshot into the existing panel.

Does not start/stop games, load provider credentials, or restart game services.
Activation: daemon-reload; enable --now commander-gym-observer; restart panel.
"""
import argparse
import json
import os
from pathlib import Path


def write(path,text,mode=0o600):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text);p.chmod(mode)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--release',type=Path,required=True);args=parser.parse_args()
    if os.getuid()!=0:parser.error('Root required for service preparation')
    release=args.release.resolve(strict=True)
    if release.parent!=Path('/opt/commander-gym-web') or not (release/'commander_gym/panel_observer.py').is_file():parser.error('Immutable panel release required')
    human=json.loads(Path('/etc/argentum-luna/service-profile.json').read_text())
    batch=json.loads(Path('/etc/commander-gym-batches/config.json').read_text())
    panel_path=Path('/etc/commander-gym-web/panel.json');panel=json.loads(panel_path.read_text())
    workers=['commander-gym-batch-'+role+'.service' for role in ('native','recorder','provider','fixture')]
    state=Path('/var/lib/commander-gym-observer');state.mkdir(mode=0o700,exist_ok=True)
    config={'output':str(state/'overview.json'),'scope':'Human-play and managed-batch runtimes, including private games',
            'control_socket':batch['runtime_directory']+'/panel/control.sock',
            'actor':panel['allowed_logins'][0],'supervisor_unit':'commander-gym-batches.service','services':panel.get('services',[]),
            'runtimes':[{'id':'human','name':'Human play','scope':'Native human-play server · public and private sessions',
                         'unit':'argentum-play.service','socket':human['paths']['lifecycleSocket'],'uid':human['identities']['nativeUid']},
                        {'id':'batches','name':'Managed batches','scope':'Isolated batch server · all managed sessions',
                         'unit':workers[0],'socket':batch['runtime_directory']+'/native/lifecycle.sock','uid':batch['engine_uid'],'worker_units':workers}]}
    write('/etc/commander-gym-web/observer.json',json.dumps(config,indent=2)+'\n')
    unit=f'''[Unit]
Description=Read-only private Commander Gym live observer
After=network.target commander-gym-batches.service
[Service]
Type=simple
User=root
WorkingDirectory={release}
ExecStart=/usr/bin/python3 -B -m commander_gym.panel_observer --config /etc/commander-gym-web/observer.json
StateDirectory=commander-gym-observer
StateDirectoryMode=0700
ReadWritePaths={state}
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
RestrictAddressFamilies=AF_UNIX
CapabilityBoundingSet=CAP_DAC_OVERRIDE CAP_DAC_READ_SEARCH
NoNewPrivileges=yes
InaccessiblePaths=-/srv/commander-runtime/.config/commander-gym -/etc/argentum-luna/sidecar.token -/run/credentials -/var/lib/commander-gym-batches
UMask=0077
MemoryMax=128M
TasksMax=16
CPUQuota=20%
Nice=10
TimeoutStopSec=15
Restart=on-failure
RestartSec=5
[Install]
WantedBy=multi-user.target
'''
    write('/etc/systemd/system/commander-gym-observer.service',unit,0o644)
    panel['live_snapshot']='/live/overview.json';write(panel_path,json.dumps(panel,indent=2)+'\n')
    unit_path=Path('/etc/systemd/system/commander-gym-web.service');text=unit_path.read_text()
    mount='BindReadOnlyPaths=/var/lib/commander-gym-observer:/live'
    if mount not in text:write(unit_path,text+'\n[Service]\n'+mount+'\n',0o644)
    print('Prepared live observer and read-only panel snapshot mount. No game services changed.')

if __name__=='__main__':main()
