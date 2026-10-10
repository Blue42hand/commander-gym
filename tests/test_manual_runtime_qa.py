"""Isolated source-contract tests; no systemd, network, provider or games."""
import copy
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from commander_gym import manual_runtime_qa as qa
from commander_gym.manual_runtime_profile import ManualRuntimeError, ValidatedRuntime, canonical, digest, precredential_validate, validate_qualification
from commander_gym.manual_runtime_rollout import RuntimeSnapshot, promote
from test_manual_runtime_profile import fixture_profile, qualification, approval
from test_manual_runtime_rollout import FakeHost


def paired_profiles(root):
    q=fixture_profile(root)
    q['recordingAuthority']['registryUid']=0
    for a in q['artifacts'].values():a['uid']=0
    p=copy.deepcopy(q);p['purpose']='native-manual-play'
    p['paths']={'recordingRoot':'/var/lib/commander-gym/runs/native','lifecycleSocket':'/run/argentum-play/lifecycle.sock',
        'recorderSocket':'/run/argentum-luna-ipc/native.sock','registryPath':'/etc/argentum-play/recording-dispositions/acknowledged-incomplete.json'}
    for role,a in p['artifacts'].items():
        a['root']='/usr/local/libexec/argentum-'+('native' if role=='legacyNative' else 'source') if role in ('legacyNative','legacySource') else '/srv/argentum-luna/artifacts/'+role+'/'+digest(a['files'])
    p['ingress']['origins']=['https://tolaria.example.ts.net'];q['ingress']=copy.deepcopy(p['ingress'])
    from commander_gym.manual_runtime_config import service_units,shared_https_nginx
    expected=service_units(launcher_root=p['artifacts']['launcher']['root'],dependency_root=p['artifacts']['dependencies']['root'],python_relative='python/bin/python3.12',tailnet_only=True)
    expected['nginx.conf']=shared_https_nginx(frontend_root=p['artifacts']['frontend']['root'],tailnet_origin=p['ingress']['origins'][0],lan_address=p['ingress']['lanAddress'],lan_subnet=p['ingress']['lanSubnet'],tailnet_only=True)
    inventory={name:hashlib.sha256(text.encode()).hexdigest() for name,text in expected.items()}
    q['artifacts']['config']['files']=inventory;p['artifacts']['config']['files']=inventory
    p['artifacts']['config']['root']='/srv/argentum-luna/artifacts/config/'+digest(inventory)
    ci=qualification(q)['sourceCi']
    value=dict(schemaVersion=1,scope='isolated-qa-observation-only',nonce='1'*32,issuedUnix=1000,expiresUnix=1900,
        productionProfileSha256=digest(p),qaProfileSha256=digest(q),previousRuntimeId='0'*64,sequence=2,
        sourceCiSha256=digest(ci),executionInventory={name:'a'*64 for name in ({f'units/{r}.service' for r in qa.ROLES_ORDER}|{f'probes/{r}' for r in qa.PROBES}|{f'implementation/commander_gym/{n}' for n in qa.IMPLEMENTATION_FILES})},
        limits=qa.LIMITS.copy(),qaMinFreeBytes=64*1024**2,productionAuthoritySha256=p['recordingAuthority']['registrySha256'],qaAuthoritySha256=q['recordingAuthority']['registrySha256'],implementationSha256='4'*64,
        previousProfileSha256=digest(q),previousSelectorSha256='5'*64,qaSourceSha='c'*40,qaSourceCi={'sha':'c'*40,'checks':[{'name':name,'conclusion':'success','runId':2} for name in ('python','manual-runtime-sdk')]})
    return p,q,ci,value


class QAContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='.manual-qa-contract-',dir=Path.cwd());self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.p,self.q,self.ci,self.authority=paired_profiles(self.root)
        self.root_patch=patch.object(qa,'ROOT',self.root);self.root_patch.start();self.addCleanup(self.root_patch.stop)

    def test_exact_p_q_mapping_and_no_authority_transfer(self):
        result=qa.validate_authority(self.authority,self.p,self.q,self.ci,now=1000)
        self.assertFalse(result['activationEligible']);self.assertFalse(result['productionQualificationIssued'])
        for mutation in (lambda q:q['artifacts']['server']['files'].update(extra='a'*64),lambda q:q['settings'].update(manualOnly=False),lambda q:q['paths'].update(recordingRoot='/var/lib/commander-gym/runs/native')):
            q=copy.deepcopy(self.q);mutation(q)
            with self.assertRaises(ManualRuntimeError):qa.match_profiles(self.p,q)

    def test_scope_freshness_limits_nonce_and_exact_ci_fail_closed(self):
        for mutate in (lambda a:a.update(scope='activate-manual-runtime'),lambda a:a.update(sequence=True),lambda a:a.update(expiresUnix=1901),lambda a:a.update(nonce='../escape'),lambda a:a.update(qaProfileSha256='9'*64),lambda a:a.update(productionAuthoritySha256='9'*64),lambda a:a.update(qaAuthoritySha256='8'*64),lambda a:a['limits'].update(guestMemoryBytes=3*1024**3),lambda a:a.update(qaMinFreeBytes=True),lambda a:a['executionInventory'].update({'probes/foreign':'a'*64})):
            a=copy.deepcopy(self.authority);mutate(a)
            with self.subTest(a=a),self.assertRaises(ManualRuntimeError):qa.validate_authority(a,self.p,self.q,self.ci,now=1000)
        with self.assertRaisesRegex(ManualRuntimeError,'qa_authority_stale'):qa.validate_authority(self.authority,self.p,self.q,self.ci,now=1901)
        ci=copy.deepcopy(self.ci);ci['engine']['checks'][0]['conclusion']='skipped';a={**self.authority,'sourceCiSha256':digest(ci)}
        with self.assertRaisesRegex(ManualRuntimeError,'qa_ci_failed'):qa.validate_authority(a,self.p,self.q,ci,now=1000)

    def test_qa_profile_and_manifest_can_never_be_production_receipt_or_approval(self):
        receipt=qualification(self.q)
        with self.assertRaisesRegex(ManualRuntimeError,'qa_never_activates'):precredential_validate(self.q,receipt,approval(self.q,receipt),previous_id='0'*64,sequence=2,now=1000)
        with self.assertRaisesRegex(ManualRuntimeError,'qualification_fields'):validate_qualification(self.p,{'productionQualificationIssued':False,'observations':{}},now=1000)
        with self.assertRaisesRegex(ManualRuntimeError,'qa_dedicated_host_required'):qa.promote_qa(FakeHost(None))
        class Subclass(qa.QAFileHost):pass
        with self.assertRaisesRegex(ManualRuntimeError,'qa_dedicated_host_required'):qa.promote_qa(Subclass.__new__(Subclass))

    def test_registry_preserves_existing_root_private_native_group_read_contract(self):
        import types
        path=Path(self.q['paths']['registryPath']);raw=path.read_bytes()
        actual=Path.lstat
        def metadata(path):
            rows=list(actual(path));rows[4]=0;rows[5]=777;return os.stat_result(rows)
        with patch.object(qa,'protected_bytes',return_value=raw),patch.object(Path,'lstat',metadata),patch.object(qa.pwd,'getpwuid',return_value=types.SimpleNamespace(pw_gid=777)):
            path.chmod(0o640);self.assertEqual(qa.registry_bytes(self.q),raw)
            with patch.object(qa.pwd,'getpwuid',return_value=types.SimpleNamespace(pw_gid=778)),self.assertRaisesRegex(ManualRuntimeError,'qa_registry_reader_group'):qa.registry_bytes(self.q)
            path.chmod(0o644)
            with self.assertRaisesRegex(ManualRuntimeError,'qa_registry_file'):qa.registry_bytes(self.q)
            path.chmod(0o600);self.assertEqual(qa.registry_bytes(self.q),raw)

    def test_qa_implementation_has_its_own_exact_ci_and_import_inventory(self):
        for mutate in (lambda a:a['qaSourceCi'].update(sha='d'*40),lambda a:a['qaSourceCi']['checks'][0].update(conclusion='skipped'),lambda a:a['executionInventory'].pop('implementation/commander_gym/manual_runtime_rollout.py'),lambda a:a['executionInventory'].update({'implementation/commander_gym/../escape.py':'a'*64})):
            value=copy.deepcopy(self.authority);mutate(value)
            with self.assertRaises(ManualRuntimeError):qa.validate_authority(value,self.p,self.q,self.ci,now=1000)

    def test_real_implementation_inventory_and_loaded_unit_identity_reject_extras(self):
        import types
        host=qa.QAFileHost.__new__(qa.QAFileHost);host.authority=copy.deepcopy(self.authority)
        implementation=self.root/'implementation/commander_gym';implementation.mkdir(parents=True)
        for name in qa.IMPLEMENTATION_FILES:
            path=implementation/name;path.write_bytes(b'sealed source fixture')
            host.authority['executionInventory']['implementation/commander_gym/'+name]=qa.raw_digest(path.read_bytes())
        for name in host.authority['executionInventory']:
            path=self.root/name
            if not path.exists():path.parent.mkdir(exist_ok=True);path.write_bytes(b'sealed fixture')
            host.authority['executionInventory'][name]=qa.raw_digest(path.read_bytes())
        host.units={'native':'nonce-native'};host._show=Mock(return_value={'FragmentPath':str(self.root/'units/native.service'),'DropInPaths':'','NeedDaemonReload':'no'})
        def imported(name):return types.SimpleNamespace(__code__=types.SimpleNamespace(co_filename=str(implementation/name)))
        with patch.object(qa,'_promote',imported('manual_runtime_rollout.py')),patch.object(qa,'validate_profile',imported('manual_runtime_profile.py')),patch.object(qa,'capture_inventory',imported('manual_runtime_quiescence.py')),patch.object(qa,'root_bytes',side_effect=lambda path:Path(path).read_bytes()):
            host._inventory()
            host._show.return_value['NeedDaemonReload']='yes'
            with self.assertRaisesRegex(ManualRuntimeError,'qa_loaded_unit_mismatch'):host._inventory()
            host._show.return_value['NeedDaemonReload']='no';extra=implementation/'unreviewed.py';extra.write_bytes(b'hold')
            with self.assertRaisesRegex(ManualRuntimeError,'qa_unindexed_implementation'):host._inventory()
            extra.unlink();(implementation/'manual_runtime_rollout.py').write_bytes(b'changed')
            with self.assertRaisesRegex(ManualRuntimeError,'qa_execution_changed'):host._inventory()

    def test_production_entry_still_validates_before_any_mutating_effect(self):
        runtime=ValidatedRuntime(canonical(self.q),digest(self.q),2);host=FakeHost(runtime)
        with self.assertRaisesRegex(ManualRuntimeError,'qa_never_activates'):promote(host,self.q,{}, {},clock=lambda:1000)
        self.assertNotIn('drain',host.events);self.assertNotIn('begin',host.events)

    def test_actual_shared_transaction_qa_validator_has_no_circular_receipt(self):
        host=qa.QAFileHost.__new__(qa.QAFileHost)
        runtime=ValidatedRuntime(canonical(self.q),digest(self.q),2);fake=FakeHost(runtime)
        host.qa=self.q;host.authority=self.authority;host._fresh=Mock()
        for name in ('exclusive_lock','assert_no_unfinished_transaction','current','verify_snapshot','status','drain','begin','stop_closure','quiescence_barrier','select','start_closed','complete','complete_rollback','resume','held'):
            setattr(host,name,getattr(fake,name))
        with patch.object(qa.time,'time',return_value=1000):result=qa.promote_qa(host)
        self.assertEqual(result['state'],'promoted');self.assertLess(fake.events.index('commit'),fake.events.index('resume'))
        fake=FakeHost(runtime);fake.resume_failure=True
        for name in ('exclusive_lock','assert_no_unfinished_transaction','current','verify_snapshot','status','drain','begin','stop_closure','quiescence_barrier','select','start_closed','complete','complete_rollback','resume','held'):setattr(host,name,getattr(fake,name))
        with patch.object(qa.time,'time',return_value=1000),self.assertRaisesRegex(ManualRuntimeError,'committed_resume_unconfirmed'):qa.promote_qa(host)
        self.assertNotIn('select:previous',fake.events)

    def file_host(self):
        host=qa.QAFileHost.__new__(qa.QAFileHost);host.state=self.root/'qa/state';host.state.mkdir();(host.state/'observations').mkdir()
        host.locked=True;host.qa=self.q;host.production=self.p;host.authority=self.authority;host.raw=b'fixture authority';host.pending=None;host._fresh=Mock()
        return host
    @contextmanager
    def fixture_io(self):
        with patch.object(qa,'root_bytes',side_effect=lambda p,*args:Path(p).read_bytes()),patch.object(qa,'write',side_effect=lambda p,v:Path(p).write_bytes(canonical(v))),patch.object(qa,'verify_artifacts'):yield

    def test_real_selector_bank_and_nonce_survive_recreation_and_reject_tamper(self):
        host=self.file_host();previous=RuntimeSnapshot('0'*64,1,'keyless',self.q['engineSha'],self.q['gymSha'],1)
        selector=previous.__dict__.copy();self.authority['previousSelectorSha256']=digest(selector)
        (host.state/'selector.json').write_bytes(canonical(selector));(host.state/'service-profile.json').write_bytes(canonical(self.q))
        candidate=ValidatedRuntime(canonical(self.q),digest(self.q),2)
        with self.fixture_io():
            host.begin(previous,candidate);self.assertTrue((host.state/'used.json').exists())
            host.select(candidate);self.assertEqual(host.current().runtime_id,candidate.runtime_id)
            host.select(previous);self.assertEqual(host.current().sequence,2);self.assertEqual(host.current().runtime_id,previous.runtime_id)
            host.complete_rollback(previous,2)
            with self.assertRaisesRegex(ManualRuntimeError,'qa_single_use'):host.assert_no_unfinished_transaction()
            bank=host._json('bank.json');bank['selector']['sequence']=999;(host.state/'bank.json').write_bytes(canonical(bank))
            with self.assertRaisesRegex(ManualRuntimeError,'qa_bank_changed'):host.select(previous)

    def test_foreign_selected_profile_and_possible_admission_recovery_hold(self):
        host=self.file_host();(host.state/'transaction.json').write_bytes(canonical({'phase':'completed'}))
        (host.state/'bank.json').write_bytes(b'{}')
        @contextmanager
        def locked():yield
        host.exclusive_lock=locked
        with self.fixture_io(),self.assertRaisesRegex(ManualRuntimeError,'qa_possible_admission_held'):qa.recover_qa_closed(host)
        (host.state/'transaction.json.next').write_bytes(b'unknown pending')
        with self.fixture_io(),self.assertRaisesRegex(ManualRuntimeError,'qa_pending_write'):qa.recover_qa_closed(host)

    def test_observations_manifest_keeps_all_production_gaps(self):
        host=self.file_host()
        with self.fixture_io():
            host.observe('transaction',{'phase':'completed','outcome':'qa-promoted'})
            result=qa.evidence_manifest(host)
        self.assertFalse(result['productionGateBooleansTransferred']);self.assertEqual(set(result['unestablishedProductionGates']),qa.GATES)
        self.assertEqual(len(result['observations']),1)
        raw=next((host.state/'observations').glob('*.json')).read_bytes();self.assertEqual(next(iter(result['observations'].values())),hashlib.sha256(raw).hexdigest())

    def test_complete_cgroup_shutdown_checks_control_pid_and_descendants(self):
        host=self.file_host();host.units={'native':'commander-gym-qa-fixture-native.service'};host.pending={'stopContext':{},'stopInventory':{}}
        host._show=Mock(return_value={'ActiveState':'inactive','MainPID':'0','ControlPID':'1','ControlGroup':''})
        self.assertFalse(host.quiescence_barrier())
        host._show.return_value={'ActiveState':'inactive','MainPID':'0','ControlPID':'0','ControlGroup':'/../escape'}
        with self.assertRaisesRegex(ManualRuntimeError,'qa_cgroup_path'):host.quiescence_barrier()

    def test_previous_mapping_cannot_redirect_lifecycle_or_capture_authority(self):
        with patch.object(qa,'verify_artifacts'):
            qa.validate_previous(self.q,self.q)
            for mutate in (lambda p:p['paths'].update(lifecycleSocket=str(self.root/'qa/other.sock')),lambda p:p['identities'].update(nativeUid=99999),lambda p:p['artifacts']['server'].update(root='/outside'),lambda p:p['recordingAuthority'].update(registrySha256='9'*64)):
                previous=copy.deepcopy(self.q);mutate(previous)
                with self.assertRaises(ManualRuntimeError):qa.validate_previous(previous,self.q)

    def test_native_rpc_envelope_has_required_protocol_and_exact_socket(self):
        host=self.file_host();client=Mock();client.recv.return_value=canonical({'protocol':1,'ok':True,'nativeFixture':'keyless'})+b'\n'
        manager=Mock();manager.__enter__=Mock(return_value=client);manager.__exit__=Mock(return_value=False)
        import struct
        client.getsockopt.return_value=struct.pack('3i',100,self.q['identities']['nativeUid'],100)
        sock_info=Mock(st_mode=0o140600,st_uid=self.q['identities']['nativeUid']);dir_info=Mock(st_mode=0o040700,st_uid=self.q['identities']['nativeUid'])
        with self.fixture_io(),patch.object(qa.socket,'socket',return_value=manager),patch.object(qa.socket,'SO_PEERCRED',17,create=True),patch.object(Path,'lstat',autospec=True,side_effect=lambda p: sock_info if p==Path(self.q['paths']['lifecycleSocket']) else dir_info):
            host.rpc('status')
        sent=qa.decode(client.sendall.call_args.args[0].strip())
        self.assertEqual(sent,{'protocol':1,'op':'status'});client.connect.assert_called_once_with(self.q['paths']['lifecycleSocket'])

    def test_actual_file_crash_after_full_or_partial_selection_recovers_previous_closed(self):
        for partial in (False,True):
            with self.subTest(partial=partial):
                host=self.file_host();previous=RuntimeSnapshot('0'*64,1,'keyless',self.q['engineSha'],self.q['gymSha'],1)
                selector=previous.__dict__.copy();self.authority['previousSelectorSha256']=digest(selector)
                (host.state/'selector.json').write_bytes(canonical(selector));(host.state/'service-profile.json').write_bytes(canonical(self.q))
                candidate=ValidatedRuntime(canonical(self.q),digest(self.q),2)
                @contextmanager
                def locked():yield
                host.exclusive_lock=locked;host.units={'native':'fixed-qa-native'}
                host._show=Mock(side_effect=lambda unit,properties:({'MainPID':'0','ControlPID':'0'} if properties==('MainPID','ControlPID') else {'ActiveState':'inactive','MainPID':'0','ControlPID':'0'}))
                host.start_closed=Mock();host.quiescence_barrier=Mock(return_value=True)
                with self.fixture_io():
                    host.begin(previous,candidate)
                    context={'runtimeId':previous.runtime_id,'stopNonce':self.authority['nonce'],'captureInventorySha256':digest({})}
                    (host.state/'stop-context.json').write_bytes(canonical({'context':context,'inventory':{}}))
                    if partial:(host.state/'service-profile.json').write_bytes(candidate.profile_bytes)
                    else:host.select(candidate)
                    # Simulate a new controller after process loss, retain files.
                    host.pending=None
                    host.status=Mock(side_effect=lambda:dict(releaseId=previous.runtime_id,engineSha=previous.engine_sha,gymSha=previous.gym_sha,recordingSchemaVersion=1,bootId='qa-restored',observedUnix=1000,recordingHealthy=True,recoveryComplete=True,activeGames=0,pendingActivities=0,pendingRecordWrites=0,inFlightAdmissions=0,acceptingNewGames=False,drainAcknowledged=True,recordingDrainComplete=True))
                    with patch.object(qa.time,'time',return_value=1000):result=qa.recover_qa_closed(host)
                    self.assertEqual(result['state'],'qa-previous-restored-closed');self.assertEqual(host.current().runtime_id,previous.runtime_id)
                    self.assertEqual(host.current().sequence,2);self.assertEqual(host._json('transaction.json')['phase'],'rolled-back-closed')
                import shutil
                shutil.rmtree(host.state)

    def test_missing_symlinked_and_busy_cgroup_evidence_fail_closed(self):
        root=self.root/'cgroups';root.mkdir();(root/'cgroup.procs').write_text('');child=root/'child';child.mkdir();(child/'cgroup.procs').write_text('')
        real_path=Path
        with patch.object(qa,'Path',side_effect=lambda p:root if p=='/sys/fs/cgroup' else real_path(p)):
            self.assertTrue(qa.cgroup_empty('/'))
            (child/'cgroup.procs').write_text('42\n');self.assertFalse(qa.cgroup_empty('/'))
            (child/'cgroup.procs').unlink()
            with self.assertRaises(FileNotFoundError):qa.cgroup_empty('/')
            (child/'cgroup.procs').symlink_to(root/'cgroup.procs')
            with self.assertRaisesRegex(ManualRuntimeError,'qa_cgroup_procs'):qa.cgroup_empty('/')
            (child/'cgroup.procs').unlink();child.rmdir();(root/'linked').symlink_to(root,target_is_directory=True)
            with self.assertRaisesRegex(ManualRuntimeError,'qa_cgroup_symlink'):qa.cgroup_empty('/')

    def test_actual_bounded_probe_process_preserves_output_and_failure_evidence(self):
        host=self.file_host();folder=self.root/'probes';folder.mkdir();program=folder/'admission'
        program.write_text("#!/bin/sh\nprintf '%s\\n' '{\"syntheticFixture\":\"contract-only\"}'\n");program.chmod(0o700)
        with self.fixture_io():
            result=host.probe('admission');self.assertEqual(result,{'syntheticFixture':'contract-only'})
            import base64
            record=next((host.state/'observations').glob('*.json'))
            evidence=qa.decode(record.read_bytes())['value']
            self.assertEqual(qa.raw_digest(base64.b64decode(evidence['stdoutBase64'])),evidence['stdoutSha256'])
            program.write_text('#!/bin/sh\nprintf "fixture failure" >&2\nexit 7\n');program.chmod(0o700)
            with self.assertRaisesRegex(ManualRuntimeError,'qa_probe_failed'):host.probe('admission')
            result=qa.decode(sorted((host.state/'observations').glob('*.json'))[-1].read_bytes())['value']
            self.assertEqual(result['exitCode'],7);self.assertEqual(base64.b64decode(result['stderrBase64']),b'fixture failure')
            program.write_text('#!/bin/sh\nprintf not-json\n');program.chmod(0o700)
            with self.assertRaisesRegex(ManualRuntimeError,'invalid_json'):host.probe('admission')
            record=qa.decode(sorted((host.state/'observations').glob('*.json'))[-1].read_bytes())['value']
            self.assertEqual(record['exitCode'],0);self.assertEqual(base64.b64decode(record['stdoutBase64']),b'not-json')
            with self.assertRaisesRegex(ManualRuntimeError,'qa_probe_role'):host.probe('foreign')

    def test_write_rejects_escape_and_pending_file(self):
        with self.assertRaisesRegex(ManualRuntimeError,'qa_write_escape'):qa.write(self.root/'outside.json',{})
        # Local nonroot fixtures substitute only ownership checks; the atomic
        # O_EXCL path is real and cannot overwrite an ambiguous pending write.
        folder=self.root/'qa/atomic';folder.mkdir();pending=folder/'state.json.next';pending.write_bytes(b'preserve')
        real=Path.lstat
        def metadata(path):
            rows=list(real(path));rows[4]=0;return os.stat_result(rows)
        with patch.object(Path,'lstat',metadata),self.assertRaises(FileExistsError):qa.write(folder/'state.json',{})
        self.assertEqual(pending.read_bytes(),b'preserve')

if __name__=='__main__':unittest.main()
