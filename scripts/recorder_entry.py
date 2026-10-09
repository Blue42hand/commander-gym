"""Sealed native-UID recorder entry; no provider/token credential is accepted."""
from pathlib import Path
import grp
import os
import signal
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from commander_gym.manual_runtime_service import service_runtime, sealed_python_paths, verify_running_python
from commander_gym.manual_runtime_profile import require


def main():
    if sys.argv[1:]: raise ValueError('fixed recorder entry')
    runtime = service_runtime('recorder', Path(os.environ['CREDENTIALS_DIRECTORY']),
                              boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
    verify_running_python(runtime, sys.executable, sys.base_prefix)
    gid = grp.getgrnam('commander-prod').gr_gid
    require(os.getgid() == gid, 'recorder_catalog_group')
    paths = sealed_python_paths(runtime)
    sys.path.extend(paths)
    import commander_gym
    commander_gym.__path__.append(str(Path(paths[0]) / 'commander_gym'))
    # Only native journal/lifecycle/IPC access; no provider environment inherited.
    os.environ.clear(); os.environ.update({'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
    from commander_gym.manual_runtime_recorder import run_recorder
    stop = [False]
    def stopping(signum, frame): stop[0] = True
    signal.signal(signal.SIGTERM, stopping); signal.signal(signal.SIGINT, stopping)
    run_recorder(runtime, sidecar_gid=gid, stopping=lambda: stop[0])
    return 0


if __name__ == '__main__':
    try: raise SystemExit(main())
    except Exception:
        print('manual_recorder_held', file=sys.stderr)
        raise SystemExit(1)
