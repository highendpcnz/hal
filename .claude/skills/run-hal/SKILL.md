---
name: run-hal
description: Run, start, smoke-test, screenshot, or drive the HAL 9000 voice web frontend — talk to HAL via curl (/api/say, /api/talk), check health, view the eye UI. Use when asked to run hal, test the voice loop, verify HAL is up, or debug the Hermes bridge.
---

# Run: HAL 9000 voice frontend

FastAPI web app (uvicorn, port 8000) that fronts the Hermes Agent CLI with
HAL 9000's voice: faster-whisper STT → persistent `hermes-acp` process
(Agent Client Protocol) → Piper TTS. Drive it entirely with **curl** — no
browser needed. Paths are relative to this repo (`~/Documents/hal`).

**Fastest verification:** run the driver:

```bash
.claude/skills/run-hal/smoke.sh
```

Starts the server if needed, then does the full loop: health → `/api/say`
(text → HAL-voice WAV) → `/api/talk` (feeds that WAV back in: STT → Hermes →
TTS) → `/api/history`. ~15s when warm; each say/talk is a real Hermes
inference turn. Exit 0 = whole pipeline operational.

## Prerequisites

Runs inside the **Hermes venv** — no environment of its own. On this
machine that venv is `~/.hermes/hermes-agent/venv` (see gotcha #1). Hermes
must be authenticated (it is; see the `run-hermes-agent` skill in
`~/.hermes/hermes-agent`). Voice model: `~/.hermes/voices/hal9000/hal9000.onnx`.

## Run (agent path)

Launch (no-op if already healthy — safe to run anytime):

```bash
~/.local/bin/hal --no-open     # starts via bin/hal, waits for health, up to 2 min on cold boot
```

`~/.local/bin/hal` is a wrapper that sets `HAL_HERMES_VENV`,
`HAL_HERMES_BIN`, `HAL_HERMES_ACP_BIN` to the real venv paths before
exec-ing `bin/hal`. Server log: `data/server.log`; agent (ACP) stderr:
`data/acp.log`.

Talk to HAL programmatically:

```bash
# health — includes ACP bridge liveness
curl -sf http://127.0.0.1:8000/api/health

# text in → WAV out (one Hermes turn, ~6s)
curl -sf -c /tmp/hal.jar -D /tmp/hal.h -o /tmp/hal.wav -X POST \
  http://127.0.0.1:8000/api/say \
  -H 'Content-Type: application/json' -d '{"text": "Hello HAL"}'

# audio in → WAV out (multipart field is `audio`; WAV/webm/mp4 all accepted)
curl -sf -b /tmp/hal.jar -D /tmp/hal.h -o /tmp/reply.wav -X POST \
  http://127.0.0.1:8000/api/talk -F 'audio=@/tmp/hal.wav;type=audio/wav'

# transcripts ride response headers, percent-encoded — decode them:
grep -i x-hal-transcript /tmp/hal.h | python3 -c \
  "import sys,urllib.parse; print(urllib.parse.unquote(sys.stdin.read()))"

# conversation history + journaled events for the cookie's session
curl -sf -b /tmp/hal.jar http://127.0.0.1:8000/api/history
```

The `hal_session` cookie maps to one persistent Hermes session
(`hermes sessions list --source hal-web`). Reuse the cookie jar for a
continuing conversation; drop it for a fresh session.

Screenshot the eye (Bridge UI) headlessly:

```bash
timeout 45 "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --headless=new --disable-gpu --user-data-dir="$(mktemp -d)" \
  --screenshot=/tmp/hal_eye.png --window-size=1280,900 --hide-scrollbars \
  http://127.0.0.1:8000/ 2>/dev/null || true   # Chrome hangs AFTER writing the PNG — timeout is the cleanup
```

Expected: red HAL eye left, MISSION LOG right, telemetry bar bottom
(`BRIDGE ● ACP · STT base.en · VOICE hal9000.onnx · TOOLS Denied`).

## Run (human path)

`hal` (opens browser) or `./run.sh` (foreground uvicorn). Hold the eye or
Space to speak; `/` opens the typed command line; `/mission <title>` starts
a background mission. Useless headless — use the curl path above.

## Test

```bash
~/.hermes/hermes-agent/venv/bin/python tests/run.py   # zero-dep, seconds, "all tests passed"
```

(README says `~/hermes-agent/.venv/...` — wrong path on this machine.)

## Gotchas

- **`run.sh`'s default venv path is wrong for this machine.** It defaults
  `HAL_HERMES_VENV` to `$HOME/hermes-agent/.venv`, which does not exist —
  the real venv is `$HOME/.hermes/hermes-agent/venv`. Always launch via
  `~/.local/bin/hal`, or export `HAL_HERMES_VENV` first.
- **Transcript/timing headers are percent-encoded** (`x-user-transcript`,
  `x-hal-transcript`, `x-hal-timings`) and truncated to 2000 chars — full
  text is in `/api/history`.
- **Headless Chrome hangs with the default profile** while desktop Chrome
  is running. Use `--headless=new` **and** a throwaway `--user-data-dir`
  (the plain `--headless --screenshot` form never wrote the file). Even
  then Chrome lingers after writing the PNG — hence the `timeout 45`
  wrapper (homebrew coreutils); the screenshot lands regardless.
- **Every say/talk costs a real inference turn** (~6s, provider quota).
  Smoke sessions pile up in Hermes state — they're tagged
  `--source hal-web` and harmless, but don't loop the driver.
- Server is a **live local service** — `run.sh`/`bin/hal` already no-op
  when healthy, so never kill it just to "restart and check".
- Tool permissions default to **deny** (`TOOLS Denied` in the UI);
  `HAL_PERMISSION_MODE=ask|yolo` changes that — see README table for all
  env knobs.
- `Dockerfile`, `download_model.py`, `.env.example`, `hal_prompt.py` are
  vestigial from the upstream HF Space fork — the Dockerfile does **not**
  build a working image.

## Troubleshooting

- `{"status":"degraded", ...bridge alive:false}` from `/api/health` → the
  `hermes-acp` process died; check `data/acp.log`, then restart via
  `~/.local/bin/hal`. Verify hermes itself with the `run-hermes-agent`
  smoke driver first.
- `run.sh` prints "Port 8000 is in use by another process (not HAL)" →
  something else grabbed the port; relaunch with `HAL_PORT=8001`.
- Screenshot PNG never appears, Chrome exits silently → you used plain
  `--headless` with the desktop profile; add `--headless=new` +
  `--user-data-dir` (gotcha above).
