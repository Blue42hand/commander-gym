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

if [[ ! -d /run/systemd/system ]]; then
  echo "systemd is not the active system manager on this host" >&2
  exit 69
fi

config_path="$work_root/node-package-input.json"
rendered_root="$work_root/rendered"
storage_root="$work_root/storage"
token_file="$work_root/gateway-token"
qualification_json="$work_root/qualification.json"
status_path="$work_root/systemd-status.txt"
argentum_log="$work_root/argentum-journal.log"
gateway_log="$work_root/gateway-journal.log"
gateway_port="18082"
service_user="$(id -un)"
service_group="$(id -gn)"
unit_dir="/etc/systemd/system"
argentum_unit="argentum-gym.service"
gateway_unit="commander-gym-gateway.service"
argentum_unit_path="$unit_dir/$argentum_unit"
gateway_unit_path="$unit_dir/$gateway_unit"

if [[ -e "$argentum_unit_path" || -e "$gateway_unit_path" ]]; then
  echo "refusing to overwrite existing Commander Gym qualification units" >&2
  exit 73
fi

printf '%s\n' 'portable-node-systemd-qualification-token' > "$token_file"
chmod 600 "$token_file"

export COMMANDER_ROOT="$commander_root"
export COMMANDER_REVISION="$commander_revision"
export ARGENTUM_ROOT="$argentum_root"
export ARGENTUM_REVISION="$argentum_revision"
export STORAGE_ROOT="$storage_root"
export TOKEN_FILE="$token_file"
export GATEWAY_PORT="$gateway_port"
export CONFIG_PATH="$config_path"
export SERVICE_USER="$service_user"
export SERVICE_GROUP="$service_group"

python3 - <<'PY'
import json
import os
from pathlib import Path

payload = {
    "version": 1,
    "service_user": os.environ["SERVICE_USER"],
    "service_group": os.environ["SERVICE_GROUP"],
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
  --min-free-bytes 1048576

collect_evidence() {
  sudo systemctl show \
    "$argentum_unit" "$gateway_unit" \
    --property=Id,LoadState,ActiveState,SubState,ExecMainCode,ExecMainStatus \
    > "$status_path" 2>&1 || true
  sudo journalctl --no-pager -u "$argentum_unit" > "$argentum_log" 2>&1 || true
  sudo journalctl --no-pager -u "$gateway_unit" > "$gateway_log" 2>&1 || true
}

cleanup() {
  set +e
  collect_evidence
  sudo systemctl stop "$gateway_unit" "$argentum_unit" >/dev/null 2>&1 || true
  sudo rm -f "$gateway_unit_path" "$argentum_unit_path"
  sudo systemctl daemon-reload >/dev/null 2>&1 || true
}
trap cleanup EXIT

sudo install -m 0644 "$rendered_root/systemd/$argentum_unit" "$argentum_unit_path"
sudo install -m 0644 "$rendered_root/systemd/$gateway_unit" "$gateway_unit_path"
sudo systemctl daemon-reload

wait_for_service_url() {
  local unit="$1"
  local url="$2"
  local bearer_token="${3:-}"
  local label="$4"
  local attempts="${5:-180}"
  local delay="${6:-2}"

  for ((attempt=1; attempt<=attempts; attempt++)); do
    if ! sudo systemctl is-active --quiet "$unit"; then
      echo "$label systemd unit is not active" >&2
      sudo systemctl status --no-pager "$unit" >&2 || true
      sudo journalctl --no-pager -n 200 -u "$unit" >&2 || true
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
  sudo systemctl status --no-pager "$unit" >&2 || true
  sudo journalctl --no-pager -n 200 -u "$unit" >&2 || true
  return 1
}

sudo systemctl start "$argentum_unit"
wait_for_service_url \
  "$argentum_unit" \
  "http://127.0.0.1:8081/health" \
  "" \
  "Argentum Gym"

sudo systemctl start "$gateway_unit"
qualification_token="$(tr -d '\r\n' < "$token_file")"
wait_for_service_url \
  "$gateway_unit" \
  "http://127.0.0.1:${gateway_port}/health" \
  "$qualification_token" \
  "Commander Gym gateway" \
  60 \
  1

export COMMANDER_GYM_ARGENTUM_TOKEN="$qualification_token"
python3 -m commander_gym.service_doctor \
  --argentum-url "http://127.0.0.1:${gateway_port}" \
  --expected-build-revision "$argentum_revision" \
  > "$qualification_json"

collect_evidence
cat "$qualification_json"
echo "Portable node systemd qualification artifact: $qualification_json"
