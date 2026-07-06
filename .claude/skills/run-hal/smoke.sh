#!/usr/bin/env bash
# End-to-end smoke test for the HAL 9000 voice frontend.
# Starts the server if needed, then: health → /api/say (text→WAV) →
# /api/talk (HAL listens to his own WAV: STT→brain→TTS) → /api/history.
# Each say/talk call is a real Hermes inference turn (~6-7s each).
set -euo pipefail

HOST="${HAL_HOST:-127.0.0.1}"; PORT="${HAL_PORT:-8000}"; URL="http://$HOST:$PORT"
APP_DIR="$(cd "$(dirname "$0")/../../.." && pwd)"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
JAR="$WORK/cookies.txt"

decode() { python3 -c "import sys,urllib.parse; print(urllib.parse.unquote(sys.argv[1]))" "$1"; }
hdr() { grep -i "^$2:" "$1" | cut -d' ' -f2- | tr -d '\r'; }

if ! curl -sf -m 2 "$URL/api/health" >/dev/null 2>&1; then
  echo "── starting HAL (first boot loads STT+TTS models — up to 2 min)"
  HAL_HERMES_VENV="${HAL_HERMES_VENV:-$HOME/.hermes/hermes-agent/venv}" "$APP_DIR/bin/hal" --no-open
fi

echo "── health"
curl -sf "$URL/api/health"; echo

echo "── /api/say (text → HAL voice WAV)"
curl -sf -c "$JAR" -D "$WORK/say.h" -o "$WORK/say.wav" -X POST "$URL/api/say" \
  -H 'Content-Type: application/json' \
  -d '{"text": "Say exactly: All systems are functional."}'
file "$WORK/say.wav" | grep -q 'WAVE audio' || { echo "FAIL: /api/say did not return a WAV" >&2; exit 1; }
echo "HAL said: $(decode "$(hdr "$WORK/say.h" x-hal-transcript)")"

echo "── /api/talk (HAL listens to his own WAV: STT → brain → TTS)"
curl -sf -b "$JAR" -c "$JAR" -D "$WORK/talk.h" -o "$WORK/talk.wav" -X POST "$URL/api/talk" \
  -F "audio=@$WORK/say.wav;type=audio/wav"
file "$WORK/talk.wav" | grep -q 'WAVE audio' || { echo "FAIL: /api/talk did not return a WAV" >&2; exit 1; }
echo "HAL heard: $(decode "$(hdr "$WORK/talk.h" x-user-transcript)")"
echo "HAL said:  $(decode "$(hdr "$WORK/talk.h" x-hal-transcript)")"
echo "timings:   $(decode "$(hdr "$WORK/talk.h" x-hal-timings)")"

echo "── /api/history (same cookie session)"
curl -sf -b "$JAR" "$URL/api/history" | python3 -c "
import json, sys
h = json.load(sys.stdin)['history']
assert len(h) >= 4, f'expected >=4 turns, got {len(h)}'
print(f'{len(h)} turns recorded; last: {h[-1][\"content\"][:60]!r}')"

echo "OK — full voice loop operational"
