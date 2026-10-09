"""Sealed private sidecar entry; systemd credentials load only after root preflight."""
from pathlib import Path
import os
import signal
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from commander_gym.manual_runtime_service import service_runtime, sealed_python_paths, verify_running_python, verify_dependency_runtime
from commander_gym.manual_runtime_launch import sidecar_environment


def main():
    if sys.argv[1:]: raise ValueError('fixed sidecar entry')
    directory = Path(os.environ['CREDENTIALS_DIRECTORY'])
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    runtime = service_runtime('sidecar', directory, boot_id=boot)
    verify_running_python(runtime, sys.executable, sys.base_prefix)
    paths = sealed_python_paths(runtime)
    sys.path.extend(paths)
    import commander_gym
    commander_gym.__path__.append(str(Path(paths[0]) / 'commander_gym'))
    verify_dependency_runtime(runtime)
    # All executable/catalog/dependency checks happen before either private read.
    environment = sidecar_environment(runtime, credential_directory=directory, uid=os.getuid())
    os.environ.clear(); os.environ.update(environment)
    from commander_gym.game_server_binding_openai_sidecar import main as binding_main
    def interrupted(signum, frame): raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    return binding_main()  # Merged main injects RemoteCaptureSink; no scanner/reporter.


if __name__ == '__main__':
    try: raise SystemExit(main())
    except Exception:
        print('manual_sidecar_held', file=sys.stderr)
        raise SystemExit(1)
