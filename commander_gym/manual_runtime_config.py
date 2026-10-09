"""Render a proposal only; no certificate/key, unit or network is installed."""
from __future__ import annotations

from pathlib import Path
import re

from .manual_runtime_profile import require


API_MAP = '''map "$request_method:$uri" $argentum_api_allowed {
    default 0;
    ~^GET:/api/config$ 1;
    ~^GET:/api/cards(/.*)?$ 1;
    ~^GET:/api/printings$ 1;
    ~^GET:/api/sets(/.*)?$ 1;
    ~^GET:/api/decks/(examples|formats)$ 1;
    ~^POST:/api/decks/(validate|legal-formats)$ 1;
    ~^GET:/api/quick-games/(public|live)$ 1;
    ~^GET:/api/tournaments/(public|live)$ 1;
}
'''


def shared_https_nginx(*, frontend_root: str, tailnet_origin: str, lan_address: str, lan_subnet: str) -> str:
    # Inputs are exact proposed metadata, never request/user strings in nginx.
    from urllib.parse import urlsplit
    import ipaddress
    address, subnet = ipaddress.ip_address(lan_address), ipaddress.ip_network(lan_subnet)
    require(address.version == 4 and address.is_private and address in subnet and subnet.prefixlen >= 24, 'exact_lan_scope')
    uri = urlsplit(tailnet_origin)
    require(uri.scheme == 'https' and uri.hostname and not uri.port and not uri.username and not uri.password
            and uri.path == '' and not uri.query and not uri.fragment and re.fullmatch(r'[a-z0-9.-]+', uri.hostname), 'tailnet_origin')
    require(re.fullmatch(r'/srv/argentum-luna/artifacts/frontend/[0-9a-f]{64}', frontend_root), 'sealed_frontend_layout')
    lan_origin = f'https://{address}:8443'
    maps = '''map $http_upgrade $argentum_connection_upgrade { default upgrade; '' close; }
log_format argentum_metadata '$time_iso8601 $request_method $status $body_bytes_sent $request_time';
'''
    for name, origin in (('tailnet', tailnet_origin), ('lan', lan_origin)):
        maps += f'map $http_origin $argentum_{name}_origin_allowed {{ default 0; "{origin}" 1; }}\n'
    maps += API_MAP
    def server(listeners: str, origin: str, allow: str, tls: str = ''):
        return f'''server {{
    {listeners}
    {allow}
    deny all;
    server_name _;
    root {frontend_root};
    index index.html;
    server_tokens off;
    access_log /var/log/argentum-web/access.log argentum_metadata;
    error_log /dev/null crit;
    {tls}
    add_header X-Content-Type-Options nosniff always;
    add_header Referrer-Policy no-referrer always;
    add_header X-Frame-Options DENY always;
    client_max_body_size 5m;
    location / {{ try_files $uri $uri/ /index.html; }}
    location /actuator/ {{ return 404; }}
    location /api/ {{
        set $argentum_mutation 0;
        if ($argentum_api_allowed = 0) {{ return 404; }}
        if ($request_method = POST) {{ set $argentum_mutation 1; }}
        if ($argentum_{origin}_origin_allowed = 0) {{ set $argentum_mutation "${{argentum_mutation}}0"; }}
        if ($argentum_mutation = 10) {{ return 403; }}
        proxy_pass http://127.0.0.1:18080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header Tailscale-User-Login "";
        proxy_set_header Tailscale-User-Name "";
        proxy_set_header Tailscale-App-Capabilities "";
    }}
    location = /game {{
        if ($argentum_{origin}_origin_allowed = 0) {{ return 403; }}
        proxy_pass http://127.0.0.1:18080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $argentum_connection_upgrade;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header Tailscale-User-Login "";
        proxy_set_header Tailscale-User-Name "";
        proxy_set_header Tailscale-App-Capabilities "";
        proxy_read_timeout 86400;
    }}
}}
'''
    maps += server('listen 127.0.0.1:8180;', 'tailnet', 'allow 127.0.0.1;')
    maps += f'''server {{
    listen {address}:8180;
    allow {subnet};
    deny all;
    server_name _;
    access_log off;
    error_log /dev/null crit;
    return 308 {lan_origin}$request_uri;
}}
'''
    maps += server(f'listen {address}:8443 ssl;', 'lan', f'allow {subnet};',
                   'ssl_certificate /run/credentials/argentum-web.service/lan-server.crt;\n    ssl_certificate_key /run/credentials/argentum-web.service/lan-server.key;\n    ssl_protocols TLSv1.2 TLSv1.3;')
    return maps


