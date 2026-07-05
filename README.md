# HAL 9000 — voice frontend for Hermes Agent

Push-to-talk web interface that makes HAL 9000 the face (and voice) of
[Hermes Agent CLI](https://github.com/NousResearch/Hermes-Agent). Hold the
red eye, speak, release — Hermes does the thinking (with full tool access),
and the reply comes back in HAL's voice.

On screens wider than 760px the eye sits in a **Bridge** layout — mission log,
telemetry bar, live waveform, mission cards, always-visible input — with an
optional full-duplex mode (always-on mic with voice detection, barge-in, live
interim captions, and an optional "HAL, …" wake-word gate) and background
**missions** HAL reports on when they finish. With
`HAL_PERMISSION_MODE=ask`, HAL asks out loud before running a tool and waits
for your yes/no (or an on-screen Allow/Deny).

Forked from [piclez/hal](https://huggingface.co/spaces/piclez/hal) and rewired
to run fully local with zero cloud API keys:

| Stage | Original (HF Space)     | This fork                                          |
|-------|-------------------------|----------------------------------------------------|
| STT   | Groq Whisper API        | faster-whisper, local (`base.en` by default)       |
| Brain | Claude API              | **Hermes Agent CLI** — named sessions, tools, skills |
| TTS   | Piper (plain)           | [campwill/HAL-9000-Piper-TTS](https://huggingface.co/campwill/HAL-9000-Piper-TTS) with Hermes' HAL text normalization and optional ffmpeg mastering |

## How it works

```
browser (hold-to-talk)
  └─ POST /api/talk (webm/mp4 audio)
       ├─ faster-whisper  → transcript
       ├─ persistent hermes-acp process (Agent Client Protocol over stdio)
       │    · session/prompt per turn — no CLI startup cost (~4s/turn all-in)
       │    · cookie session ↔ ACP session map: data/hermes_sessions.json
       │    · ACP sessions persist in ~/.hermes/state.db → survive restarts
       │      of both the bridge and the agent (session/load)
       │    · persona injected via AGENTS.md in this directory (cwd rules)
       │    · agent stderr → data/acp.log
       └─ piper (hal9000.onnx) → WAV reply
```

The bridge speaks ACP through the official `agent-client-protocol` library
(installed in the Hermes venv as the `hermes-agent[acp]` extra). Set
`HAL_BRIDGE=subprocess` to fall back to the original one-`hermes chat -Q`
-per-turn bridge if ACP ever misbehaves.

- **Persona** lives in [AGENTS.md](AGENTS.md) — Hermes auto-injects it because the
  agent runs with this directory as cwd. Edit it to change how HAL speaks or
  what he calls you. No global Hermes config is touched.
- **Sessions**: each browser session continues one Hermes session
  (`hermes sessions list --source hal-web`). Clear the `hal_session` cookie
  for a fresh one. Hermes' builtin memory still carries facts across sessions.
- **Tools**: it's real Hermes — "Hal, what's in my downloads folder?" works.
  Dangerous-command permission requests follow `HAL_PERMISSION_MODE`:
  - `deny` (default) — every request is rejected and HAL tells you the
    action was blocked.
  - `ask` — HAL asks ("Dave, I need your permission: …") and waits up to
    `HAL_PERMISSION_TIMEOUT` seconds. Answer by voice or text ("yes" /
    "go ahead" / "no" / "deny"), or with the Allow/Deny bar in the UI.
    Unanswered requests are denied.
  - `yolo` — auto-approve everything (`HAL_YOLO=1` is the legacy alias),
    if you accept voice-triggered shell access.

## Run

```
./run.sh                # http://127.0.0.1:8000
```

Runs inside the Hermes venv — no separate environment, no `.env`, no API keys.
First start downloads the STT model (~75 MB) to the Hugging Face cache.
`run.sh` is plain bash and works on macOS and Linux.

Or just type `hal` anywhere — [bin/hal](bin/hal) starts the server if
needed, waits for it to become healthy, and opens the eye in your browser
(`hal --no-open` skips the tab). Install it once with:

```
ln -s "$(pwd)/bin/hal" ~/.local/bin/hal
```

## Tests

```
~/hermes-agent/.venv/bin/python tests/run.py
```

Zero-dependency (no pytest) checks of the pure-python parts — `speakable()`
markdown stripping, session-id validation, history persistence, mission
lifecycle/caps/triggers, the permission registry, CLI output cleanup, and
frontend/backend protocol-contract invariants. Sets `HAL_SKIP_MODELS=1`
internally so no models load; finishes in seconds. The same suite plus
`ruff check .` runs in CI on every push. The audio/inference pipeline is
still verified by running the server.

## Configuration (env vars, all optional)

| Var | Default | Purpose |
|-----|---------|---------|
| `HAL_PORT` / `HAL_HOST` | `8000` / `127.0.0.1` | bind address (keep loopback; there is no auth) |
| `HAL_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Host-header allowlist (blocks DNS rebinding); add your hostname/IP — or `*` — if you bind beyond loopback |
| `HAL_STT_MODEL` | `base.en` | any faster-whisper model; `small.en` = better accuracy, slower |
| `HAL_VOICE` | `~/.hermes/voices/hal9000/hal9000.onnx` | Piper voice model |
| `HAL_BRIDGE` | `acp` | `acp` = persistent agent process; `subprocess` = one CLI call per turn |
| `HAL_PERMISSION_MODE` | `deny` | `deny` / `ask` / `yolo` — how ACP tool-permission requests are answered (see above) |
| `HAL_PERMISSION_TIMEOUT` | `30` | seconds an `ask` waits before the request is denied |
| `HAL_YOLO` | *(unset)* | legacy alias: `1` = `HAL_PERMISSION_MODE=yolo` |
| `HAL_MAX_ACTIVE_MISSIONS` | `3` | per-session cap on concurrently running background missions |
| `HAL_TRIGGERS_POLL` | `30` | seconds between `data/triggers.json` scans |
| `HAL_INTERIM_STT` | `1` | live interim captions while a duplex utterance records; `0` disables |
| `HAL_HERMES_ACP_BIN` | `~/hermes-agent/.venv/bin/hermes-acp` | ACP adapter path |
| `HAL_HERMES_BIN` | `~/hermes-agent/.venv/bin/hermes` | Hermes CLI path (subprocess mode) |
| `HAL_HERMES_ARGS` | *(empty)* | extra CLI args in subprocess mode, e.g. `-m gpt-5.4` or `--yolo` |
| `HAL_AGENT_CWD` | this directory | agent working dir (must contain AGENTS.md for the persona) |
| `HAL_AGENT_TIMEOUT` | `180` | seconds before a turn is abandoned |
| `HAL_OFFLINE_PREFLIGHT` | `1` | check connectivity before remote inference; set `0` for a local-only provider |
| `HAL_OFFLINE_CHECK_HOSTS` | `1.1.1.1:443,api.openai.com:443,openrouter.ai:443` | comma-separated connectivity probes, checked in parallel |
| `HAL_OFFLINE_CHECK_TIMEOUT` / `HAL_OFFLINE_CHECK_TTL` | `0.4` / `30` | seconds for the offline preflight timeout and cache |
| `HAL_LATENCY_LOG` | `1` | log per-turn timing breakdowns on the server |
| `HAL_TTS_MASTERING` | `0` | set `1` to restore ffmpeg mastering at the cost of extra latency |
| `HAL_LENGTH_SCALE` etc. | `1.08 / 0.6 / 0.72` | voice pacing/timbre knobs |
| `HAL_DATA_DIR` | `./data` | transcript history + session map |
| `HAL_STT_PROMPT` | *(empty)* | optional whisper bias prompt (helps it spell "HAL"/"Hermes"; can hallucinate on silence) |
| `HAL_STT_BEAM` | `5` | whisper beam size; `1` (greedy) is ~2x faster with slightly lower accuracy |
| `HAL_MAX_UPLOAD_MB` | `25` | reject recordings larger than this (413) |
| `HAL_COOKIE_MAX_AGE_DAYS` | `180` | lifetime of the `hal_session` cookie |
| `HAL_SYSTEMS_TTL` | `20` | seconds to cache the `/api/systems` CLI surfaces |

## Endpoints

- `GET /` — the eye (hold it, or hold the space bar, to speak; press the eye
  while HAL is talking to barge in; `/` opens a typed command line)
- `POST /api/talk` — multipart audio in, WAV out (`X-User-Transcript` /
  `X-Hal-Transcript` response headers, truncated to 2000 chars; full text
  in `/api/history`). Add `?stream=1` for raw 16-bit mono PCM (`audio/L16`,
  rate in `X-Hal-Sample-Rate`) streamed sentence-by-sentence as Piper
  synthesizes — the browser starts playing after the first sentence.
- `POST /api/say` — `{"text": "..."}` in, WAV out; same pipeline minus the
  mic; supports the same `?stream=1`
- `POST /api/session/reset` — forget this browser session's Hermes thread and
  history, issue a fresh cookie (also a button in the Systems drawer)
- `GET /api/health` — includes ACP bridge liveness (`status: degraded` if the
  agent process is down)
- `GET /api/status` / `GET /api/systems` / `GET /api/history` — what the
  Systems drawer reads; `/api/systems?refresh=1` bypasses its cache.
  `/api/history` also returns `events`: the journaled terminal
  tool/permission/mission events that let the Bridge log survive a reload
- `GET /api/missions` — this session's missions (plus trigger-created ones),
  newest first; feeds the Missions cards on the desktop Bridge
- `POST /api/permission/{request_id}` — `{"decision": "allow"|"deny"}`;
  answers a pending `ask`-mode tool-permission request (the Allow/Deny bar)
- `GET /api/events` — SSE stream of tool-call/permission/mission events for
  the eye; mission-owned events carry a `mission_session` tag
- `WS /ws/conversation` — full-duplex channel used by the Bridge UI and duplex
  mode: client sends `start_speech` + binary audio + `end_speech` (or
  `text_input`, or `set_mode` to toggle the wake-word gate), server answers
  with `transcript` frames, `tts_start`, raw PCM, `tts_done` — plus
  `interim_transcript` while you're still speaking — or `turn_aborted` when
  there is nothing to say (`reason: no_wake_word` for gated ambient speech).
  HAL can speak first on this channel (mission reports, permission prompts).

## Missions (background tasks)

Type `/mission <title>` in the Bridge input — or say "HAL, start mission
<title>" — to run a task in the background. Each mission gets its own Hermes
session seeded with the recent conversation for context; its tool calls
stream into the Mission Log and its card in the Missions panel (status,
elapsed time, tool count — click to expand the result). The record persists
in `data/missions/`, HAL announces a result summary over the live connection
when it finishes, and the full report is fed back into your session's brain
on the next turn — so "what did you find?" works. At most
`HAL_MAX_ACTIVE_MISSIONS` run per session at once.

Missions obey the same permission model as everything else: with `deny` they
can't run tools; `ask` routes their permission prompts to your Bridge; set
`yolo` only if you accept unattended tool access. Failed missions are also
journaled to `data/missions/failed.jsonl` — food for
`reflection/reflection_loop.py --transcript data/missions/failed.jsonl`.

### Triggers (HAL starts missions on his own)

Create `data/triggers.json` to have HAL open missions without being asked —
on a schedule or when files change:

```json
[
  {"title": "Morning systems check", "prompt": "Check disk space, memory, and recent errors. Report anomalies.", "every_minutes": 480},
  {"title": "Downloads watcher", "prompt": "A new file arrived in Downloads. Identify it and suggest where it belongs.", "watch": "~/Downloads/*"}
]
```

`every_minutes` fires on an interval (armed at boot, no startup storm);
`watch` fires when the newest mtime under the glob advances; `"enabled":
false` disables an entry. The file is re-read every `HAL_TRIGGERS_POLL`
seconds, so edits apply without a restart. Trigger missions report to every
connected Bridge session and show in everyone's Missions panel.

## Autostart at login (optional)

```
cat > ~/Library/LaunchAgents/com.hal9000.frontend.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.hal9000.frontend</string>
  <key>ProgramArguments</key><array>
    <string>/bin/zsh</string><string>REPLACE_WITH/hal/run.sh</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict></plist>
EOF
launchctl load ~/Library/LaunchAgents/com.hal9000.frontend.plist
```

## Vestigial files from the original Space

`Dockerfile`, `download_model.py`, `.env.example`, and `hal_prompt.py` are no
longer used by this fork (persona and user context now live in `AGENTS.md`).
They are kept for reference / upstream diffing. In particular the Dockerfile
predates the Hermes rewiring and **does not build a working image** — it
neither copies `hermes_bridge.py`/`mission_control.py` nor provides the
Hermes CLI or the HAL voice model.

## Notes

- English only (the HAL Piper voice is English-only by design).
- Audio replies are 22,050 Hz mono WAV — browsers play this natively.
- STT on push-to-talk audio is decent with `base.en`; if HAL mishears you,
  set `HAL_STT_MODEL=small.en`.
