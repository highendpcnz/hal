# HAL 9000 — voice frontend for Hermes Agent

Push-to-talk web interface that makes HAL 9000 the face (and voice) of
[Hermes Agent CLI](https://github.com/NousResearch/Hermes-Agent). Hold the
red eye, speak, release — Hermes does the thinking (with full tool access),
and the reply comes back in HAL's voice.

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
  Dangerous-command permission requests are **denied by default**: the bridge
  answers the ACP `session/request_permission` with a rejection and HAL tells
  you the action was blocked. Set `HAL_YOLO=1` to auto-approve instead, if
  you accept voice-triggered shell access.

## Run

```
./run.sh                # http://127.0.0.1:8000
```

Runs inside the Hermes venv — no separate environment, no `.env`, no API keys.
First start downloads the STT model (~75 MB) to the Hugging Face cache.

Or just type `hal` anywhere (installed at `~/.local/bin/hal`) — it starts the
server if needed, waits for it to become healthy, and opens the eye in your
browser. `hal --no-open` starts it without opening a browser tab.

## Configuration (env vars, all optional)

| Var | Default | Purpose |
|-----|---------|---------|
| `HAL_PORT` / `HAL_HOST` | `8000` / `127.0.0.1` | bind address (keep loopback; there is no auth) |
| `HAL_STT_MODEL` | `base.en` | any faster-whisper model; `small.en` = better accuracy, slower |
| `HAL_VOICE` | `~/.hermes/voices/hal9000/hal9000.onnx` | Piper voice model |
| `HAL_BRIDGE` | `acp` | `acp` = persistent agent process; `subprocess` = one CLI call per turn |
| `HAL_YOLO` | *(unset)* | `1` auto-approves dangerous-tool permission requests (ACP mode) |
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

## Endpoints

- `GET /` — the eye
- `POST /api/talk` — multipart audio in, WAV out (`X-User-Transcript` /
  `X-Hal-Transcript` response headers)
- `POST /api/say` — `{"text": "..."}` in, WAV out; same pipeline minus the mic
- `GET /api/health`

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

`Dockerfile`, `download_model.py`, `.env.example`, `profile.md`, and
`hal_prompt.py` are no longer used by this fork (persona and user context now
live in `AGENTS.md`). They are kept for reference / upstream diffing.

## Notes

- English only (the HAL Piper voice is English-only by design).
- Audio replies are 22,050 Hz mono WAV — browsers play this natively.
- STT on push-to-talk audio is decent with `base.en`; if HAL mishears you,
  set `HAL_STT_MODEL=small.en`.
