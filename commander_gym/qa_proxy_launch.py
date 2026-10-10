"""Use an explicitly sealed guest nginx closure and literal QA TLS fixture.

No host nginx, public listener, certificate store or production config is used.
The guest operator must propose these additional dependency bytes in a NEW
matched profile. This is not permission to change the frozen dependency artifact.
"""
from pathlib import Path
import os
import re

from .manual_runtime_launch import LaunchSpec
from .manual_runtime_profile import decode, protected_bytes, relative_path, require


def proxy_spec(runtime):
    from .manual_runtime_qa_roles import _private_role_directory, _literal_file
    profile = runtime.profile
    artifact = profile['artifacts']['dependencies']
    root = Path(artifact['root'])
    require('qa-proxy-lock.json' in artifact['files'], 'qa_proxy_closure_missing')
    lock = decode(protected_bytes(root/'qa-proxy-lock.json', uid=artifact['uid']))
    require(set(lock) == {'schemaVersion','program','certificate','fixtureKey'}
            and type(lock['schemaVersion']) is int and lock['schemaVersion'] == 1, 'qa_proxy_lock')
    for key in ('program','certificate','fixtureKey'):
        relative_path(lock[key])
        require(lock[key] in artifact['files'], 'qa_proxy_unsealed_file')
    require(lock['program'] == 'qa-proxy/nginx'
            and lock['certificate'] == 'qa-proxy/literal-fixture-certificate.pem'
            and lock['fixtureKey'] == 'qa-proxy/literal-fixture-key.pem', 'qa_proxy_fixture_paths')
    frontend = Path(profile['artifacts']['frontend']['root'])
    for path in (root, frontend):
        require(str(path).startswith('/opt/commander-gym-qa/artifacts/')
                and not any(c in str(path) for c in '\n\r";{}'), 'qa_proxy_config_path')
    directory = Path('/opt/commander-gym-qa/qa/proxy')
    _private_role_directory(directory, os.getuid())
    origins = profile['ingress']['origins']
    require(all(re.fullmatch(r'https://[A-Za-z0-9.-]+(?::[0-9]+)?', origin) for origin in origins), 'qa_proxy_origin')
    origin_rules = '\n'.join('    ~^'+re.escape(origin)+'$ 1;' for origin in origins)
    # Explicit loopback-only TLS. The transport observation never attests LAN,
    # tailnet, real certificates, host nginx ABI or production HTTPS eligibility.
    configuration = f'''pid {directory}/nginx.pid;
error_log stderr warn;
events {{ worker_connections 64; }}
http {{
  map $http_origin $qa_origin_allowed {{
    default 0;
{origin_rules}
  }}
  access_log off;
  client_body_temp_path {directory}/body;
  proxy_temp_path {directory}/proxy;
  server {{
    listen 127.0.0.1:18443 ssl;
    ssl_certificate {root / lock['certificate']};
    ssl_certificate_key {root / lock['fixtureKey']};
    root {frontend};
    location / {{ try_files $uri $uri/ /index.html; }}
    location /actuator {{ return 404; }}
    location /internal {{ return 404; }}
    location /api {{ return 404; }}
    location /game/ {{ return 404; }}
    location = /game {{
      if ($qa_origin_allowed = 0) {{ return 403; }}
      proxy_pass http://127.0.0.1:18080;
      proxy_http_version 1.1;
      proxy_set_header Upgrade $http_upgrade;
      proxy_set_header Connection "upgrade";
      proxy_set_header Host $host;
      proxy_read_timeout 120s;
    }}
  }}
}}
'''
    path = directory/'nginx.conf'
    _literal_file(path, configuration.encode())
    return LaunchSpec((str(root/lock['program']), '-p', str(directory)+'/', '-c', str(path),
                       '-g', 'daemon off;'), {'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'})
