"""Sealed local service entry. Accept only its separate native authentication token."""
from pathlib import Path
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from commander_gym.manual_runtime_service import service_runtime, sealed_python_paths, verify_running_python
from commander_gym.manual_runtime_local import local_environment, local_arguments


def main():
    if sys.argv[1:]: raise ValueError('fixed local service entry')
    directory = Path(os.environ['CREDENTIALS_DIRECTORY'])
    runtime = service_runtime('local', directory,
        boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
    verify_running_python(runtime, sys.executable, sys.base_prefix)
    paths = sealed_python_paths(runtime)
    sys.path.extend(paths)
    import commander_gym
    commander_gym.__path__.append(str(Path(paths[0]) / 'commander_gym'))
    # Read-only discovery: fail startup while admission is still closed if the
    # exact configured model/context is unavailable. Never load or select another.
    from commander_gym.lmstudio_provider import LMStudioConfig, LMStudioTransport, require_loaded_model
    config = LMStudioConfig(**runtime.profile['localInference']['config'])
    require_loaded_model(LMStudioTransport(config).request('/api/v1/models', None,
                         timeout=min(10, config.timeout_seconds)), config)
    environment = local_environment(runtime, credential_directory=directory, uid=os.getuid())
    notify_socket = os.environ.get('NOTIFY_SOCKET')
    os.environ.clear(); os.environ.update(environment)
    sys.argv = [sys.argv[0], *local_arguments(runtime.profile)]
    from commander_gym.game_server_binding_lmstudio_sidecar import main as local_main
    def ready():
        import socket
        if not notify_socket: raise ValueError('local readiness socket required')
        address = '\0' + notify_socket[1:] if notify_socket.startswith('@') else notify_socket
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as connection:
            connection.connect(address)
            connection.sendall(b'READY=1')
    return local_main(ready=ready)


if __name__ == '__main__':
    try: raise SystemExit(main())
    except Exception:
        print('manual_local_sidecar_held', file=sys.stderr)
        raise SystemExit(1)
