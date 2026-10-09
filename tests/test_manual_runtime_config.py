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
