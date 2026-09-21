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

export COMMANDER_GYM_LIVE_OPENAI_ACCEPTANCE=1
export COMMANDER_GYM_OPENAI_MODEL="${COMMANDER_GYM_OPENAI_MODEL:-gpt-5.6-luna}"
export ARGENTUM_ENGINE_DIR

exec "$ARGENTUM_ENGINE_DIR/gradlew" \
  -p "$ROOT/jvm-adapter" \
  test \
  --tests org.commandergym.argentum.LiveCommanderPodAcceptanceTest
