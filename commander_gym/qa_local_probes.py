"""Fixed, no-game local QA probes; execution requires the staged guest authority.

Peer probes retain a labelled synthetic journal fixture in the QA recording tree.
They never fabricate a native terminal or grant a production qualification gate.
"""
import hashlib
import http.client
import json
import multiprocessing
import os
from pathlib import Path
import pwd
import socket
import threading
import time

from .manual_runtime_profile import canonical, digest, require
from .qa_local_provider import LOCAL_TOKEN, LOCAL_GATES, NativeLegalLocalTransport
from .qa_legal_responses import CALLBACK_SCHEMA_HASH


def observation():
    return {'type': 'GameServerSeat', 'agentToAct': 'qa-local-seat', 'perspectivePlayerId': 'qa-local-seat', 'terminated': False, 'callbackKind': 'chooseAction', 'schemaHash': CALLBACK_SCHEMA_HASH,
            'stateDigest': 'a'*64, 'state': {'viewingPlayerId': 'qa-local-seat'}, 'pendingDecision': None,
            'legalActions': [{'actionId': 0, 'semanticId': 'qa-local-pass', 'kind': 'PassPriority',
                'actionType': 'PassPriority', 'action': {'type': 'PassPriority', 'playerId': 'qa-local-seat'},
                'parameterSpec': {'allowedFields': {}}}], 'recentGameLog': []}


def _catalog(port, token):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
    try:
        connection.request('GET', '/v1/controller-profiles', headers={'Authorization': 'Bearer '+token})
        response = connection.getresponse(); raw = response.read(1024*1024+1)
        require(len(raw) <= 1024*1024, 'qa_local_catalog_size')
        return response.status, json.loads(raw)
    finally: connection.close()


def provider_isolation(profile):
    from .manual_runtime_local import local_recording_registry
    from .lmstudio_provider import LMStudioConfig
    from .lmstudio_pilot import LMStudioPilot
    from .manual_runtime_qa_roles import TOKEN
    registry = local_recording_registry(profile)
    status, payload = _catalog(profile['localInference']['port'], LOCAL_TOKEN)
    require(status == 200 and payload == {'profiles': list(registry.profile_payloads())}, 'qa_local_catalog_identity')
    require(_catalog(profile['localInference']['port'], TOKEN)[0] == 401, 'qa_local_cloud_token_admitted')
    require(_catalog(8083, LOCAL_TOKEN)[0] == 401, 'qa_local_token_admitted_by_cloud')
    config = LMStudioConfig(**profile['localInference']['config'])
    transport = NativeLegalLocalTransport(config)
    choice = LMStudioPilot(config, transport=transport).choose(observation())
    require(choice.action_id == 0 and choice.metadata['provider'] == 'lmstudio', 'qa_local_choice')
    response = choice.metadata['modelIo']['attempts'][0]['response']
    require(response['responseId'].startswith('qa-local-fixture-')
            and choice.metadata['usage']['total_tokens'] == 0, 'qa_local_fixture_provenance')
    return {'catalogBindings': registry.binding_fingerprints, 'localCatalogAuthenticated': True,
            'crossProviderTokensRejected': True, 'fixtureRequests': transport.calls,
            'responseId': response['responseId'], 'modelIoProvider': 'lmstudio'}