def service_units(*, launcher_root: str, dependency_root: str, python_relative: str) -> dict[str, str]:
    for role, root in (('launcher', launcher_root), ('dependencies', dependency_root)):
        require(re.fullmatch('/srv/argentum-luna/artifacts/' + role + '/[0-9a-f]{64}', root), 'sealed_unit_layout')
    require(re.fullmatch(r'[A-Za-z0-9_./-]+', python_relative) and '..' not in Path(python_relative).parts, 'interpreter_path')
    python = dependency_root + '/' + python_relative
    dispatch = f'{python} -I -S -B {launcher_root}/scripts/run_manual_runtime.py'
    gate = 'ConditionPathExists=/run/argentum-luna-runtime/precredential.json'
    creds = 'LoadCredential=profile.json:/etc/argentum-luna/service-profile.json'
    protection = '''NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
CapabilityBoundingSet=
UMask=0077
Restart=no
KillSignal=SIGTERM
KillMode=control-group
SendSIGKILL=no
TimeoutStopSec=150
StandardOutput=null
StandardError=null
'''
    entry = f'{python} -I -S -B {launcher_root}'
    result = {
        'units/argentum-manual-preflight.service': f'''[Unit]
Description=Verify exact approved runtime before loading private credentials
Before=argentum-recorder.service argentum-luna-sidecar.service argentum-play.service
[Service]
Type=oneshot
User=root
ExecStart={dispatch} preflight
UMask=0077
''',
        'units/argentum-play.conf': f'''[Unit]
{gate}
Requires=argentum-recorder.service argentum-luna-sidecar.service
After=argentum-recorder.service argentum-luna-sidecar.service
[Service]
ExecStart=
ExecStart={entry}/scripts/manual_service_entry.py native
Environment=
EnvironmentFile=
LoadCredential=
{creds}
LoadCredential=commander-gym.sidecar.token:/etc/argentum-luna/sidecar.token
SendSIGKILL=no
''',
        'units/argentum-recorder.conf': f'''[Unit]
{gate}
Requires=argentum-manual-preflight.service
After=argentum-manual-preflight.service
[Service]
Group=commander-prod
SupplementaryGroups=argentum-play
ExecStart=
ExecStart={entry}/recorder_entry.py
Environment=
EnvironmentFile=
LoadCredential=
{creds}
ReadWritePaths=/run/argentum-luna-ipc
SendSIGKILL=no
''',
        'units/argentum-luna-sidecar.service': f'''[Unit]
Description=Approved manual Luna private provider sidecar
{gate}
Requires=argentum-manual-preflight.service argentum-recorder.service
After=argentum-manual-preflight.service argentum-recorder.service
[Service]
Type=exec
User=commander-prod
Group=commander-prod
{creds}
LoadCredential=openai.env:/srv/commander-runtime/.config/commander-gym/openai.env
LoadCredential=commander-gym.sidecar.token:/etc/argentum-luna/sidecar.token
ExecStart={entry}/sidecar_entry.py
{protection}RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
InaccessiblePaths=/var/lib/commander-gym/runs /srv/commander-runtime /etc/ssh /etc/ssl/private -/var/lib/tailscale
ReadOnlyPaths=/srv/argentum-luna/artifacts
MemoryMax=1G
TasksMax=64
''',
        'units/argentum-web.conf': '''[Service]
LoadCredential=lan-server.crt:/etc/argentum-luna/lan-server.crt
LoadCredential=lan-server.key:/etc/argentum-luna/lan-server.key
SendSIGKILL=no
''',
    }
    for operation, unit in (('update', 'argentum-updater'), ('admit', 'argentum-admission'), ('source', 'argentum-source-feeder')):
        result['units/' + unit + '.conf'] = f'''[Service]
ExecStart=
ExecStart={dispatch} {operation}
ReadWritePaths=/etc/argentum-luna /var/lib/argentum-updater/manual-runtime /run/argentum-luna-runtime
'''
    return result
