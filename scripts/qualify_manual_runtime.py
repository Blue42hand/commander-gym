"""Inspect a sealed manual profile without keys, provider calls or deployment."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from commander_gym.manual_runtime_profile import decode
from commander_gym.manual_runtime_qualification import inspect_runtime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = inspect_runtime(decode(args.profile.read_bytes()))
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'profileSha256': result['profileSha256'], 'activationEligible': False, 'externalProviderCalls': 0}))


if __name__ == '__main__':
    main()