def _as_role(profile, role, operation):
    """Bounded child of this root probe, using only authenticated staged identities."""
    from .qa_redis_variant import role_uid
    from .manual_runtime_qa_roles import child_groups
    require(os.getuid() == os.geteuid() == 0, 'qa_local_probe_root')
    uid = role_uid(profile, role); account = pwd.getpwuid(uid)
    sidecar_gid = pwd.getpwuid(profile['identities']['sidecarUid']).pw_gid
    groups = child_groups(role, profile, account.pw_gid, sidecar_gid)
    context = multiprocessing.get_context('fork'); receiver, sender = context.Pipe(False)
    def run():
        try:
            os.setgroups(groups); os.setgid(account.pw_gid); os.setuid(uid)
            sender.send({'value': operation()})
        except Exception as error: sender.send({'error': type(error).__name__})
        finally: sender.close()
    child = context.Process(target=run); child.start(); sender.close()
    try:
        require(receiver.poll(10), 'qa_local_probe_child_timeout')
        result = receiver.recv(); child.join(2)
        require(child.exitcode == 0 and set(result) == {'value'}, 'qa_local_probe_child_failed')
        return result['value']
    finally:
        if child.is_alive(): child.kill(); child.join(2)
        receiver.close()


def recorder_peer(profile, nonce):
    """Actual UID/receipt proof in a separately staged disposable namespace."""
    import stat
    from .manual_runtime_local import local_recording_registry, RecordingRegistries
    from .manual_runtime_qa import ROOT
    from .manual_runtime_qa_roles import child_groups
    from .native_game_capture import NativeGameCapture
    from .recorder_bridge import exchange, _expected_event, recording_registry, RecorderBridge, peer_uid
    require(os.getuid() == 0 and len(nonce) == 32 and all(c in '0123456789abcdef' for c in nonce), 'qa_local_probe_nonce')
    base = ROOT/'qa/local-peer-fixture'/nonce
    # Fail before forking or touching the fixture if a staging root would
    # exceed the portable sockaddr_un path budget (including its terminator).
    require(all(len(os.fsencode(base/path)) < 104 for path in
            ('ipc/native.sock','ipc/sidecar.sock','local-ipc/local.sock')), 'qa_local_peer_socket_path')
    native_uid = profile['identities']['nativeUid']; cloud_uid = profile['identities']['sidecarUid']
    local = profile['localInference']; cloud_gid = pwd.getpwuid(cloud_uid).pw_gid
    for parent in (base.parent, *base.parent.parents):
        info=parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and not parent.is_symlink() and info.st_uid==0 and not info.st_mode & 0o022, 'qa_local_peer_fixture_parent')
    for path, uid, gid, mode in [(base,0,0,0o755), (base/'recordings',native_uid,pwd.getpwuid(native_uid).pw_gid,0o700),
            (base/'ipc',native_uid,cloud_gid,0o750),(base/'local-ipc',native_uid,local['gid'],0o750)]:
        info=path.lstat()
        require(stat.S_ISDIR(info.st_mode) and not path.is_symlink() and info.st_uid==uid
                and info.st_gid==gid and stat.S_IMODE(info.st_mode)==mode, 'qa_local_peer_fixture_custody')
    require(base/'recordings' != Path(profile['paths']['recordingRoot']), 'qa_local_peer_fixture_isolation')
    cloud_root=Path(profile['artifacts']['catalog']['root'])
    cloud=recording_registry(cloud_root/profile['catalog']['roster'],cloud_root)
    local_registry=local_recording_registry(profile)
    registry=RecordingRegistries(cloud,local_registry,local['catalog']['bindings'])
    local_binding=sorted(local_registry.binding_fingerprints)[0]
    cloud_binding=sorted(cloud.binding_fingerprints)[0]
    context=multiprocessing.get_context('fork'); controller,worker=context.Pipe()
    def record():
        capture=bridge=None
        try:
            account=pwd.getpwuid(native_uid)
            os.setgroups(child_groups('recorder',profile,account.pw_gid,cloud_gid))
            os.setgid(account.pw_gid);os.setuid(native_uid)
            pins={'engine':profile['engineSha'],'gym':profile['gymSha'],
                  'models':{'provider':'qa-recorder-peer-fixture','model':'none'},'decks':{},'bindings':{},
                  'config':{'qaSyntheticPeerProbe':True},'rng':{'seed':0}}
            capture=NativeGameCapture(base/'recordings',pins)
            bridge=RecorderBridge(capture,registry,native_socket=base/'ipc/native.sock',sidecar_socket=base/'ipc/sidecar.sock',
                native_uid=native_uid,sidecar_uid=cloud_uid,sidecar_gid=cloud_gid,
                local_socket=base/'local-ipc/local.sock',local_uid=local['uid'],local_gid=local['gid'],
                local_binding_ids=local['catalog']['bindings'])
            bridge.start();worker.send({'ready':True})
            require(worker.poll(45) and worker.recv()=='close','qa_local_peer_fixture_timeout')
        except Exception as error:
            try:worker.send({'error':type(error).__name__})
            except (OSError,BrokenPipeError):pass
            raise
        finally:
            if bridge is not None:bridge.close()
            if capture is not None:capture.close()
            worker.close()
    process=context.Process(target=record);process.start();worker.close()
    game='qa-local-peer-'+nonce
    def request(seat,binding):
        actions=observation()['legalActions'];actions[0]['action']['playerId']=seat
        return {'playerId':seat,'profileId':binding,'gameSessionId':game,'manualHumanGame':True,
                'state':{'viewingPlayerId':seat},'legalActions':actions,'pendingDecision':None,'recentGameLog':[]}
    local_request=request('qa-local-seat',local_binding);cloud_request=request('qa-cloud-seat',cloud_binding)
    def identity(req):
        return dict(protocol=1,callbackId=req['playerId'],gameId=game,seatId=req['playerId'],bindingId=req['profileId'],callback='chooseAction')
    def register():
        directory=base/'recordings'/game;directory.mkdir(mode=0o700)
        payload={'setup':{'seed':0,'players':[{'playerId':seat,'deck':['Forest']} for seat in ('qa-local-seat','qa-cloud-seat')]},
                 'pinnedCards':['Forest'],'qaSyntheticPeerProbe':True}
        body=dict(schemaVersion=1,gameId=game,sequence=1,previousSha256='0'*64,engineRevision=profile['engineSha'],
                  clockEpoch='qa-local-peer',kind='initialization',visibility='admin',payload=payload)
        exact=json.dumps(body,separators=(',',':'))
        raw=canonical({'body':exact,'sha256':hashlib.sha256(exact.encode()).hexdigest()})+b'\n'
        fd=os.open(directory/'native-000000.ndjson',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
        return [exchange(base/'ipc/native.sock',dict(identity(req),op='register',request=req),recorder_uid=native_uid)
                for req in (local_request,cloud_request)]
    def events(req):
        event=_expected_event(registry,req,'chooseAction');ident=identity(req)
        return (dict(ident,op='started',event=event),dict(ident,op='finished',event={**event,
            'choice':{'channel':'action','actionId':0,'metadata':{'qaExecution':'synthetic-recorder-peer-probe','cost_usd':0}}}))
    def receipts(path,req):
        started,finished=events(req)
        exchange(path,started,recorder_uid=native_uid)
        receipt=exchange(path,finished,recorder_uid=native_uid)
        require(receipt==exchange(path,finished,recorder_uid=native_uid),'qa_local_duplicate_changed')
        return receipt
    def rejection(path,message,*,dac=False):
        # A dead/missing listener is never counted as an authorization denial.
        try:
            with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
                client.settimeout(3);client.connect(str(path));require(peer_uid(client)==native_uid,'qa_local_fixture_peer')
                client.sendall(canonical(message)+b'\n');raw=b''
                while b'\n' not in raw:
                    data=client.recv(4096);require(data and len(raw)+len(data)<=4096,'qa_local_fixture_reply');raw+=data
            require(json.loads(raw)=={'protocol':1,'ok':False,'error':'recorder_event_rejected'},'qa_local_fixture_scope')
            return 'authenticated-rejection'
        except PermissionError:
            require(dac,'qa_local_fixture_unexpected_dac');return 'filesystem-denial'
    try:
        require(controller.poll(5) and controller.recv()=={'ready':True},'qa_local_peer_fixture_start')
        _as_role(profile,'native',register)
        local_receipt=_as_role(profile,'local',lambda:receipts(base/'local-ipc/local.sock',local_request))
        cloud_receipt=_as_role(profile,'sidecar',lambda:receipts(base/'ipc/sidecar.sock',cloud_request))
        local_finished=events(local_request)[1];cloud_finished=events(cloud_request)[1]
        cloud_scope=_as_role(profile,'sidecar',lambda:rejection(base/'ipc/sidecar.sock',local_finished))
        local_scope=_as_role(profile,'local',lambda:rejection(base/'local-ipc/local.sock',cloud_finished))
        cross_local=_as_role(profile,'local',lambda:rejection(base/'ipc/sidecar.sock',local_finished,dac=True))
        cross_cloud=_as_role(profile,'sidecar',lambda:rejection(base/'local-ipc/local.sock',local_finished,dac=True))
        _as_role(profile,'native',lambda:[exchange(base/'ipc/native.sock',dict(identity(req),op='closed'),recorder_uid=native_uid)
                                        for req in (local_request,cloud_request)])
    finally:
        if process.is_alive():
            try:controller.send('close')
            except (OSError,BrokenPipeError):pass
        process.join(8)
        if process.is_alive():process.kill();process.join(2)
        controller.close()
    require(process.exitcode==0,'qa_local_peer_fixture_cleanup')
    return {'fixtureRoot':str(base),'gameId':game,'scope':'disposable-recorder-peer-fixture',
            'activeRecordingTreeTouched':False,'syntheticFixtureOnly':True,'nativeTerminalFabricated':False,
            'localDurableReceiptSha256':local_receipt['receiptSha256'],'cloudDurableReceiptSha256':cloud_receipt['receiptSha256'],
            'duplicateReceiptsIdentical':True,'cloudBindingScope':cloud_scope,'localBindingScope':local_scope,
            'localToCloudSocket':cross_local,'cloudToLocalSocket':cross_cloud,'fixtureWorkerReaped':True}


def failure_cancellation(profile):
    from .lmstudio_provider import LMStudioConfig, LMStudioError, require_loaded_model
    from .lmstudio_pilot import LMStudioPilot, LMStudioPilotError
    config = LMStudioConfig(**profile['localInference']['config']); codes = []
    class FailureTransport(NativeLegalLocalTransport):
        def __init__(self, code): super().__init__(config); self.code = code
        def request(self, path, body, **options): raise LMStudioError(self.code)
    for code in ('qa_local_unavailable', 'deadline_exceeded'):
        transport = FailureTransport(code)
        try: LMStudioPilot(config, transport=transport).choose(observation())
        except LMStudioPilotError as error: require(str(error) == code, 'qa_local_failure_changed')
        else: require(False, 'qa_local_failure_selected')
        codes.append(code)
    invalid = NativeLegalLocalTransport(config).request('/api/v1/models', None, timeout=1)
    invalid['models'][0]['loaded_instances'][0]['config']['context_length'] = config.context_window-1
    try: require_loaded_model(invalid, config)
    except LMStudioError as error: require(str(error) == 'loaded_context_below_configured_budget', 'qa_local_context_error')
    else: require(False, 'qa_local_bad_context_ready')
    # A real accepted HTTP callback is blocked in a fake transport, then reaped
    # by the exact LocalServer shutdown/cancellation path. No native game exists.
    from .game_server_binding_lmstudio_sidecar import LocalBindingConfig, build_binding_lmstudio_sidecar
    from .game_server_sidecar import GameServerSidecarConfig
    entered = threading.Event(); cancelled = threading.Event()
    class BlockingTransport(NativeLegalLocalTransport):
        def request(self, path, body, *, timeout, cancel=None):
            if path == '/api/v1/models': return super().request(path, body, timeout=timeout, cancel=cancel)
            entered.set(); deadline = time.monotonic()+min(timeout, 3)
            while time.monotonic() < deadline:
                if cancel is not None and cancel.is_set():
                    cancelled.set(); raise LMStudioError('cancelled')
                time.sleep(.01)
            raise LMStudioError('deadline_exceeded')
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0)); port = reservation.getsockname()[1]
    catalog = Path(profile['artifacts']['localCatalog']['root'])
    server = build_binding_lmstudio_sidecar(LocalBindingConfig(GameServerSidecarConfig(LOCAL_TOKEN, port=port, require_manual_human_game=True),
        config, catalog/profile['localInference']['catalog']['roster'], catalog), transport=BlockingTransport(config))
    server.handle_error = lambda *_: None
    serving = threading.Thread(target=server.serve_forever); serving.start()
    binding = sorted(profile['localInference']['catalog']['bindings'])[0]
    request = {'playerId': 'qa-local-seat', 'profileId': binding, 'gameSessionId': 'qa-local-cancel',
               'manualHumanGame': True, 'state': observation()['state'], 'pendingDecision': None,
               'recentGameLog': [], 'legalActions': observation()['legalActions']}
    delivered = []
    def callback():
        client = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
        try:
            client.request('POST', '/v1/choose-action', canonical(request),
                {'Authorization': 'Bearer '+LOCAL_TOKEN, 'Content-Type': 'application/json'})
            response = client.getresponse(); delivered.append(response.status == 200)
        except OSError: pass
        finally: client.close()
    calling = threading.Thread(target=callback); calling.start(); start = time.monotonic()
    try:
        require(entered.wait(2), 'qa_local_callback_not_entered')
    finally:
        server.shutdown(); server.server_close(); calling.join(6); serving.join(2)
    elapsed = time.monotonic()-start
    require(cancelled.is_set() and not calling.is_alive() and not serving.is_alive()
            and not any(t.is_alive() for t in server._threads) and True not in delivered
            and elapsed < 4, 'qa_local_cancellation_not_reaped')
    return {'failuresSelectedNoAction': codes, 'badContextWithheldReadiness': True,
            'acceptedCallbackCancelled': True, 'callbackThreadsReaped': True, 'elapsedSeconds': elapsed}


