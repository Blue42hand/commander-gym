"""Deterministic source specs only. No Redis process, privileged role or game."""
import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from dataclasses import asdict
from commander_gym.manual_runtime_profile import canonical, digest, validate_profile, ManualRuntimeError, ValidatedRuntime
from commander_gym.manual_runtime_qa import match_profiles, roles_order, validate_authority
from commander_gym.manual_runtime_qa_roles import selected_runtime
from commander_gym.qa_redis_variant import CONFIG, PROFILE, dependency_additions, native_overrides, redis_spec, role_uid
from commander_gym.qa_redis_recovery import validate_snapshot, snapshot_request
from test_manual_runtime_profile import fixture_profile

class RedisVariantTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='.qa-redis-source-',dir=Path.cwd());self.addCleanup(self.temp.cleanup)
        self.base=fixture_profile(Path(self.temp.name))
        self.qa=copy.deepcopy(self.base);self.qa['profile']=PROFILE;self.qa['settings']['redisEnabled']=True
        self.qa['redisQa']=dict(runId='a'*32,uid=10004,memoryMaxBytes=64*1024**2,snapshotBytes=8*1024**2,aggregateSnapshotBytes=16*1024**2)
        self.dep=Path(self.qa['artifacts']['dependencies']['root']);(self.dep/'qa-redis').mkdir()
        files={'qa-redis/redis-server':b'nonexecutable synthetic bytes', 'qa-redis/redis.conf':CONFIG,'qa-redis/loader':b'synthetic loader'}
        for name,raw in files.items():
            (self.dep/name).write_bytes(raw)
            self.qa['artifacts']['dependencies']['files'][name]=hashlib.sha256(raw).hexdigest()
        lock=dict(schemaVersion=1,program='qa-redis/redis-server',config='qa-redis/redis.conf',files={name:self.qa['artifacts']['dependencies']['files'][name] for name in files})
        (self.dep/'qa-redis-lock.json').write_bytes(canonical(lock))
        self.qa['artifacts']['dependencies']['files']['qa-redis-lock.json']=hashlib.sha256(canonical(lock)).hexdigest()
    def test_explicit_variant_is_nonproduction_original_unchanged(self):
        validate_profile(self.base);validate_profile(self.qa)
        self.assertEqual(roles_order(self.base),('recorder','sidecar','native','proxy'))
        self.assertEqual(roles_order(self.qa)[0],'redis')
        for changed in (dict(purpose='native-manual-play'), dict(profile='manual-luna-v1')):
            with self.assertRaises(ManualRuntimeError):validate_profile({**self.qa,**changed})
        with self.assertRaises(ManualRuntimeError):validate_profile({**self.base,'settings':self.qa['settings']})
    def test_exact_loopback_specs_and_no_ambient_provider_environment(self):
        spec=redis_spec(ValidatedRuntime(canonical(self.qa),digest(self.qa),2))
        self.assertEqual(spec.arguments,(str(self.dep/'qa-redis/redis-server'),str(self.dep/'qa-redis/redis.conf')))
        self.assertEqual(set(spec.environment),{'PATH','LANG'})
        args=native_overrides(self.qa)
        self.assertIn('--native.qa.callback-initially-paused=true',args)
        self.assertIn('--cache.redis.key-prefix=qa:'+'a'*32+':',args)
        self.assertEqual(role_uid(self.qa,'redis'),10004)
        with self.assertRaises(ManualRuntimeError):role_uid(self.base,'redis')
    def test_missing_binary_lock_wrong_bytes_and_uid_or_budget_fail(self):
        additions=dependency_additions(self.qa)
        self.assertEqual(set(additions),{'qa-redis-lock.json','qa-redis/redis-server','qa-redis/redis.conf','qa-redis/loader'})
        (self.dep/'qa-redis/redis.conf').write_bytes(CONFIG+b'bind 0.0.0.0\n')
        with self.assertRaises(ManualRuntimeError):dependency_additions(self.qa)
        for key,value in [('uid',10001),('memoryMaxBytes',128*1024**2),('snapshotBytes',True),('runId','not-a-pin')]:
            changed=copy.deepcopy(self.qa);changed['redisQa'][key]=value
            with self.assertRaises(ManualRuntimeError):validate_profile(changed)
    def test_p_q_authority_requires_new_role_inventory_and_exact_dependency_delta(self):
        from test_manual_runtime_qa import paired_profiles
        from commander_gym import manual_runtime_qa as qa_module
        root=Path(self.temp.name)/'paired';root.mkdir()
        production,qa,ci,authority=paired_profiles(root)
        qa['profile']=PROFILE;qa['settings']['redisEnabled']=True;qa['redisQa']=self.qa['redisQa']
        deps=qa['artifacts']['dependencies']
        additions={key:value for key,value in self.qa['artifacts']['dependencies']['files'].items() if key.startswith('qa-redis')}
        deps['files'].update(additions)
        for name in additions:
            target=Path(deps['root'])/name;target.parent.mkdir(exist_ok=True)
            target.write_bytes((self.dep/name).read_bytes())
        authority['qaProfileSha256']=digest(qa)
        required={'units/redis.service',*[f'implementation/commander_gym/{name}' for name in
                 ('qa_redis_variant.py','qa_redis_entry.py','qa_redis_recovery.py','manual_runtime_qa_roles.py','qa_callback_drain.py')]}
        authority['executionInventory'].update({name:'a'*64 for name in required})
        with patch.object(qa_module,'ROOT',root),patch('commander_gym.qa_redis_variant.protected_bytes',side_effect=lambda path,**_:path.read_bytes()):
            result=validate_authority(authority,production,qa,ci,now=1000)
            self.assertFalse(result['activationEligible'])
            self.assertEqual(result['redisDependencyAdditions'],additions)
            self.assertNotIn('dependencies',result['unchangedRoleInventories'])
            missing=copy.deepcopy(authority);missing['executionInventory'].pop('units/redis.service')
            with self.assertRaises(ManualRuntimeError):validate_authority(missing,production,qa,ci,now=1000)
            qa['artifacts']['dependencies']['files']['unapproved']='a'*64
            with self.assertRaises(ManualRuntimeError):match_profiles(production,qa)

    def test_snapshot_proof_requires_exact_boot_pins_bytes_and_manual_identity(self):
        drain=dict(bootId='boot',releaseId='a'*64,pins=dict(engine='b'*40,gym='c'*40),runId='synthetic')
        proof=dict(gameId='synthetic',snapshotSha256='d'*64,snapshotBytes=100,stateDigest='e'*64,manualHumanStart=True,playerIdentitySha256='f'*64)
        response=dict(protocol=1,ok=True,bootId='boot',releaseId='a'*64,engineSha='b'*40,gymSha='c'*40,snapshot=proof)
        self.assertEqual(validate_snapshot(response,drain),proof)
        for key,value in [('snapshotBytes',True),('snapshotBytes',8*1024**2+1),('manualHumanStart',False),('stateDigest','bad')]:
            with self.assertRaises(ValueError):validate_snapshot({**response,'snapshot':{**proof,key:value}},drain)
        with self.assertRaises(ValueError):validate_snapshot({**response,'bootId':'wrong'},drain)
        self.assertEqual(snapshot_request('boot','a'*64,'synthetic')['op'],'qa-redis-snapshot')

