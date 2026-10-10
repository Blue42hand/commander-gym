"""Nondeployable, single-use QA transaction and evidence contract.

No production host is imported and no qualification/approval receipt is issued.
Only an explicitly staged isolated Linux guest may execute this contract. Import
and metadata validation perform no service, credential or network operations.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import base64
import fcntl
import hashlib
import math
import resource
import re
import tempfile
import os
import pwd
from pathlib import Path
import socket
import stat
import struct
import subprocess
import sys
import time

from .manual_runtime_profile import (
    GATES, SHA, ROLES, ValidatedRuntime, canonical, decode, digest,
    protected_bytes, require, validate_profile, verify_artifacts,
)
from .manual_runtime_rollout import RuntimeSnapshot, _promote, idle_state
from .manual_runtime_quiescence import capture_inventory, stop_context

AUTHORITY = Path('/etc/commander-gym-qa/authority.json')
ROOT = Path('/opt/commander-gym-qa')
PROBES = ('admission', 'native-writer', 'recording-recovery', 'websocket')
ROLES_ORDER = ('recorder', 'sidecar', 'native', 'proxy')
LIMITS = {'allocatedBytes': 2*1024**3, 'guestMemoryBytes': 2*1024**3,
          'supervisorMemoryBytes': 3*1024**3, 'swapBytes': 0, 'cpuPercent': 100,
          'tasks': 128, 'runtimeSeconds': 900}
# These are expectations on the supervisor, not an installer or resource grant.
IMPLEMENTATION_FILES = ('__init__.py','manual_runtime_qa.py','manual_runtime_rollout.py',
    'manual_runtime_profile.py','manual_runtime_quiescence.py','manual_runtime_config.py')
AUTH_FIELDS = {'schemaVersion', 'scope', 'nonce', 'issuedUnix', 'expiresUnix',
    'productionProfileSha256', 'qaProfileSha256', 'previousRuntimeId', 'sequence',
    'sourceCiSha256', 'executionInventory', 'limits', 'qaMinFreeBytes',
    'productionAuthoritySha256', 'qaAuthoritySha256', 'implementationSha256',
    'previousProfileSha256', 'previousSelectorSha256', 'qaSourceSha', 'qaSourceCi'}


def raw_digest(raw): return hashlib.sha256(raw).hexdigest()


def root_bytes(path, maximum=4*1024*1024):
    raw = protected_bytes(path, uid=0, max_bytes=maximum)
    info = path.lstat()
    require(info.st_nlink == 1 and stat.S_IMODE(info.st_mode) & 0o077 == 0, 'qa_root_file')
    return raw


def registry_bytes(profile):
    path = Path(profile['paths']['registryPath'])
    raw = protected_bytes(path,uid=0,max_bytes=4*1024*1024)
    info = path.lstat();mode = stat.S_IMODE(info.st_mode)
    require(info.st_nlink == 1 and mode in (0o600,0o640),'qa_registry_file')
    if mode == 0o640:
        require(info.st_gid == pwd.getpwuid(profile['identities']['nativeUid']).pw_gid,'qa_registry_reader_group')
    return raw


def match_profiles(production, qa):
    """Explicit P→Q substitutions; executable/catalog/helper inventories match."""
    validate_profile(production); validate_profile(qa)
    require(production['purpose'] == 'native-manual-play'
            and qa['purpose'] == 'qualification', 'qa_profile_purposes')
    for key in set(production) - {'purpose', 'artifacts', 'paths', 'recordingAuthority'}:
        require(production[key] == qa[key], 'qa_profile_delta')
    # Root remapping is necessary in Q; sealed config is carried as P bytes and
    # never installed. Separately sealed QA units are the explicit execution seam.
    for role in ROLES:
        require(production['artifacts'][role]['files'] == qa['artifacts'][role]['files'], 'qa_artifact_delta')
        require(Path(qa['artifacts'][role]['root']).is_relative_to(ROOT/'artifacts')
                and qa['artifacts'][role]['uid'] == 0, 'qa_artifact_layout')
    require(all(Path(path).is_relative_to(ROOT/'qa') for path in qa['paths'].values()), 'qa_path_escape')
    require(qa['recordingAuthority']['registryUid'] == 0, 'qa_registry_custody')
    return {'productionProfileSha256': digest(production), 'qaProfileSha256': digest(qa),
            'unchangedRoleInventories': {role: digest(qa['artifacts'][role]['files']) for role in sorted(ROLES)},
            'substitutions': ['purpose', 'artifactRoots', 'runtimePaths', 'recordingAuthority'],
            'productionQualificationIssued': False, 'activationEligible': False}


def validate_previous(previous, candidate):
    validate_profile(previous)
    require(previous['purpose'] == 'qualification' and previous['paths'] == candidate['paths']
            and previous['identities'] == candidate['identities'], 'qa_previous_mapping')
    require(previous['recordingAuthority'] == candidate['recordingAuthority'], 'qa_previous_registry')
    for artifact in previous['artifacts'].values():
        require(artifact['uid'] == 0 and Path(artifact['root']).is_relative_to(ROOT/'artifacts'), 'qa_previous_artifact_layout')
    verify_artifacts(previous)


def validate_authority(value, production, qa, ci, *, now):
    require(type(value) is dict and set(value) == AUTH_FIELDS, 'qa_authority_fields')
    require(type(value['schemaVersion']) is int and value['schemaVersion'] == 1
            and value['scope'] == 'isolated-qa-observation-only', 'qa_authority_scope')
    require(type(value['nonce']) is str and len(value['nonce']) == 32
            and all(c in '0123456789abcdef' for c in value['nonce']), 'qa_nonce')
    require(all(type(value[k]) in (int, float) and math.isfinite(value[k]) for k in ('issuedUnix','expiresUnix'))
            and 0 <= now-value['issuedUnix'] <= 900 and now <= value['expiresUnix']
            and 0 < value['expiresUnix']-value['issuedUnix'] <= 900, 'qa_authority_stale')
    match = match_profiles(production, qa)
    require(all(value[k] == match[k] for k in ('productionProfileSha256','qaProfileSha256')), 'qa_exact_profiles')
    require(value['limits'] == LIMITS and all(type(value['limits'][k]) is int for k in LIMITS), 'qa_resource_limits')
    require(type(value['qaMinFreeBytes']) is int and 64*1024**2 <= value['qaMinFreeBytes'] <= 256*1024**2, 'qa_test_reserve')
    require(type(value['sequence']) is int and value['sequence'] >= 2
            and type(value['previousRuntimeId']) is str and SHA.fullmatch(value['previousRuntimeId']), 'qa_previous_sequence')
    for k in ('productionAuthoritySha256','qaAuthoritySha256','implementationSha256','sourceCiSha256','previousProfileSha256','previousSelectorSha256'):
        require(type(value[k]) is str and SHA.fullmatch(value[k]), 'qa_authority_seals')
    require(value['sourceCiSha256'] == digest(ci), 'qa_ci_seal')
    require(type(ci) is dict and set(ci) == {'engine','gym'}, 'qa_ci_roles')
    for role, required in (('engine',{'coverage'}),('gym',{'python','jvm-adapter','manual-runtime-sdk'})):
        row = ci[role]
        require(type(row) is dict and set(row) == {'sha','checks'} and row['sha'] == qa[role+'Sha'], 'qa_ci_identity')
        checks = row['checks']
        require(type(checks) is list and checks and all(type(c) is dict and set(c) == {'name','conclusion','runId'}
            and type(c['name']) is str and c['name'] and c['conclusion'] == 'success'
            and type(c['runId']) is int and c['runId'] > 0 for c in checks), 'qa_ci_failed')
        names = [c['name'] for c in checks]
        require(len(names) == len(set(names)) and required <= set(names), 'qa_ci_missing')
    from .manual_runtime_profile import COMMIT
    require(type(value['qaSourceSha']) is str and COMMIT.fullmatch(value['qaSourceSha']), 'qa_implementation_source')
    implementation_ci = value['qaSourceCi']
    require(type(implementation_ci) is dict and set(implementation_ci) == {'sha','checks'}
            and implementation_ci['sha'] == value['qaSourceSha'], 'qa_implementation_ci_identity')
    checks = implementation_ci['checks']
    require(type(checks) is list and checks and all(type(c) is dict and set(c) == {'name','conclusion','runId'}
            and type(c['name']) is str and c['name'] and c['conclusion'] == 'success'
            and type(c['runId']) is int and c['runId'] > 0 for c in checks), 'qa_implementation_ci_failed')
    names = [c['name'] for c in checks]
    require(len(names) == len(set(names)) and {'python','manual-runtime-sdk'} <= set(names), 'qa_implementation_ci_missing')
    inventory = value['executionInventory']
    required = ({f'units/{role}.service' for role in ROLES_ORDER} | {f'probes/{role}' for role in PROBES}
                | {f'implementation/commander_gym/{name}' for name in IMPLEMENTATION_FILES})
    require(type(inventory) is dict and required <= set(inventory) and len(inventory) <= 5000
            and all(type(name) is str and (name in required or re.fullmatch(r'implementation/commander_gym/(?:[a-zA-Z0-9_]+/)*[a-zA-Z0-9_]+\.py',name))
                    and type(sha) is str and SHA.fullmatch(sha) for name,sha in inventory.items()), 'qa_execution_inventory')
    return match


def guest_boundary():
    require(sys.platform == 'linux' and os.geteuid() == 0, 'qa_linux_root')
    # Root-sealed operator marker is staged only in the disposable guest. Root
    # custody alone cannot prove physical backing, host reserve or live ingress.
    require(root_bytes(ROOT/'isolated-guest.marker', 128) == b'commander-gym-isolated-qa-v1\n', 'qa_guest_marker')
    require({p.name for p in Path('/sys/class/net').iterdir()} == {'lo'}, 'qa_external_nic')
    for path in ('/etc/argentum-luna/operator-policy.json', '/etc/argentum-play/qualifications',
                 '/var/lib/commander-gym/runs/native', '/run/credentials'):
        require(not Path(path).exists() and not Path(path).is_symlink(), 'qa_production_mount')
    # The marker is authority to run Q only; no parent/root mount is accepted.
    mounts = Path('/proc/self/mountinfo').read_text().splitlines()
    for line in mounts:
        fields = line.split(); sep = fields.index('-')
        require(fields[sep+1] not in ('9p','virtiofs','nfs','nfs4','cifs'), 'qa_host_share')


def cgroup_empty(group):
    require(group.startswith('/') and '..' not in group.split('/') and '//' not in group,'qa_cgroup_path')
    root = Path('/sys/fs/cgroup')/group.lstrip('/')
    for parent in (root,*root.parents):
        info = parent.lstat();require(stat.S_ISDIR(info.st_mode),'qa_cgroup_directory')
    queue = [root];count = 0
    while queue:
        directory = queue.pop();count += 1;require(count <= 4096,'qa_cgroup_size')
        info = directory.lstat();require(stat.S_ISDIR(info.st_mode),'qa_cgroup_directory')
        procs = directory/'cgroup.procs';require(stat.S_ISREG(procs.lstat().st_mode),'qa_cgroup_procs')
        if procs.read_text().strip():return False
        for path in directory.iterdir():
            info = path.lstat();require(not stat.S_ISLNK(info.st_mode),'qa_cgroup_symlink')
            if stat.S_ISDIR(info.st_mode):queue.append(path)
    return True


def write(path, value):
    require(path.parent.is_relative_to(ROOT/'qa'), 'qa_write_escape')
    parent = path.parent.lstat()
    require(stat.S_ISDIR(parent.st_mode) and parent.st_uid == 0 and parent.st_mode & 0o022 == 0, 'qa_state_directory')
    if path.exists() or path.is_symlink(): root_bytes(path)
    pending = path.with_name(path.name+'.next')
    fd = os.open(pending, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd,'wb') as stream:
        stream.write(canonical(value)); stream.flush(); os.fsync(stream.fileno())
    os.replace(pending,path)
    fd = os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)


class QAFileHost:
    """Fixed guest-only services/state and real lifecycle/recording observations.

    Supervisor/units/probes must already be separately reviewed and staged.
    This class neither stages units nor raises resource limits.
    """
    def __init__(self):
        guest_boundary()
        self.raw = root_bytes(AUTHORITY)
        self.authority = decode(self.raw)
        self.production = decode(root_bytes(ROOT/'production-profile.json'))
        self.qa = decode(root_bytes(ROOT/'qa-profile.json'))
        self.ci = decode(root_bytes(ROOT/'source-ci.json'))
        validate_authority(self.authority,self.production,self.qa,self.ci,now=time.time())
        require(raw_digest(root_bytes(ROOT/'production-authority.json')) == self.authority['productionAuthoritySha256'], 'qa_p_authority_seal')
        require(raw_digest(registry_bytes(self.qa)) == self.authority['qaAuthoritySha256'], 'qa_q_authority_seal')
        require(Path(__file__).resolve() == ROOT/'implementation/commander_gym/manual_runtime_qa.py', 'qa_implementation_layout')
        require(raw_digest(root_bytes(Path(__file__))) == self.authority['implementationSha256'], 'qa_implementation_seal')
        verify_artifacts(self.qa)
        self.state = ROOT/'qa/state'; self.locked = False; self.pending = None
        self.units = {role: 'commander-gym-qa-'+self.authority['nonce']+'-'+role+'.service' for role in ROLES_ORDER}
        self._inventory()
        if (self.state/'bank.json').exists():
            bank = self._json('bank.json');require(digest(bank) == self._json('transaction.json')['bankSha256'],'qa_bank_changed')
            require(digest(bank['profile']) == self.authority['previousProfileSha256'] and digest(bank['selector']) == self.authority['previousSelectorSha256'],'qa_initial_bank_identity')
            previous_profile = bank['profile']
        else:
            require(digest(self._json('service-profile.json')) == self.authority['previousProfileSha256']
                    and digest(self._json('selector.json')) == self.authority['previousSelectorSha256'], 'qa_initial_bank_identity')
            previous_profile = self._json('service-profile.json')
        validate_previous(previous_profile,self.qa)

    def _inventory(self):
        require(Path(_promote.__code__.co_filename).resolve() == ROOT/'implementation/commander_gym/manual_runtime_rollout.py'
                and Path(validate_profile.__code__.co_filename).resolve() == ROOT/'implementation/commander_gym/manual_runtime_profile.py'
                and Path(capture_inventory.__code__.co_filename).resolve() == ROOT/'implementation/commander_gym/manual_runtime_quiescence.py', 'qa_import_layout')
        implementation = ROOT/'implementation/commander_gym'
        require(not implementation.is_symlink(),'qa_implementation_directory')
        actual = set()
        for path in implementation.rglob('*'):
            info = path.lstat();require(not stat.S_ISLNK(info.st_mode),'qa_implementation_symlink')
            if stat.S_ISREG(info.st_mode):actual.add(str(path.relative_to(ROOT)))
            else:require(stat.S_ISDIR(info.st_mode),'qa_implementation_special_file')
        expected = {name for name in self.authority['executionInventory'] if name.startswith('implementation/')}
        require(actual == expected,'qa_unindexed_implementation')
        for name, sha in self.authority['executionInventory'].items():
            require(raw_digest(root_bytes(ROOT/name)) == sha, 'qa_execution_changed')
        for role, unit in self.units.items():
            fields = self._show(unit,('FragmentPath','DropInPaths','NeedDaemonReload'))
            require(fields['FragmentPath'] == str(ROOT/'units'/f'{role}.service')
                    and fields['DropInPaths'] == '' and fields['NeedDaemonReload'] == 'no', 'qa_loaded_unit_mismatch')

    def _show(self,unit,properties):
        require(unit in self.units.values(), 'qa_unit_escape')
        command = ['/usr/bin/systemctl','show',unit]
        for prop in properties:command += ['-p',prop]
        text = subprocess.check_output(command,text=True,timeout=10)
        return dict(line.split('=',1) for line in text.splitlines())

    def _fresh(self):
        require(root_bytes(AUTHORITY) == self.raw, 'qa_authority_changed')
        validate_authority(self.authority,self.production,self.qa,self.ci,now=time.time())
        guest_boundary(); self._inventory(); verify_artifacts(self.qa)

    @contextmanager
    def exclusive_lock(self):
        require(not self.locked,'qa_nested_lock'); self._fresh()
        lock = self.state/'lock'; root_bytes(lock)
        fd = os.open(lock,os.O_RDWR|os.O_NOFOLLOW)
        try:
            require(os.fstat(fd).st_ino == lock.lstat().st_ino, 'qa_lock_changed')
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB); self.locked = True
            yield
        finally:self.locked = False; os.close(fd)

    def _locked(self): require(self.locked,'qa_lock_required')
    def _json(self,name):return decode(root_bytes(self.state/name))
    def current(self):return RuntimeSnapshot(**self._json('selector.json'))
    def assert_no_unfinished_transaction(self):
        self._locked()
        require(not (self.state/'used.json').exists() and not (self.state/'used.json').is_symlink(), 'qa_single_use')
        require(not any(self.state.glob('*.next')), 'qa_pending_write')
        require(not (self.state/'transaction.json').exists(), 'qa_transaction_unfinished')

    def verify_snapshot(self,snapshot):
        self._locked(); self._fresh()
        if self.pending is None:
            require(snapshot == self.current(), 'qa_snapshot_changed')
        else:
            bank = self._json('bank.json');record = self._json('transaction.json')
            require(digest(bank) == record['bankSha256'] and snapshot == self.pending['previous'], 'qa_snapshot_bank_changed')
            require(digest(bank['profile']) == self.authority['previousProfileSha256'], 'qa_previous_profile_changed')
            validate_previous(bank['profile'],self.qa)
        fs = os.statvfs(ROOT/'qa')
        require(fs.f_bavail*fs.f_frsize >= self.authority['qaMinFreeBytes'], 'qa_test_storage_reserve')
        # This actual measurement is Q capacity only; never substitutes P32GiB.

    def rpc(self,operation,**fields):
        require(operation in ('status','drain','resume'), 'qa_rpc_operation')
        path = Path(self.qa['paths']['lifecycleSocket'])
        require(path.is_relative_to(ROOT/'qa'), 'qa_socket_escape')
        info = path.parent.lstat();require(stat.S_ISDIR(info.st_mode) and info.st_uid == self.qa['identities']['nativeUid']
                and stat.S_IMODE(info.st_mode) == 0o700,'qa_socket_directory')
        info = path.lstat(); require(stat.S_ISSOCK(info.st_mode) and info.st_uid == self.qa['identities']['nativeUid']
                and stat.S_IMODE(info.st_mode) == 0o600, 'qa_socket_custody')
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
            client.settimeout(10);client.connect(str(path))
            _,uid,_ = struct.unpack('3i',client.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
            require(uid == self.qa['identities']['nativeUid'],'qa_native_peer')
            client.sendall(canonical(dict(protocol=1,op=operation,**fields))+b'\n')
            data = b''
            while b'\n' not in data:
                chunk = client.recv(65536);require(chunk,'qa_rpc_eof');data += chunk
                require(len(data) <= 1024*1024,'qa_rpc_size')
        result = decode(data.split(b'\n',1)[0])
        require(result.get('protocol') == 1 and result.get('ok') is True,'qa_native_reply')
        self.observe('lifecycle-'+operation,result);return result
    def status(self):return self.rpc('status')
    def drain(self,boot):self.rpc('drain',bootId=boot);return self.status()

    def observe(self,name,value):
        self._locked()
        require(name in {'lifecycle-status','lifecycle-drain','lifecycle-resume','stop-context','stop-proof','transaction','probe'},'qa_observation_kind')
        # Unique append-only files; no caller-supplied production gate booleans.
        folder = self.state/'observations'
        ordinal = len(list(folder.glob('*.json')))
        path = folder/f'{ordinal:06d}.json';require(not path.exists(),'qa_observation_exists')
        write(path,dict(kind=name,nonce=self.authority['nonce'],observedUnix=time.time(),value=value))

    def begin(self,previous,candidate):
        self._locked();self._fresh()
        require(previous.runtime_id == self.authority['previousRuntimeId']
                and candidate.runtime_id == self.authority['qaProfileSha256']
                and candidate.sequence == self.authority['sequence'],'qa_begin_tuple')
        write(self.state/'used.json',dict(authoritySha256=raw_digest(self.raw),nonce=self.authority['nonce']))
        bank = {'selector':self._json('selector.json'),'profile':decode(root_bytes(self.state/'service-profile.json'))}
        require(digest(bank['profile']) == self.authority['previousProfileSha256'] and digest(bank['selector']) == self.authority['previousSelectorSha256'],'qa_initial_bank_identity')
        validate_previous(bank['profile'],self.qa)
        write(self.state/'bank.json',bank)
        self.pending = {'previous':previous,'candidate':candidate,'bank':bank}
        write(self.state/'transaction.json',dict(phase='intent',bankSha256=digest(bank),previous=asdict(previous),candidate=candidate.runtime_id,sequence=candidate.sequence))
        self.observe('transaction',self._json('transaction.json'))

    def _service(self,verb,role):
        require(verb in ('start','stop') and role in self.units,'qa_service_operation')
        self._fresh()
        subprocess.run(['/usr/bin/systemctl',verb,self.units[role]],check=True,timeout=150,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

    def stop_closure(self):
        self._locked();require(self.pending is not None,'qa_transaction_required')
        state = self.status();snapshot = self.current()
        inventory = capture_inventory(Path(self.qa['paths']['recordingRoot']),uid=self.qa['identities']['nativeUid'])
        context = stop_context(snapshot,state,inventory,nonce=self.authority['nonce'],now=time.time())
        self.pending.update(stopContext=context,stopInventory=inventory)
        write(self.state/'stop-context.json',dict(context=context,inventory=inventory))
        self.observe('stop-context',context)
        for role in reversed(ROLES_ORDER):self._service('stop',role)

    def quiescence_barrier(self):
        self._locked();require(self.pending is not None and 'stopContext' in self.pending,'qa_stop_context')
        observations = {}
        for role,unit in self.units.items():
            fields = self._show(unit,('ActiveState','MainPID','ControlPID','ControlGroup')); observations[role] = fields
            if fields.get('ActiveState') not in ('inactive','failed') or fields.get('MainPID') != '0' or fields.get('ControlPID') != '0':return False
            group = fields.get('ControlGroup','')
            if group:
                if not cgroup_empty(group):return False
        actual = capture_inventory(Path(self.qa['paths']['recordingRoot']),uid=self.qa['identities']['nativeUid'])
        if actual != self.pending['stopInventory']:return False
        self.observe('stop-proof',dict(context=self.pending['stopContext'],units=observations,captureInventory=actual));return True

    def select(self,runtime):
        self._locked();require(self.pending is not None,'qa_transaction_required')
        record = self._json('transaction.json'); bank = self._json('bank.json')
        require(digest(bank) == record['bankSha256'],'qa_bank_changed')
        if isinstance(runtime,ValidatedRuntime):
            require(runtime.profile['purpose'] == 'qualification' and runtime.runtime_id == self.authority['qaProfileSha256'],'qa_selection_identity')
            profile = runtime.profile;selector = asdict(RuntimeSnapshot(runtime.runtime_id,runtime.sequence,'manual-luna-v1',profile['engineSha'],profile['gymSha'],profile['recordingSchemaVersion']))
        else:
            require(runtime == self.pending['previous'],'qa_rollback_identity');profile = bank['profile'];selector = bank['selector']
            selector['sequence'] = self.pending['candidate'].sequence
        current_profile = decode(root_bytes(self.state/'service-profile.json'))
        require(digest(current_profile) in (digest(bank['profile']),self.authority['qaProfileSha256']),'qa_foreign_config')
        write(self.state/'service-profile.json',profile);write(self.state/'selector.json',selector)
        write(self.state/'transaction.json',{**record,'phase':'selected','selected':selector['runtime_id']})

    def start_closed(self):
        self._locked();self._fresh()
        write(self.state/'start-policy.json',dict(acceptingNewGames=False,nonce=self.authority['nonce'],providerMode='fake',credentialSource='qa-placeholder-only'))
        profile = decode(root_bytes(self.state/'service-profile.json'))
        require(profile['purpose'] == 'qualification','qa_never_starts_production')
        for role in ROLES_ORDER:self._service('start',role)

    def complete(self,runtime):
        record = self._json('transaction.json');require(record['candidate'] == runtime.runtime_id,'qa_commit_identity')
        write(self.state/'transaction.json',{**record,'phase':'completed','outcome':'qa-promoted'});self.observe('transaction',self._json('transaction.json'))
    def complete_rollback(self,previous,sequence):
        record = self._json('transaction.json');require(record['previous'] == asdict(previous) and record['sequence'] == sequence,'qa_rollback_tuple')
        write(self.state/'transaction.json',{**record,'phase':'completed','outcome':'qa-previous-restored'});self.observe('transaction',self._json('transaction.json'))
    def resume(self,boot,runtime_id):
        result = self.rpc('resume',bootId=boot,releaseId=runtime_id)
        require(result.get('bootId') == boot and result.get('releaseId') == runtime_id
                and result.get('acceptingNewGames') is True,'qa_resume_unconfirmed')
    def held(self,code):
        record = self._json('transaction.json');write(self.state/'transaction.json',{**record,'phase':'operator-review','reason':code})
    def delegate_keyless_update(self):raise ValueError('qa_never_delegates')

    def probe(self,role):
        self._locked();self._fresh();require(role in PROBES,'qa_probe_role')
        path = ROOT/'probes'/role
        # Probes are exact root-sealed fixture programs, not caller commands.
        def bounded_child():
            resource.setrlimit(resource.RLIMIT_FSIZE,(1024*1024,1024*1024))
            resource.setrlimit(resource.RLIMIT_CORE,(0,0))
        with tempfile.TemporaryFile() as stdout,tempfile.TemporaryFile() as stderr:
            completed = subprocess.run([str(path)],stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,timeout=120,check=False,
                env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1'},preexec_fn=bounded_child)
            stdout.seek(0);stderr.seek(0);raw=stdout.read(1024*1024+1);error=stderr.read(1024*1024+1)
        require(len(raw) <= 1024*1024 and len(error) <= 1024*1024,'qa_probe_output_size')
        self.observe('probe',dict(role=role,exitCode=completed.returncode,stdoutSha256=raw_digest(raw),stderrSha256=raw_digest(error),stdoutBase64=base64.b64encode(raw).decode(),stderrBase64=base64.b64encode(error).decode()))
        require(completed.returncode == 0,'qa_probe_failed')
        return decode(raw)


def promote_qa(host):
    require(type(host) is QAFileHost,'qa_dedicated_host_required')
    def validate(previous_id,sequence,now):
        host._fresh()
        require(previous_id == host.authority['previousRuntimeId'] and sequence == host.authority['sequence'],'qa_exact_attempt')
        return ValidatedRuntime(canonical(host.qa),digest(host.qa),sequence)
    # Does not create a fake qualification receipt, approved production tuple or
    # all-true gate map to enter the existing state machine.
    return {**_promote(host,validate,clock=time.time),'kind':'isolated-qa-transaction',
            'activationEligible':False,'productionQualificationIssued':False}


def evidence_manifest(host):
    require(type(host) is QAFileHost,'qa_dedicated_host_required')
    host._fresh()
    inventory = {}
    for path in (host.state/'observations').glob('*.json'):
        inventory[path.name] = raw_digest(root_bytes(path))
    return {'kind':'commander-gym.qa-observations-v1','nonce':host.authority['nonce'],
            'authoritySha256':raw_digest(host.raw),'productionProfileSha256':digest(host.production),
            'qaProfileSha256':digest(host.qa),'observations':inventory,
            'productionQualificationIssued':False,'activationEligible':False,
            'productionGateBooleansTransferred':False,
            'unestablishedProductionGates':sorted(GATES),
            'residuals':['physicalHost32GiBReserve','realTailnetTerminationAndClientAcl',
                         'productionCredentialDelivery','nativeMemoryFitUntilMeasured',
                         'originalProductionRecordingAuthorityPreservation']}


def recover_qa_closed(host):
    """Explicit QA-only recovery; possible admission is held, never rolled back."""
    require(type(host) is QAFileHost,'qa_dedicated_host_required')
    with host.exclusive_lock():
        require(not any(host.state.glob('*.next')), 'qa_pending_write')
        record = host._json('transaction.json');bank = host._json('bank.json')
        require(record['phase'] not in ('completed','operator-review'), 'qa_possible_admission_held')
        require(digest(bank) == record['bankSha256'] and digest(bank['profile']) == host.authority['previousProfileSha256']
                and digest(bank['selector']) == host.authority['previousSelectorSha256'], 'qa_recovery_bank')
        previous = RuntimeSnapshot(**record['previous'])
        require(previous.runtime_id == host.authority['previousRuntimeId'] and record['candidate'] == host.authority['qaProfileSha256']
                and record['sequence'] == host.authority['sequence'], 'qa_recovery_tuple')
        host.pending = {'previous':previous,'candidate':ValidatedRuntime(canonical(host.qa),digest(host.qa),record['sequence']),'bank':bank}
        stopped = all(host._show(unit,('ActiveState','MainPID','ControlPID')).get('ActiveState') in ('inactive','failed')
                      and host._show(unit,('MainPID','ControlPID')) == {'MainPID':'0','ControlPID':'0'} for unit in host.units.values())
        if stopped:
            saved = host._json('stop-context.json')
            current = host.current()
            candidate = host.pending['candidate'];profile = candidate.profile
            expected = RuntimeSnapshot(candidate.runtime_id,candidate.sequence,'manual-luna-v1',profile['engineSha'],profile['gymSha'],profile['recordingSchemaVersion'])
            consumed_previous = RuntimeSnapshot(previous.runtime_id,candidate.sequence,previous.kind,previous.engine_sha,previous.gym_sha,previous.recording_schema)
            context = saved['context']
            require(current in (previous,expected,consumed_previous) and context['runtimeId'] in (previous.runtime_id,candidate.runtime_id)
                    and context['stopNonce'] == host.authority['nonce'] and context['captureInventorySha256'] == digest(saved['inventory']), 'qa_recovery_stop_identity')
            host.pending.update(stopContext=saved['context'],stopInventory=saved['inventory'])
        else:host.stop_closure()
        require(host.quiescence_barrier(),'qa_recovery_stop')
        host.verify_snapshot(previous);host.select(previous);host.start_closed()
        current = host.current();idle_state(host.status(),current,now=time.time(),closed=True)
        write(host.state/'transaction.json',{**record,'phase':'rolled-back-closed','outcome':'qa-explicit-recovery'})
        return {'state':'qa-previous-restored-closed','activationEligible':False}
