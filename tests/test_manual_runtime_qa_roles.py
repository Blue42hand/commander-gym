"""Source-only launch/selection regressions: no privilege, services or games."""
import copy
import io
import os
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from commander_gym import manual_runtime_qa_roles as roles
from commander_gym.manual_runtime_profile import ManualRuntimeError, ValidatedRuntime, canonical, digest
from commander_gym.manual_runtime_rollout import RuntimeSnapshot
from commander_gym.qa_fake_responses import SchemaFixtureClient, fixture_value
from test_manual_runtime_profile import fixture_profile
from dataclasses import asdict


class QARoleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='.qa-roles-', dir=Path.cwd())
        self.addCleanup(self.tmp.cleanup)
        self.profile = fixture_profile(Path(self.tmp.name))
        self.previous = copy.deepcopy(self.profile)
        self.previous['artifacts']['launcher']['root'] += '-previous'
        self.authority = dict(issuedUnix=1000,expiresUnix=1900,nonce='a'*32,sequence=2,
                             previousProfileSha256=digest(self.previous))
        self.policy = dict(acceptingNewGames=False,nonce='a'*32,providerMode='fake',credentialSource='qa-placeholder-only')

    def selector(self, profile, sequence):
        return asdict(RuntimeSnapshot(digest(profile),sequence,'manual-luna-v1',
            profile['engineSha'],profile['gymSha'],profile['recordingSchemaVersion']))

    def select(self, profile=None, sequence=2, **kwargs):
        profile = profile or self.profile
        return roles.selected_runtime(self.authority,self.profile,profile,
            self.selector(profile,sequence),self.policy,now=kwargs.get('now',1000))

    def test_candidate_previous_and_authenticated_rollback_sequences(self):
        self.assertEqual(self.select().runtime_id,digest(self.profile))
        self.assertEqual(self.select(self.previous,1).sequence,1)
        self.assertEqual(self.select(self.previous,2).sequence,2)
        for profile,sequence in ((self.profile,1),(self.profile,3),(self.previous,3)):
            with self.assertRaises(ManualRuntimeError): self.select(profile,sequence)

    def test_policy_exactness_freshness_and_unknown_profile_rejected(self):
        for key,value in (('acceptingNewGames',True),('providerMode','openai'),
                          ('credentialSource','systemd-credentials'),('nonce','b'*32)):
            policy={**self.policy,key:value}
            with self.assertRaisesRegex(ManualRuntimeError,'qa_role_policy'):
                roles.selected_runtime(self.authority,self.profile,self.profile,self.selector(self.profile,2),policy,now=1000)
        for now in (999,1901):
            with self.assertRaisesRegex(ManualRuntimeError,'qa_role_expired'): self.select(now=now)
        profile=copy.deepcopy(self.profile);profile['artifacts']['recorder']['root']+='-other'
        with self.assertRaisesRegex(ManualRuntimeError,'qa_role_profile'): self.select(profile)

    def test_selector_identity_and_types(self):
        for mutation in (dict(gym_sha='f'*40),dict(runtime_id='a'*64),dict(sequence=True),dict(extra=1)):
            selector={**self.selector(self.profile,2),**mutation}
            with self.assertRaises(ManualRuntimeError):
                roles.selected_runtime(self.authority,self.profile,self.profile,selector,self.policy,now=1000)

    def test_all_four_launch_specs_are_isolated_and_have_no_credentials(self):
        runtime=self.select()
        with patch('commander_gym.manual_runtime_launch.dependency_lock',return_value={
                'python':'python/bin/python3','pythonPath':['site-packages']}):
            for role in roles.ROLES:
                spec,message=roles.child_spec(role,runtime)
                self.assertEqual(spec.arguments[1:4],('-I','-S','-B'))
                self.assertTrue(spec.arguments[-1].endswith('/qa_role_child_entry.py'))
                self.assertEqual(message['role'],role)
                self.assertEqual(message['runtimeId'],digest(self.profile))
                self.assertFalse(any('OPENAI' in k or 'CREDENTIAL' in k or 'TOKEN' in k for k in spec.environment))
        with self.assertRaisesRegex(ManualRuntimeError,'qa_role_name'): roles.child_spec('host',runtime)

    def test_child_rejects_production_wrong_uid_or_ambient_credentials_before_dispatch(self):
        profile=copy.deepcopy(self.profile)
        message=dict(role='sidecar',profile=profile,runtimeId=digest(profile),sequence=2,
                     importPaths=[],sidecarGid=777)
        with patch.object(roles.os,'getuid',return_value=0),patch.object(roles.os,'geteuid',return_value=0):
            with self.assertRaisesRegex(ManualRuntimeError,'qa_role_child_uid'): roles.run_child(message)
        uid=profile['identities']['sidecarUid']
        with patch.object(roles.os,'getuid',return_value=uid),patch.object(roles.os,'geteuid',return_value=uid),patch.dict(roles.os.environ,{'OPENAI_API_KEY':'synthetic-test-only'}):
            with self.assertRaisesRegex(ManualRuntimeError,'qa_role_child_environment'): roles.run_child(message)

    def test_native_configtree_uses_literal_token_not_credential_loader(self):
        profile=self.profile;uid=profile['identities']['nativeUid']
        message=dict(role='native',profile=profile,runtimeId=digest(profile),sequence=2,importPaths=[],sidecarGid=777)
        spec=Mock(arguments=('java','fake'),environment={})
        with patch.object(roles.os,'getuid',return_value=uid),patch.object(roles.os,'geteuid',return_value=uid),patch.dict(roles.os.environ,{},clear=True),patch.object(roles.signal,'signal'),patch.object(roles,'_private_role_directory'),patch.object(Path,'iterdir',return_value=iter([])),patch.object(roles,'_literal_file') as literal,patch('commander_gym.manual_runtime_launch.native_spec',return_value=spec),patch.object(roles.os,'execve') as execute:
            roles.run_child(message)
            self.assertEqual(literal.call_args.args[1],roles.TOKEN.encode())
            execute.assert_called_once_with('java',spec.arguments,spec.environment)

    def test_proxy_requires_new_sealed_dependency_closure(self):
        from commander_gym.qa_proxy_launch import proxy_spec
        with self.assertRaisesRegex(ManualRuntimeError,'qa_proxy_closure_missing'): proxy_spec(self.select())

    @unittest.skipUnless(sys.platform == 'linux', 'Linux guest descriptor boundary')
    def test_real_anonymous_launch_descriptor_cannot_be_modified(self):
        with tempfile.TemporaryFile() as stream:
            stream.write(b'{"fixture":true}');stream.flush();stream.seek(0)
            fd=roles.readonly_message_fd(stream.fileno())
            try:
                self.assertEqual(os.read(fd,100),b'{"fixture":true}')
                with self.assertRaises(OSError):os.write(fd,b'changed')
                self.assertEqual(os.fstat(fd).st_nlink,0)
            finally:os.close(fd)

    def test_proxy_uses_closed_origin_and_control_routes_on_loopback_only(self):
        from commander_gym import qa_proxy_launch as proxy
        profile=copy.deepcopy(self.profile)
        profile['artifacts']['dependencies']['root']='/opt/commander-gym-qa/artifacts/dependencies/new'
        profile['artifacts']['frontend']['root']='/opt/commander-gym-qa/artifacts/frontend/new'
        lock=dict(schemaVersion=1,program='qa-proxy/nginx',certificate='qa-proxy/literal-fixture-certificate.pem',fixtureKey='qa-proxy/literal-fixture-key.pem')
        profile['artifacts']['dependencies']['files'].update({name:'a'*64 for name in ['qa-proxy-lock.json',*list(lock.values())[1:]]})
        runtime=ValidatedRuntime(canonical(profile),digest(profile),2)
        with patch.object(proxy,'protected_bytes',return_value=canonical(lock)),patch.object(roles,'_private_role_directory'),patch.object(roles,'_literal_file') as write:
            spec=proxy.proxy_spec(runtime)
        text=write.call_args.args[1].decode()
        self.assertIn('listen 127.0.0.1:18443 ssl;',text)
        self.assertIn('default 0;',text)
        self.assertIn('if ($qa_origin_allowed = 0) { return 403; }',text)
        self.assertIn('location = /game',text)
        for route in ('actuator','internal','api','game/'):
            self.assertIn('location /'+route+' { return 404; }',text)
        self.assertNotIn('0.0.0.0',text)
        self.assertTrue(spec.arguments[0].endswith('/qa-proxy/nginx'))


class FakeResponsesTests(unittest.TestCase):
    def test_native_schema_fixture_without_sdk_or_network(self):
        client=SchemaFixtureClient()
        request=dict(text={'format':{'schema':{'type':'object','properties':{
            'actionId':{'type':'integer','enum':[5,8]},'params':{'type':'object','properties':{}}},
            'required':['actionId','params']}}})
        first=client.create(**request);second=client.create(**request)
        self.assertEqual(first.output_text,second.output_text)
        self.assertEqual(first.output_text,'{"actionId": 5, "params": {}}')
        self.assertEqual(first.usage.total_tokens,0)
        self.assertTrue(first.id.startswith('qa-fixture-'))

    def test_unsupported_and_unbounded_schemas_fail_closed(self):
        for schema in ({'type':'unknown'},{'type':'array','minItems':101,'items':{'type':'null'}}):
            with self.assertRaises(ValueError):fixture_value(schema)


if __name__ == '__main__': unittest.main()
