"""Fixed native role entry; recorder/sidecar integration is separately sealed.

Run with the sealed interpreter -I -S -B. Its bootstrap imports ONLY the adjacent
root-installed runtime package. Provider/recorder entry adapters supplied by their
owner are separately qualified and must perform the same service_runtime check.
"""
from pathlib import Path
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from commander_gym.manual_runtime_service import service_runtime
from commander_gym.manual_runtime_launch import native_spec


def main():
    if sys.argv[1:] != ['native']: raise ValueError('fixed native service role')
    directory = Path(os.environ['CREDENTIALS_DIRECTORY'])
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    runtime = service_runtime('native', directory, boot_id=boot)
    spec = native_spec(runtime, credential_directory=directory)
    os.execve(spec.arguments[0], spec.arguments, spec.environment)


if __name__ == '__main__':
    try: main()
    except Exception:
        print('manual_service_held', file=sys.stderr)
        raise SystemExit(1)
