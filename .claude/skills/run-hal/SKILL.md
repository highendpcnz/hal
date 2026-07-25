---
name: run-hal
description: Run, start, smoke-test, screenshot, or drive the HAL 9000 voice web frontend — talk to HAL via curl (/api/say, /api/talk), check health, view the eye UI. Use when asked to run hal, test the voice loop, verify HAL is up, or debug the Hermes bridge.
---

# Run: HAL 9000 voice frontend

FastAPI web app (uvicorn, port 8000) that fronts the Hermes Agent CLI with
HAL 9000's voice: faster-whisper STT → persistent `hermes-acp` process
(Agent Client Protocol) → Piper TTS. Drive it entirely with **curl** — no
browser needed. Paths below are relative to the repo root (the directory
containing `run.sh`).

**Fastest verification:** run the driver:

```bash
.claude/skills/run-hal/smoke.sh
```

Starts the server if needed, then does the full loop: health → `/api/say`
(text → HAL-voice WAV) → `/api/talk` (feeds that WAV back in: STT → Hermes →
TTS) → `/api/history`. ~15s when warm; each say/talk is a real Hermes
inference turn. Exit 0 = whole pipeline operational.

## Prerequisites — don't assume an environment

`run.sh` selects the Python environment in this order: `$HAL_HERMES_VENV` →
repo-local `.venv` → `~/.hermes/hermes-agent/venv` → `~/hermes-agent/.venv`.
The agent binaries (`hermes`, `hermes-acp`) are resolved **separately**, since
they run as subprocesses and needn't live beside uvicorn.

**The Hermes venv cannot always run HAL.** Some Hermes installs ship without
`faster_whisper`/`piper`, and importing `main` against one fails outright.
Check before launching:

```bash
~/.hermes/hermes-agent/venv/bin/python -c "import faster_whisper, piper" 2>&1
```

If that fails, build an isolated venv — this never touches the Hermes install:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt agent-client-protocol sherpa-onnx
```

`agent-client-protocol` is required for the default `HAL_BRIDGE=acp` mode;
`sherpa-onnx` is optional (voiceprints). `run.sh` prefers this `.venv`
automatically once it exists — no env vars needed.

**Voice model:** if `~/.hermes/voices/hal9000/hal9000.onnx` is absent, run
`python3 download_model.py` to fetch `models/hal.onnx` (61 MB, gitignored) —
`run.sh` falls back to it automatically.

**espeak-ng:** needed on macOS (`brew install espeak-ng`), but **not** on
Linux — the `piper-tts` wheel bundles it. Verify by synthesizing, not by
looking for the binary.

## Run (agent path)

```bash
bin/hal --no-open     # no-op if already healthy; up to 2 min on cold boot
```

`bin/hal` execs `run.sh`, which does all detection itself. Server log:
`data/server.log`; agent (ACP) stderr: `data/acp.log`.

```bash
# health — includes ACP bridge liveness
curl -sf http://127.0.0.1:8000/api/health

# text in → WAV out (one Hermes turn, ~9s: ~8.3s inference + ~0.75s TTS)
curl -sf -c /tmp/hal.jar -D /tmp/hal.h -o /tmp/hal.wav -X POST \
  http://127.0.0.1:8000/api/say \
  -H 'Content-Type: application/json' -d '{"text": "Hello HAL"}'

# audio in → WAV out (multipart field is `audio`; WAV/webm/mp4 all accepted)
curl -sf -b /tmp/hal.jar -D /tmp/hal.h -o /tmp/reply.wav -X POST \
  http://127.0.0.1:8000/api/talk -F 'audio=@/tmp/hal.wav;type=audio/wav'

# transcripts ride response headers, percent-encoded — decode them:
grep -i x-hal-transcript /tmp/hal.h | python3 -c \
  "import sys,urllib.parse; print(urllib.parse.unquote(sys.stdin.read()))"

curl -sf -b /tmp/hal.jar http://127.0.0.1:8000/api/history
```

The `hal_session` cookie maps to one persistent Hermes session
(`hermes sessions list --source hal-web`). Reuse the cookie jar for a
continuing conversation; drop it for a fresh session.

## Screenshotting the Bridge UI

**Headless Chrome via the CLI does not work for this app.** The page holds an
SSE stream (`/api/events`) and a WebSocket (`/ws/conversation`) open, so it
never reaches network-idle and `--screenshot` hangs without ever writing the
file. `--virtual-time-budget`, `--no-sandbox`, and
`--enable-unsafe-swiftshader` do not fix it. Don't burn time on flags.

Use the **claude-in-chrome MCP** instead — WebGL and the Three.js optic render
correctly there, and you can interact (switch directions, open panels, send
turns) rather than only capture:

```
tabs_context_mcp{createIfEmpty:true} → navigate{url:"http://127.0.0.1:8000/"}
→ computer{action:"screenshot"}
```

Expected: red HAL eye left, MISSION LOG right, telemetry bar
(`BRIDGE ● ACP · STT base.en · VOICE hal.onnx · TOOLS Denied`), direction rail
`01 02 03 04` bottom-right. The rail's y-position shifts as the log fills —
re-screenshot before clicking it rather than reusing stale coordinates.

*Historical note (macOS):* there the CLI form did write the PNG, but Chrome
lingered afterwards, so it was wrapped in `timeout 45` with `--headless=new`
plus a throwaway `--user-data-dir`. That workaround does not generalize; the
MCP path is the supported one.

## Test

```bash
.venv/bin/python tests/run.py    # or <hermes-venv>/bin/python — zero-dep, seconds
.venv/bin/ruff check .
npm run check                    # tsc --noEmit && vite build
```

## Gotchas

- **Every say/talk costs a real inference turn** (~9s, provider quota). Smoke
  sessions pile up in Hermes state — tagged `--source hal-web`, harmless, but
  don't loop the driver.
- Server is a **live local service** — `run.sh`/`bin/hal` no-op when healthy,
  so never kill it just to "restart and check".
- **Transcript/timing headers are percent-encoded** (`x-user-transcript`,
  `x-hal-transcript`, `x-hal-timings`) and truncated to 2000 chars — full text
  is in `/api/history`.
- Tool permissions default to **deny** (`TOOLS Denied` in the UI);
  `HAL_PERMISSION_MODE=ask|yolo` changes that — see the README env table.
- `Dockerfile`, `.env.example`, `hal_prompt.py` are vestigial from the upstream
  HF Space fork — the Dockerfile does **not** build a working image.
  `download_model.py` is nominally vestigial too but is still the easiest way
  to fetch the voice model.

## Troubleshooting

- `ModuleNotFoundError: faster_whisper` on boot → the selected venv can't run
  HAL. Build the isolated `.venv` above; `run.sh` will prefer it.
- `{"status":"degraded", ...bridge alive:false}` → the `hermes-acp` process
  died; check `data/acp.log`. Verify hermes itself with
  `~/.hermes/hermes-agent/venv/bin/hermes --version`.
- `run.sh` prints "Port 8000 is in use by another process (not HAL)" →
  relaunch with `HAL_PORT=8001`.
- Screenshot never appears → you used the Chrome CLI. See above; use the MCP.