def run_probe(name):
    from .manual_runtime_qa import QAFileHost
    from .manual_runtime_qa_roles import selected_runtime
    require(name in LOCAL_GATES, 'qa_local_probe_name')
    host = QAFileHost(); host._fresh()
    profile = host._json('service-profile.json')
    runtime = selected_runtime(host.authority, host.qa, profile, host._json('selector.json'),
                               host._json('start-policy.json'), now=time.time())
    require('localInference' in profile and os.getuid() == 0, 'qa_local_probe_profile')
    functions = {'local-provider-isolation': lambda: provider_isolation(profile),
                 'local-recorder-peer': lambda: recorder_peer(profile, host.authority['nonce']),
                 'local-failure-cancellation': lambda: failure_cancellation(profile)}
    value = functions[name]()
    host._fresh()
    current = selected_runtime(host.authority, host.qa, host._json('service-profile.json'), host._json('selector.json'),
                               host._json('start-policy.json'), now=time.time())
    require(current.runtime_id == runtime.runtime_id and current.sequence == runtime.sequence, 'qa_local_probe_runtime_changed')
    return {'kind': 'isolated-local-qa-observation', 'gate': LOCAL_GATES[name],
            'nonce': host.authority['nonce'], 'runtimeId': runtime.runtime_id,
            'observations': value, 'externalProviderCalls': 0,
            'productionQualificationIssued': False, 'activationEligible': False}


if __name__ == '__main__':
    import sys
    require(len(sys.argv) == 2, 'qa_local_probe_arguments')
    print(json.dumps(run_probe(sys.argv[1]), sort_keys=True))
