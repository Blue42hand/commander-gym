#!/bin/bash
set -euo pipefail

if [[ $# -ne 5 ]]; then
  echo "usage: $0 <commander-gym-root> <commander-gym-revision> <argentum-root> <argentum-revision> <work-root>" >&2
  exit 64
fi

commander_root="$1"
commander_revision="$2"
argentum_root="$3"
argentum_revision="$4"
work_root="$5"

mkdir -p "$work_root"
work_root="$(cd "$work_root" && pwd -P)"
commander_root="$(cd "$commander_root" && pwd -P)"
argentum_root="$(cd "$argentum_root" && pwd -P)"

config_path="$work_root/node-package-input.json"
rendered_root="$work_root/rendered"
storage_root="$work_root/storage"
token_file="$work_root/gateway-token"
argentum_log="$work_root/argentum.log"
gateway_log="$work_root/gateway.log"
qualification_json="$work_root/qualification.json"
gateway_port="18082"

printf '%s\n' 'portable-node-qualification-token' > "$token_file"
chmod 600 "$token_file"

export COMMANDER_ROOT="$commander_root"
export COMMANDER_REVISION="$commander_revision"
export ARGENTUM_ROOT="$argentum_root"
export ARGENTUM_REVISION="$argentum_revision"
export STORAGE_ROOT="$storage_root"
export TOKEN_FILE="$token_file"
export GATEWAY_PORT="$gateway_port"
export CONFIG_PATH="$config_path"

python3 - <<'PY'
import json
import os
from pathlib import Path

payload = {
    "version": 1,
    "service_user": os.environ.get("USER", "runner"),
    "service_group": os.environ.get("USER", "runner"),
    "commander_gym": {
        "root": os.environ["COMMANDER_ROOT"],
        "revision": os.environ["COMMANDER_REVISION"],
    },
    "argentum": {
        "root": os.environ["ARGENTUM_ROOT"],
        "revision": os.environ["ARGENTUM_REVISION"],
    },
    "gateway_token_file": os.environ["TOKEN_FILE"],
    "gateway": {
        "bind": "127.0.0.1",
        "port": int(os.environ["GATEWAY_PORT"]),
        "upstream": "http://127.0.0.1:8081",
    },
    "storage": {
        "root": os.environ["STORAGE_ROOT"],
    },
}
Path(os.environ["CONFIG_PATH"]).write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY

export PYTHONPATH="$commander_root${PYTHONPATH:+:$PYTHONPATH}"
python3 -m commander_gym.node_package \
  --config "$config_path" \
  --output "$rendered_root"

python3 -m commander_gym.node_preflight \
  --config "$config_path" \
  --initialize-storage \
  --min-free-bytes 1048576 \
  --no-systemd

argentum_pid=""
gateway_pid=""
cleanup() {
  if [[ -n "$gateway_pid" ]] && kill -0 "$gateway_pid" 2>/dev/null; then
    kill "$gateway_pid" 2>/dev/null || true
  fi
  if [[ -n "$argentum_pid" ]] && kill -0 "$argentum_pid" 2>/dev/null; then
    kill "$argentum_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT

"$rendered_root/run-argentum-gym.sh" >"$argentum_log" 2>&1 &
argentum_pid=$!

wait_for_url() {
  local url="$1"
  local bearer_token="${2:-}"
  local pid="$3"
  local label="$4"
  local log_path="$5"
  local attempts="${6:-180}"
  local delay="${7:-2}"

  for ((attempt=1; attempt<=attempts; attempt++)); do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "$label exited before becoming ready" >&2
      tail -n 200 "$log_path" >&2 || true
      return 1
    fi

    if python3 - "$url" "$bearer_token" <<'PY'
import sys
from urllib.request import Request, urlopen

url = sys.argv[1]
token = sys.argv[2]
headers = {"Accept": "application/json"}
if token:
    headers["Authorization"] = f"Bearer {token}"
request = Request(url, headers=headers, method="GET")
try:
    with urlopen(request, timeout=2) as response:
        if 200 <= response.status < 300:
            raise SystemExit(0)
except Exception:
    pass
raise SystemExit(1)
PY
    then
      return 0
    fi
    sleep "$delay"
  done

  echo "$label did not become ready" >&2
  tail -n 200 "$log_path" >&2 || true
  return 1
}

wait_for_url \
  "http://127.0.0.1:8081/health" \
  "" \
  "$argentum_pid" \
  "Argentum Gym" \
  "$argentum_log"

"$rendered_root/run-commander-gym-gateway.sh" >"$gateway_log" 2>&1 &
gateway_pid=$!

qualification_token="$(tr -d '\r\n' < "$token_file")"
wait_for_url \
  "http://127.0.0.1:${gateway_port}/health" \
  "$qualification_token" \
  "$gateway_pid" \
  "Commander Gym gateway" \
  "$gateway_log" \
  60 \
  1

export COMMANDER_GYM_ARGENTUM_TOKEN="$qualification_token"
python3 -m commander_gym.service_doctor \
  --argentum-url "http://127.0.0.1:${gateway_port}" \
  --expected-build-revision "$argentum_revision" \
  > "$qualification_json"

cat "$qualification_json"
echo "Portable node qualification artifact: $qualification_json"