class RedisRestoreObserverTests(unittest.TestCase):
    from test_canonical_capture import CanonicalCaptureTests
    fixture=CanonicalCaptureTests.fixture
    def test_real_observer_chain_requires_initial_pause_native_and_journal_prefix(self):
        import json
        from commander_gym.native_game_capture import NativeGameCapture
        from commander_gym.qa_callback_drain import _observe_callback_drain
        from commander_gym.qa_redis_recovery import verify_native_restore
        from commander_gym.game_journal import JournalError
        capture,directory=self.fixture()
        path=directory/'native-000000.ndjson'
        lines=path.read_bytes().splitlines(keepends=True)
        path.write_bytes(b''.join(lines[:-1])) # fresh synthetic fixture, preserve nonterminal before scanning
        capture.scan()
        status=dict(ok=True,bootId='old-boot',releaseId='e'*64,qaCallbackGateEnabled=True,
                    qaCallbackPaused=True,qaCallbackAdmissionDrained=True,qaCallbackActiveHandlers=0,
                    qaCallbackWaitingHandlers=0,qaCallbackAdmissionFailures=0,qaCallbackGeneration=2)
        previous=_observe_callback_drain(directory,lambda:dict(status))
        prior=json.loads(json.loads(lines[-2])['body'])
        state=next(json.loads(json.loads(line)['body'])['payload']['effectiveStateDigest'] for line in reversed(lines[:-1])
                   if json.loads(json.loads(line)['body'])['kind']=='native_transition')
        def append(kind,payload):
            nonlocal prior
            exact={**prior,'sequence':prior['sequence']+1,'previousSha256':hashlib.sha256(json.dumps(prior,separators=(',',':')).encode()).hexdigest(),
                   'kind':kind,'payload':payload,'visibility':'admin','clockEpoch':'new-native-clock','monotonicNanos':prior['monotonicNanos']+1}
            exact.pop('seatId',None)
            raw=json.dumps(exact,separators=(',',':'))
            with path.open('a') as stream:stream.write(json.dumps({'body':raw,'sha256':hashlib.sha256(raw.encode()).hexdigest()})+'\n')
            prior=exact
        capture.close()
        append('resume',dict(previousSourceSequence=prior['sequence'],exactReplayVerified=False))
        append('state_checkpoint',dict(beforeStateDigest=None,after={'synthetic':'state'}))
        append('callback_resume_state',dict(version=2,restoredStateDigest=state))
        resumed=NativeGameCapture(capture.root,capture.declared_pins);self.addCleanup(resumed.close);resumed.scan()
        new_status={**status,'bootId':'new-boot','qaCallbackGeneration':1}
        proof=dict(gameId=directory.name,snapshotSha256='a'*64,snapshotBytes=100,stateDigest=state,manualHumanStart=True,playerIdentitySha256='b'*64)
        old_response=dict(protocol=1,ok=True,bootId='old-boot',releaseId='e'*64,engineSha=previous['pins']['engine'],gymSha=previous['pins']['gym'],snapshot=proof)
        new_response={**old_response,'bootId':'new-boot'}
        with patch('commander_gym.qa_callback_drain.private_status',side_effect=lambda _:dict(new_status)):
            result=verify_native_restore(directory,'/synthetic-only',previous,old_response,new_response)
            self.assertEqual(result['prefixHead'],previous['nativeHead'])
            self.assertFalse(result['canonicalCompletion'])
            for key,value in [('journalHead','f'*64),('nativeHead','f'*64),('nativeBytes',previous['nativeBytes']-1)]:
                with self.subTest(key=key),self.assertRaises(JournalError):
                    verify_native_restore(directory,'/synthetic-only',{**previous,key:value},old_response,new_response)
            with self.assertRaises(JournalError):
                verify_native_restore(directory,'/synthetic-only',previous,old_response,{**new_response,'snapshot':{**proof,'stateDigest':'d'*64}})
        with patch('commander_gym.qa_callback_drain.private_status',return_value={**new_status,'qaCallbackGeneration':0}):
            with self.assertRaises(JournalError):verify_native_restore(directory,'/synthetic-only',previous,old_response,new_response)
