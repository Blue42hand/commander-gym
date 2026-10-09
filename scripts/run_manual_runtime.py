"""Fixed root update/promote dispatcher; no installation or credential lookup."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from commander_gym.manual_runtime_host import CONFIG, ProtectedRuntimeHost, load_bundle, root_json
from commander_gym.manual_runtime_rollout import dispatch_update, promote


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('update', 'promote', 'preflight', 'admit', 'source'))
    parser.add_argument('--anchor-runtime')
    parser.add_argument('--candidate-runtime')
    args = parser.parse_args()
    anchor = load_bundle(args.anchor_runtime)[0] if args.anchor_runtime else root_json(CONFIG / 'anchor-profile.json')
    host = ProtectedRuntimeHost(anchor)
    if args.operation == 'update':
        if args.candidate_runtime: parser.error('update does not implicitly promote manual candidates')
        result = dispatch_update(host)
    elif args.operation == 'promote':
        if not args.candidate_runtime: parser.error('promote requires exact installed root candidate')
        profile, receipt, approval = load_bundle(args.candidate_runtime)
        result = promote(host, profile, receipt, approval)
    elif args.operation == 'source':
        if args.candidate_runtime: parser.error('source does not accept candidates')
        with host.exclusive_lock(): result = host.run_source()
    elif args.operation == 'preflight':
        if args.candidate_runtime: parser.error('preflight does not accept candidates')
        result = host.ensure_preflight()
    else:
        if args.candidate_runtime: parser.error('preflight/admit do not accept candidates')
        with host.exclusive_lock():
            result = host.preflight_current() if args.operation == 'preflight' else host.admit_current()
    print(result['state'])


if __name__ == '__main__':
    try: main()
    except Exception:
        print('manual_runtime_held', file=sys.stderr)
        raise SystemExit(1)
