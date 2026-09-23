#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARGENTUM_ENGINE_DIR="${ARGENTUM_ENGINE_DIR:-$ROOT/../argentum-engine}"
PYTHON="${PYTHON:-python3}"

: "${OPENAI_API_KEY:?OPENAI_API_KEY must be set}"

if ! "$PYTHON" -c 'import openai' >/dev/null 2>&1; then
  echo "OpenAI SDK is missing. Install it with:" >&2
  echo "  $PYTHON -m pip install -r $ROOT/requirements-openai.txt" >&2
  exit 2
fi

if [[ ! -x "$ARGENTUM_ENGINE_DIR/gradlew" ]]; then
  echo "Argentum checkout not found at $ARGENTUM_ENGINE_DIR" >&2
  echo "Set ARGENTUM_ENGINE_DIR to a Blue42hand/argentum-engine checkout containing #116." >&2
  exit 2
fi

export COMMANDER_GYM_SIDECAR_TOKEN="${COMMANDER_GYM_SIDECAR_TOKEN:-$(openssl rand -hex 32)}"
export COMMANDER_GYM_SIDECAR_HOST="${COMMANDER_GYM_SIDECAR_HOST:-127.0.0.1}"
export COMMANDER_GYM_SIDECAR_PORT="${COMMANDER_GYM_SIDECAR_PORT:-8083}"
export COMMANDER_GYM_SIDECAR_URL="${COMMANDER_GYM_SIDECAR_URL:-http://127.0.0.1:${COMMANDER_GYM_SIDECAR_PORT}}"
export COMMANDER_GYM_OPENAI_MODEL="${COMMANDER_GYM_OPENAI_MODEL:-gpt-5.6-luna}"
export COMMANDER_GYM_OPENAI_TIMEOUT="${COMMANDER_GYM_OPENAI_TIMEOUT:-120}"
export COMMANDER_GYM_OPENAI_MAX_ATTEMPTS="${COMMANDER_GYM_OPENAI_MAX_ATTEMPTS:-2}"
export COMMANDER_GYM_GUI_SERVER_PORT="${COMMANDER_GYM_GUI_SERVER_PORT:-8080}"
export COMMANDER_GYM_JVM_SIDECAR_TIMEOUT_MS="${COMMANDER_GYM_JVM_SIDECAR_TIMEOUT_MS:-120000}"
export ARGENTUM_ENGINE_DIR

if [[ -n "${COMMANDER_GYM_SIDECAR_PROVENANCE:-}" ]]; then
  mkdir -p "$(dirname "$COMMANDER_GYM_SIDECAR_PROVENANCE")"
fi

cleanup() {
  if [[ -n "${SIDECAR_PID:-}" ]]; then
    kill "$SIDECAR_PID" 2>/dev/null || true
    wait "$SIDECAR_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

cd "$ROOT"
"$PYTHON" -m commander_gym.game_server_openai_sidecar &
SIDECAR_PID=$!

"$PYTHON" - "$COMMANDER_GYM_SIDECAR_HOST" "$COMMANDER_GYM_SIDECAR_PORT" <<'PY'
import socket
import sys
import time

host = sys.argv[1]
port = int(sys.argv[2])
deadline = time.monotonic() + 20
while time.monotonic() < deadline:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            break
    except OSError:
        time.sleep(0.1)
else:
    raise SystemExit(f"Commander Gym sidecar did not open {host}:{port}")
PY

echo
echo "Commander Gym Luna sidecar is ready at $COMMANDER_GYM_SIDECAR_URL"
echo "Starting normal Argentum game server on http://127.0.0.1:$COMMANDER_GYM_GUI_SERVER_PORT"
echo
echo "In another terminal, start the browser client:"
echo "  cd $ARGENTUM_ENGINE_DIR && just client"
echo "Then open http://localhost:5173 and use the normal Play vs AI / lobby flow."
echo

exec "$ARGENTUM_ENGINE_DIR/gradlew" \
  -p "$ROOT/jvm-adapter" \
  runLocalGuiServer
