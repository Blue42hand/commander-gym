"""Fixed batch role entry points. No role can control the shared human runtime."""
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time

from .batch_budget import ChoiceGuard, TokenFence


def main():
    os.umask(0o077)
    config = json.loads(Path('/config/role.json').read_text())
    role = sys.argv[1]
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT): signal.signal(sig, lambda *_: stop.set())
    if role == 'native':
        env = {'PATH':'/usr/bin:/bin','LANG':'C.UTF-8', 'SERVER_ADDRESS':'127.0.0.1','SERVER_PORT':str(config['native_port']),
               'APP_VERSION':config['pins']['engine'], 'NATIVE_RELEASE_ID':config['runtime_id'], 'NATIVE_GYM_SHA':config['pins']['gym'],
               'NATIVE_LIFECYCLE_ENABLED':'true','NATIVE_LIFECYCLE_SOCKET':'/ipc/native/lifecycle.sock','NATIVE_UPDATER_USER':'root',
               'NATIVE_RECORDING_SCHEMA_VERSION':'1', 'GAME_AI_MODE':'engine','GAME_AI_ENABLED':'true','GAME_DEBUG_MODE':'false',
               'GAME_DEV_ENDPOINTS_ENABLED':'true','GAME_AI_THINKING_DELAY_MS':'0', 'GAME_AI_INSIGHT_ENABLED':'false','GAME_EASTER_EGGS_ENABLED':'false',
               'GAME_TOURNAMENT_SIMULATE_AI_MATCHES':'false','ACCOUNTS_ENABLED':'false','CACHE_REDIS_ENABLED':'false',
               'COMMANDER_GYM_ENABLED':'true','COMMANDER_GYM_MANUAL_ONLY':'false','COMMANDER_GYM_RECORDING_MANAGED_BATCH':'true',
               'COMMANDER_GYM_SIDECAR_URL':'http://127.0.0.1:'+str(config['provider_port']), 'COMMANDER_GYM_SIDECAR_TIMEOUT_MS':'120000'}
        args = [config['java'],'-Xms256m','-Xmx2g','-Dcommander-gym.recorder.socket=/ipc/native/native.sock',
                '-Dgame.recording.root=/captures','-Dgame.recording.engine-revision='+config['pins']['engine'],
                '-Dlogging.level.root=WARN','-jar','/server/server.jar','--spring.config.import=configtree:/native-config/']
        os.execve(args[0], args, env)
    elif role == 'recorder':
        from .native_game_capture import NativeGameCapture
        from .recording_health import RecordingHealthReporter
        from .recorder_bridge import RecorderBridge, recording_registry
        capture = NativeGameCapture(Path('/captures'), config['recording_pins'], background_finalization=True)
        registry = recording_registry(Path('/catalog')/config['roster'], Path('/catalog'))
        bridge = RecorderBridge(capture, registry, native_socket=Path('/ipc/native/native.sock'),
                                sidecar_socket=Path('/ipc/provider/sidecar.sock'), native_uid=config['engine_uid'],
                                sidecar_uid=config['provider_uid'], sidecar_gid=config['provider_gid'], managed_batch=True)
        reporter = RecordingHealthReporter(capture, Path('/ipc/native/lifecycle.sock'))
        try:
            bridge.start(); reporter.start()
            while not stop.wait(.2): pass
        finally:
            for owner in (reporter,bridge,capture): owner.close()
    elif role == 'provider':
        from .game_server_binding_openai_sidecar import binding_openai_game_server_config_from_environment, build_binding_openai_game_server_sidecar
        from .recorder_bridge import RemoteCaptureSink
        plan=config['plan']
        fixture=plan['execution_mode']=='fixture'
        if fixture:
            key='fixture-no-provider-credential'
            from .batch_fixture import BatchFixtureClient
            client=BatchFixtureClient(plan['max_requests'],plan['wall_seconds']+120)
        else:
            from .manual_runtime_launch import parse_existing_key
            from .bounded_provider_process import BoundedProcessClient
            key=parse_existing_key((Path(os.environ['CREDENTIALS_DIRECTORY'])/'provider.env').read_text())
            client=BoundedProcessClient(key)
        client=TokenFence(client, Path('/budget/tokens.json'), plan['max_tokens'])
        env={'OPENAI_API_KEY':key,'COMMANDER_GYM_SIDECAR_TOKEN':Path('/provider-config/token').read_text(),
             'COMMANDER_GYM_SIDECAR_HOST':'127.0.0.1','COMMANDER_GYM_SIDECAR_PORT':str(config['provider_port']),
             'COMMANDER_GYM_OPENAI_MODEL':'gpt-6-luna','COMMANDER_GYM_OPENAI_TIMEOUT':'90','COMMANDER_GYM_OPENAI_MAX_ATTEMPTS':'2',
             'COMMANDER_GYM_INSTANCE_ROOT':'/catalog','COMMANDER_GYM_BINDING_CATALOG':'/catalog/'+config['roster'],
             'COMMANDER_GYM_CACHE_FRIENDLY_HISTORY':'true', 'COMMANDER_GYM_OPENAI_BUDGET_LEDGER':'/budget/openai.json',
             'COMMANDER_GYM_OPENAI_BUDGET_CAP_USD':str(plan['budget_usd']),
             'COMMANDER_GYM_OPENAI_BUDGET_AUTHORIZED_MAX_USD':str(plan['budget_usd']),
             'COMMANDER_GYM_OPENAI_BUDGET_MAX_REQUESTS':str(plan['max_requests'])}
        cfg=binding_openai_game_server_config_from_environment(env)
        # Deadline enforced before every provider dispatch as well as by supervisor.
        cfg.budget.dispatch_deadline_unix=config['deadline']
        remote=RemoteCaptureSink(Path('/ipc/provider/sidecar.sock'), recorder_uid=config['engine_uid'])
        server=build_binding_openai_game_server_sidecar(cfg, client=client, game_capture=remote)
        from dataclasses import replace
        server.sidecar_config=replace(server.sidecar_config, require_managed_batch_game=True)
        guard=ChoiceGuard(plan['max_decisions'],config['deadline'],Path('/progress/provider.json'))
        factory=server._profile_seat_factory
        def guarded(*args):
            seat=factory(*args); seat._pilot=guard.wrap(seat._pilot); return seat
        server._profile_seat_factory=guarded
        server._game_profile_seat_factory=lambda game_id, player_id, profile_id: guarded(player_id, profile_id, game_id)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            while not stop.wait(.2): pass
        finally: server.shutdown();server.server_close()
    else: raise ValueError('Unknown fixed role')

if __name__=='__main__': main()
