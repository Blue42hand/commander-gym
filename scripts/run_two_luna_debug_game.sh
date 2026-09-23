#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARGENTUM_ENGINE_DIR="${ARGENTUM_ENGINE_DIR:-$ROOT/../argentum-engine}"
PYTHON="${PYTHON:-python3}"
DECK_A="${COMMANDER_GYM_DEBUG_DECK_A:-$ARGENTUM_ENGINE_DIR/e2e-scenarios/decks/standard-monou.json}"
DECK_B="${COMMANDER_GYM_DEBUG_DECK_B:-$ARGENTUM_ENGINE_DIR/e2e-scenarios/decks/uw-tempo.json}"
TIMEOUT="${COMMANDER_GYM_TWO_LUNA_TIMEOUT_SECONDS:-900}"

: "${OPENAI_API_KEY:?OPENAI_API_KEY must be set}"

if [[ ! -x "$ARGENTUM_ENGINE_DIR/gradlew" ]]; then
  echo "Argentum checkout not found at $ARGENTUM_ENGINE_DIR" >&2
  exit 2
fi
if [[ ! -f "$DECK_A" || ! -f "$DECK_B" ]]; then
  echo "Debug decks not found:" >&2
  echo "  $DECK_A" >&2
  echo "  $DECK_B" >&2
  exit 2
fi
if ! "$PYTHON" -c 'import openai' >/dev/null 2>&1; then
  echo "OpenAI SDK is missing. Install it with:" >&2
  echo "  $PYTHON -m pip install -r $ROOT/requirements-openai.txt" >&2
  exit 2
fi

free_port() {
  "$PYTHON" - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
}

OUT_DIR="${COMMANDER_GYM_DEBUG_OUTPUT_DIR:-$(mktemp -d -t commander-gym-two-luna.XXXXXX)}"
mkdir -p "$OUT_DIR"
SERVER_LOG="$OUT_DIR/server.log"
PROVENANCE="$OUT_DIR/policy.jsonl"

export COMMANDER_GYM_GUI_SERVER_PORT="${COMMANDER_GYM_GUI_SERVER_PORT:-$(free_port)}"
export COMMANDER_GYM_SIDECAR_PORT="${COMMANDER_GYM_SIDECAR_PORT:-$(free_port)}"
export COMMANDER_GYM_SIDECAR_URL="http://127.0.0.1:$COMMANDER_GYM_SIDECAR_PORT"
export COMMANDER_GYM_SIDECAR_PROVENANCE="$PROVENANCE"
export COMMANDER_GYM_OPENAI_MODEL="${COMMANDER_GYM_OPENAI_MODEL:-gpt-5.6-luna}"
export ARGENTUM_ENGINE_DIR

STACK_PID=""
cleanup() {
  if [[ -n "$STACK_PID" ]]; then
    kill "$STACK_PID" 2>/dev/null || true
    wait "$STACK_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

cd "$ROOT"
bash scripts/run_local_gui_human_luna_server.sh >"$SERVER_LOG" 2>&1 &
STACK_PID=$!

"$PYTHON" - "$COMMANDER_GYM_GUI_SERVER_PORT" "$STACK_PID" <<'PY'
import os
import socket
import sys
import time

port = int(sys.argv[1])
pid = int(sys.argv[2])
deadline = time.monotonic() + 120
while time.monotonic() < deadline:
    try:
        os.kill(pid, 0)
    except OSError:
        raise SystemExit("local Luna stack exited before the game server became ready")
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            break
    except OSError:
        time.sleep(0.25)
else:
    raise SystemExit(f"Argentum game server did not open 127.0.0.1:{port}")
PY

set +e
"$PYTHON" -m commander_gym.two_luna_debug   --server-url "http://127.0.0.1:$COMMANDER_GYM_GUI_SERVER_PORT"   --deck-a "$DECK_A"   --deck-b "$DECK_B"   --timeout "$TIMEOUT"   --provenance "$PROVENANCE"   --server-log "$SERVER_LOG"
STATUS=$?
set -e

echo
echo "Two-Luna debug artifacts:"
echo "  server log: $SERVER_LOG"
echo "  policy provenance: $PROVENANCE"

exit "$STATUS"
