#!/bin/bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "usage: $0 <repo-root> <expected-revision> <token-file>" >&2
  exit 64
fi

repo_root="$1"
expected_revision="$2"
token_file="$3"

cd "$repo_root"

actual_revision="$(git rev-parse HEAD)"
if [[ "$actual_revision" != "$expected_revision" ]]; then
  echo "Commander Gym checkout moved: expected $expected_revision, found $actual_revision. Reinstall the gateway before restarting it." >&2
  exit 78
fi

if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "Commander Gym tracked files are dirty; refusing ambiguous gateway provenance." >&2
  exit 78
fi

if [[ ! -f "$token_file" ]]; then
  echo "Gateway token file missing: $token_file" >&2
  exit 78
fi

token="$(tr -d '\r\n' < "$token_file")"
if [[ -z "$token" ]]; then
  echo "Gateway token file is blank: $token_file" >&2
  exit 78
fi

export COMMANDER_GYM_GATEWAY_TOKEN="$token"
export COMMANDER_GYM_GATEWAY_BIND="127.0.0.1"
export COMMANDER_GYM_GATEWAY_PORT="8082"
export COMMANDER_GYM_GATEWAY_UPSTREAM="http://127.0.0.1:8081"

exec /usr/bin/env python3 -m commander_gym.argentum_gateway
