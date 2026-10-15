#!/usr/bin/env python3
"""Install or operate one reviewed, matched native/Gym release."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from commander_gym.manual_runtime_host import CONFIG, ProtectedRuntimeHost, load_bundle, root_json
from commander_gym.manual_runtime_profile import ManualRuntimeError, require
from commander_gym.runtime_release import diagnostics, inspect_bundle, pack_bundle, rollback, stage_bundle, wait_and_promote


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='operation', required=True)
    for operation in ('install', 'update', 'verify'):
        command = sub.add_parser(operation)
        command.add_argument('--bundle', type=Path, required=True)
        command.add_argument('--sha256', required=True, help='Reviewed release.json digest, never a moving branch')
        if operation == 'update': command.add_argument('--wait-seconds', type=int, default=3600)
    for operation in ('status', 'diagnostics', 'rollback', 'recover'):
        sub.add_parser(operation)
    pack = sub.add_parser('pack')
    pack.add_argument('--runtime', required=True); pack.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.operation == 'pack':
        profile, receipt, approval = load_bundle(args.runtime)
        result = {'manifestSha256': pack_bundle(profile, receipt, approval, args.output)}
    elif args.operation in ('verify', 'install', 'update'):
        manifest = inspect_bundle(args.bundle, args.sha256)
        if args.operation == 'verify': result = {'state': 'verified', 'engineSha': manifest['profile']['engineSha'], 'gymSha': manifest['profile']['gymSha']}
        elif args.operation == 'install':
            from commander_gym.runtime_install import install
            result = install(args.bundle, manifest)
        else:
            host = ProtectedRuntimeHost(root_json(CONFIG / 'anchor-profile.json'))
            # Staging is serialized and does not touch the selected runtime.
            with host.exclusive_lock():
                host.assert_no_unfinished_transaction()
                current = host.current()
                require(manifest['approval']['previousRuntimeId'] == current.runtime_id
                        and manifest['approval']['sequence'] == current.sequence + 1, 'update_approval_changed')
                require(manifest['profile']['recordingSchemaVersion'] == current.recording_schema, 'unsupported_storage_migration')
                stage_bundle(args.bundle, manifest)
            result = wait_and_promote(host, manifest['profile'], manifest['qualification'], manifest['approval'], wait_seconds=args.wait_seconds)
    else:
        host = ProtectedRuntimeHost(root_json(CONFIG / 'anchor-profile.json'))
        if args.operation in ('status', 'diagnostics'): result = diagnostics(host)
        elif args.operation == 'recover':
            with host.exclusive_lock(): result = host.admit_current()
        else: result = rollback(host, load_bundle=load_bundle)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try: main()
    except ManualRuntimeError as error:
        print(json.dumps({'state': 'held', 'reason': str(error)}), file=sys.stderr); raise SystemExit(1)
    except Exception:
        print('{"state":"held","reason":"operation_failed"}', file=sys.stderr); raise SystemExit(1)
