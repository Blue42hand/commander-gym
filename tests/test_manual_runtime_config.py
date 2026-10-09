"""Proposed minimal shared ingress/units; nothing installed or key-generated."""
import unittest
from commander_gym.manual_runtime_config import shared_https_nginx, service_units
from commander_gym.manual_runtime_profile import ManualRuntimeError


class ManualRuntimeConfigTests(unittest.TestCase):
    def test_shared_ingress_has_exact_origins_and_no_private_policy_route(self):
        text=shared_https_nginx(frontend_root='/srv/argentum-luna/artifacts/frontend/'+'a'*64,
            tailnet_origin='https://tolaria.taila3c720.ts.net',lan_address='192.168.0.178',lan_subnet='192.168.0.0/24')
        self.assertIn('listen 127.0.0.1:8180;',text)
        self.assertIn('listen 192.168.0.178:8443 ssl;',text)
        self.assertIn('return 308 https://192.168.0.178:8443$request_uri;',text)
        self.assertNotIn('127.0.0.1:8083',text)
        self.assertNotIn('OPENAI',text)
        self.assertNotIn('replay',text)
        self.assertEqual(text.count('proxy_set_header X-Forwarded-Proto https;'),4)
        self.assertNotIn('http://192.168.0.178:8180" 1',text)
    def test_service_separation_and_preflight_dependency(self):
        units=service_units(launcher_root='/srv/argentum-luna/artifacts/launcher/'+'a'*64,
            dependency_root='/srv/argentum-luna/artifacts/dependencies/'+'b'*64,python_relative='python/bin/python3.12')
        for name,text in units.items():
            if 'openai.env' in text:self.assertEqual(name,'units/argentum-luna-sidecar.service')
            self.assertNotIn('allow_same_uid_fixture',text)
        self.assertIn('Group=commander-prod',units['units/argentum-recorder.conf'])
        self.assertIn('After=argentum-manual-preflight.service',units['units/argentum-recorder.conf'])
        self.assertIn('SendSIGKILL=no',units['units/argentum-luna-sidecar.service'])
    def test_unsafe_template_input_is_rejected(self):
        with self.assertRaises(ManualRuntimeError):
            shared_https_nginx(frontend_root='/srv/current; injected',tailnet_origin='https://tailnet.example',lan_address='192.168.0.178',lan_subnet='0.0.0.0/0')

    def test_tailnet_only_redirects_lan_without_tls_or_private_proxy(self):
        text = shared_https_nginx(frontend_root='/srv/argentum-luna/artifacts/frontend/'+'a'*64,
            tailnet_origin='https://tolaria.taila3c720.ts.net', lan_address='192.168.0.178',
            lan_subnet='192.168.0.0/24', tailnet_only=True)
        self.assertIn('listen 127.0.0.1:8180;', text)
        self.assertIn('listen 192.168.0.178:8180;', text)
        self.assertIn('return 308 https://tolaria.taila3c720.ts.net$request_uri;', text)
        self.assertNotIn('8443', text)
        self.assertNotIn('ssl_certificate', text)
        self.assertNotIn('argentum_lan_origin_allowed', text)
        self.assertNotIn('127.0.0.1:8083', text)
        self.assertEqual(text.count('proxy_pass'), 2)
        units = service_units(launcher_root='/srv/argentum-luna/artifacts/launcher/'+'a'*64,
            dependency_root='/srv/argentum-luna/artifacts/dependencies/'+'b'*64,
            python_relative='python/bin/python3.12', tailnet_only=True)
        self.assertNotIn('lan-server', units['units/argentum-web.conf'])
        self.assertEqual(sum('LoadCredential=openai.env:' in t for t in units.values()), 1)

    def test_profile_config_mode_and_roots_must_match_before_credentials(self):
        import hashlib
        from commander_gym.manual_runtime_config import validate_config_contract
        origin='https://tolaria.taila3c720.ts.net'
        roots={role:'/srv/argentum-luna/artifacts/'+role+'/'+char*64
               for role,char in [('frontend','a'),('launcher','b'),('dependencies','c')]}
        profile={'ingress':{'origins':[origin], 'lanAddress':'192.168.0.178', 'lanSubnet':'192.168.0.0/24'},
                 'artifacts':{role:{'root':root} for role,root in roots.items()}}
        def files(tailnet_only):
            text=service_units(launcher_root=roots['launcher'],dependency_root=roots['dependencies'],
                python_relative='python/bin/python3.12',tailnet_only=tailnet_only)
            text['nginx.conf']=shared_https_nginx(frontend_root=roots['frontend'],tailnet_origin=origin,
                lan_address='192.168.0.178',lan_subnet='192.168.0.0/24',tailnet_only=tailnet_only)
            return {name:hashlib.sha256(value.encode()).hexdigest() for name,value in text.items()}
        profile['artifacts']['config']={'files':files(True)}
        validate_config_contract(profile)
        profile['artifacts']['config']['files']=files(False)
        with self.assertRaises(ManualRuntimeError):validate_config_contract(profile)
        profile['artifacts']['config']['files']['lan-server.crt']='d'*64
        profile['ingress']['origins'].append('https://192.168.0.178:8443')
        validate_config_contract(profile)
        profile['artifacts']['config']['files']=files(True)
        with self.assertRaises(ManualRuntimeError):validate_config_contract(profile)
        profile['ingress']['origins']=[origin]
        profile['artifacts']['launcher']['root']='/srv/argentum-luna/artifacts/launcher/'+'e'*64
        with self.assertRaisesRegex(ManualRuntimeError,'config_profile_mismatch'):validate_config_contract(profile)
