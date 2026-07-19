#!/usr/bin/env bash
# Launch the HAL 9000 web frontend for Hermes Agent.
# Runs inside the Hermes venv — no separate environment needed.
# Plain bash so it works on Linux as well as macOS (zsh runs it fine too).
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${HAL_PORT:-8000}"
HOST="${HAL_HOST:-127.0.0.1}"

if [[ -n "${HAL_HERMES_VENV:-}" ]]; then
  HERMES_VENV="$HAL_HERMES_VENV"
elif [[ -x "$HOME/.hermes/hermes-agent/venv/bin/uvicorn" ]]; then
  HERMES_VENV="$HOME/.hermes/hermes-agent/venv"
elif [[ -x "$HOME/hermes-agent/.venv/bin/uvicorn" ]]; then
  HERMES_VENV="$HOME/hermes-agent/.venv"
else
  echo "Hermes Agent's Python environment was not found." >&2
  echo "Install Hermes Agent, or set HAL_HERMES_VENV to its environment path." >&2
  exit 1
fi

export HAL_HERMES_BIN="${HAL_HERMES_BIN:-$HERMES_VENV/bin/hermes}"
export HAL_HERMES_ACP_BIN="${HAL_HERMES_ACP_BIN:-$HERMES_VENV/bin/hermes-acp}"

# Fall back to the repo-local voice model if the shared Hermes voice install
# is absent (e.g. a machine where ~/.hermes/voices was never populated).
if [[ -z "${HAL_VOICE:-}" ]]; then
  DEFAULT_VOICE="$HOME/.hermes/voices/hal9000/hal9000.onnx"
  if [[ ! -f "$DEFAULT_VOICE" && -f "$APP_DIR/models/hal.onnx" ]]; then
    export HAL_VOICE="$APP_DIR/models/hal.onnx"
  fi
fi

# Fail fast if the port is taken — before loading two ML models and an agent.
if curl -sf -m 2 "http://$HOST:$PORT/api/health" >/dev/null 2>&1; then
  echo "HAL is already running at http://$HOST:$PORT" >&2
  exit 0
fi
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port $PORT is in use by another process (not HAL)." >&2
  echo "Pick a different port: HAL_PORT=8001 $0" >&2
  exit 1
fi

exec "$HERMES_VENV/bin/uvicorn" main:app \
  --app-dir "$APP_DIR" \
  --host "$HOST" \
  --port "$PORT"
