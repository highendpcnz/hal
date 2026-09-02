#!/usr/bin/env bash
# Launch the provider-neutral HAL 9000 web frontend.
# Plain bash so it works on Linux as well as macOS (zsh runs it fine too).
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"

# Load .env (see .env.example) without clobbering variables the caller already
# exported — an explicit `FOO=bar ./run.sh` or shell export always wins.
if [[ -f "$APP_DIR/.env" ]]; then
  while IFS='=' read -r _env_key _env_value; do
    [[ -z "$_env_key" || "$_env_key" == \#* ]] && continue
    if [[ -z "${!_env_key:-}" ]]; then
      export "$_env_key=$_env_value"
    fi
  done < <(grep -Ev '^\s*(#|$)' "$APP_DIR/.env")
fi

PORT="${HAL_PORT:-8000}"
HOST="${HAL_HOST:-127.0.0.1}"

# HAL owns its runtime environment. HAL_HERMES_VENV remains a compatibility
# alias for existing installations, but it is never searched automatically.
if [[ -n "${HAL_VENV:-}" ]]; then
  APP_VENV="$HAL_VENV"
elif [[ -n "${HAL_HERMES_VENV:-}" ]]; then
  APP_VENV="$HAL_HERMES_VENV"
elif [[ -x "$APP_DIR/.venv/bin/uvicorn" ]]; then
  APP_VENV="$APP_DIR/.venv"
else
  echo "HAL's repository-local Python environment is missing." >&2
  echo "Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  echo "Alternatively, set HAL_VENV to an environment containing the app dependencies." >&2
  exit 1
fi

# Compatibility mode resolves Hermes only when explicitly selected.
if [[ "${HAL_BRAIN:-gemma}" == "hermes" ]]; then
  if [[ -z "${HAL_HERMES_BIN:-}" ]] && command -v hermes >/dev/null 2>&1; then
    export HAL_HERMES_BIN="$(command -v hermes)"
  fi
  if [[ -z "${HAL_HERMES_ACP_BIN:-}" ]] && command -v hermes-acp >/dev/null 2>&1; then
    export HAL_HERMES_ACP_BIN="$(command -v hermes-acp)"
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

GEMMA_PID=""
cleanup() {
  if [[ -n "$GEMMA_PID" ]] && kill -0 "$GEMMA_PID" 2>/dev/null; then
    kill "$GEMMA_PID" 2>/dev/null || true
    wait "$GEMMA_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

# With no custom endpoint, own a loopback llama.cpp process for the lifetime of
# HAL. A supplied HAL_GEMMA_URL is assumed to be managed externally unless
# HAL_MANAGE_GEMMA=1 is set explicitly.
if [[ "${HAL_BRAIN:-gemma}" == "gemma" ]]; then
  GEMMA_HOST="${HAL_GEMMA_HOST:-127.0.0.1}"
  GEMMA_PORT="${HAL_GEMMA_PORT:-8080}"
  GEMMA_URL_WAS_SET="${HAL_GEMMA_URL+x}"
  export HAL_GEMMA_URL="${HAL_GEMMA_URL:-http://$GEMMA_HOST:$GEMMA_PORT/v1/chat/completions}"
  MANAGE_GEMMA="${HAL_MANAGE_GEMMA:-auto}"
  if [[ "$MANAGE_GEMMA" == "auto" ]]; then
    [[ -z "$GEMMA_URL_WAS_SET" ]] && MANAGE_GEMMA=1 || MANAGE_GEMMA=0
  fi

  if [[ "$MANAGE_GEMMA" == "1" ]]; then
    LLAMA_SERVER="${HAL_LLAMA_SERVER:-$HOME/llama.cpp/build/bin/llama-server}"
    GEMMA_MODEL="${HAL_GEMMA_MODEL_PATH:-$HOME/models/gemma-4-e2b/gemma-4-E2B-it-Q4_0.gguf}"
    GEMMA_HEALTH="http://$GEMMA_HOST:$GEMMA_PORT/v1/models"
    if ! curl -sf -m 2 "$GEMMA_HEALTH" >/dev/null 2>&1; then
      if [[ ! -x "$LLAMA_SERVER" || ! -f "$GEMMA_MODEL" ]]; then
        echo "Local Gemma assets are missing; HAL will start in degraded mode." >&2
        echo "Set HAL_LLAMA_SERVER and HAL_GEMMA_MODEL_PATH to enable inference." >&2
      else
        export HAL_GEMMA_API_KEY="${HAL_GEMMA_API_KEY:-$($APP_VENV/bin/python -c 'import secrets; print(secrets.token_hex(24))')}"
        mkdir -p "$APP_DIR/data"
        GEMMA_ARGS=(
          --model "$GEMMA_MODEL"
          --alias "${HAL_GEMMA_MODEL:-gemma-4-e2b}"
          --ctx-size "${HAL_GEMMA_CTX:-8192}"
          --parallel "${HAL_GEMMA_PARALLEL:-1}"
          --n-gpu-layers "${HAL_GEMMA_GPU_LAYERS:-99}"
          --flash-attn auto
          # Defaults to llama-server's own "auto" (template-detected). "off"
          # was tried for a real latency win on the Pixel's CPU (~25% faster
          # inference), but it turned out to cost tool-calling reliability --
          # Gemma started confidently claiming to have driven the robot
          # without ever calling the tool, confirmed live and reproduced
          # twice (see docs/termux-port-status.md). Reverted to "auto" as
          # the default until that tradeoff is revisited; set
          # HAL_GEMMA_REASONING=off to trade reliability back for speed.
          --reasoning "${HAL_GEMMA_REASONING:-auto}"
          --host "$GEMMA_HOST"
          --port "$GEMMA_PORT"
          --api-key "$HAL_GEMMA_API_KEY"
          --no-webui
        )
        if [[ -n "${HAL_GEMMA_MMPROJ:-}" ]]; then
          GEMMA_ARGS+=(--mmproj "$HAL_GEMMA_MMPROJ")
        fi
        # CPU thread count/affinity tuning: off (llama-server's own defaults)
        # unless explicitly set. Deliberately not a shared default — this
        # matters on Termux/Android's heterogeneous big.LITTLE ARM cores
        # (confirmed live on the Pixel's Tensor G2: pinning 4 threads to the
        # 4 fast cores nearly doubled token generation, 7.3->13.3 tok/s,
        # while unpinned or wrongly-sized configs were *worse* than doing
        # nothing at all — see docs/termux-port-status.md), and is
        # meaningless-to-harmful on the Mac, which does its real compute on
        # the Metal GPU via --n-gpu-layers, not CPU threading. Set
        # HAL_GEMMA_THREADS/HAL_GEMMA_CPU_MASK/HAL_GEMMA_CPU_STRICT in the
        # phone's own .env, not here.
        if [[ -n "${HAL_GEMMA_THREADS:-}" ]]; then
          GEMMA_ARGS+=(--threads "$HAL_GEMMA_THREADS" --threads-batch "$HAL_GEMMA_THREADS")
        fi
        if [[ -n "${HAL_GEMMA_CPU_MASK:-}" ]]; then
          GEMMA_ARGS+=(--cpu-mask "$HAL_GEMMA_CPU_MASK" --cpu-mask-batch "$HAL_GEMMA_CPU_MASK")
        fi
        if [[ -n "${HAL_GEMMA_CPU_STRICT:-}" ]]; then
          GEMMA_ARGS+=(--cpu-strict "$HAL_GEMMA_CPU_STRICT" --cpu-strict-batch "$HAL_GEMMA_CPU_STRICT")
        fi
        "$LLAMA_SERVER" "${GEMMA_ARGS[@]}" >"$APP_DIR/data/gemma-server.log" 2>&1 &
        GEMMA_PID=$!
        for _attempt in {1..240}; do
          if curl -sf -m 1 -H "Authorization: Bearer $HAL_GEMMA_API_KEY" \
            "$GEMMA_HEALTH" >/dev/null 2>&1; then
            break
          fi
          if ! kill -0 "$GEMMA_PID" 2>/dev/null; then
            echo "llama-server exited during startup; see data/gemma-server.log" >&2
            exit 1
          fi
          sleep 0.25
        done
        if ! curl -sf -m 1 -H "Authorization: Bearer $HAL_GEMMA_API_KEY" \
          "$GEMMA_HEALTH" >/dev/null 2>&1; then
          echo "Timed out waiting for local Gemma; see data/gemma-server.log" >&2
          exit 1
        fi
      fi
    fi
  fi
fi

"$APP_VENV/bin/uvicorn" main:app \
  --app-dir "$APP_DIR" \
  --host "$HOST" \
  --port "$PORT"
