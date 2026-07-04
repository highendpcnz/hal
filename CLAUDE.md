# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A push-to-talk web frontend that makes HAL 9000 the voice of [Hermes Agent CLI](https://github.com/NousResearch/Hermes-Agent). Hold the eye, speak, release — Hermes does the thinking (full tool/shell access), and the reply is spoken back in HAL's voice. Forked from `piclez/hal` (a Groq/Claude/Piper HF Space) and rewired to run fully local with zero cloud API keys.

Pipeline: browser (MediaRecorder) → `POST /api/talk` → faster-whisper (STT) → persistent `hermes-acp` process (the "brain") → Piper HAL voice (TTS) → WAV back to browser.

## Running

```
./run.sh                # http://127.0.0.1:8000
```

Must run inside the Hermes venv (`~/hermes-agent/.venv` by default, override with `HAL_HERMES_VENV`) — it already has every dependency; `requirements.txt` is only for a standalone environment. `run.sh` fails fast if the port is already serving `/api/health` or is held by something else, before loading the STT/TTS models.

There is no test suite, linter, or build step in this repo. Verify changes by running the server and hitting the endpoints (`curl` for `/api/health`, `/api/status`, `/api/systems`; a browser for the actual voice flow).

## Architecture

**`main.py`** — FastAPI app. Owns HTTP surface, STT (faster-whisper) and TTS (Piper) model loading at import time, session-history persistence (JSON files per browser session under `data/sessions/`), and markdown-to-speech text cleanup (`speakable()`) before synthesis. Delegates all "thinking" to `hermes_bridge.ask_hermes()`.

**`hermes_bridge.py`** — The bridge to the actual agent, with two interchangeable implementations selected by `HAL_BRIDGE` (default `acp`):
- `acp`: a single persistent `hermes-acp` subprocess speaking the Agent Client Protocol (ACP) over stdio. A turn is just `session/prompt` — no per-turn CLI startup cost. ACP sessions persist to `~/.hermes/state.db`, so a browser session survives both bridge and agent restarts via `session/load`.
- `subprocess`: fallback that shells out to `hermes chat -Q -q` once per turn (contract: stdout = reply, stderr = `session_id: <id>`). Used if ACP misbehaves.

Both modes maintain a cookie-session → hermes-session-id map persisted in `HAL_DATA_DIR/hermes_sessions.json`, and serialize turns per browser session (`_cookie_locks`) since Hermes sessions are single-writer. Tool-call and permission events are fanned out over in-process `asyncio.Queue`s per cookie session and streamed to the browser via SSE (`/api/events`) to drive the eye's diegetic tint / ticker.

**Persona**: lives in [AGENTS.md](AGENTS.md) in this directory, not in code. Hermes auto-injects it because the agent process runs with this directory as cwd (`HAL_AGENT_CWD`). To change how HAL talks or what he calls the user, edit `AGENTS.md` — no global Hermes config is touched.

**Permissions**: dangerous tool-call requests from the agent (ACP `session/request_permission`) are denied by default — the bridge answers with a rejection and HAL reports the action was blocked. Set `HAL_YOLO=1` to auto-approve instead (voice-triggered shell access — treat as intentionally permissive, not a default).

**`static/index.html`** — single-file frontend: the breathing/eye UI, push-to-talk recorder, and SSE client for tool-call events. No build step or framework.

**Vestigial files** (kept for reference/upstream diffing only, not used by the running app): `Dockerfile`, `download_model.py`, `.env.example`, `hal_prompt.py`. These belong to the original cloud-API-based HF Space this was forked from — don't wire them back in without deliberately reintroducing that dependency.

## Key environment variables

All optional; full table in [README.md](README.md). The ones most likely to matter when changing code:

- `HAL_BRIDGE` (`acp` default) — which bridge implementation is active; behavior in `hermes_bridge.py` branches heavily on this.
- `HAL_AGENT_CWD` — where Hermes runs; must contain `AGENTS.md` for the persona to load.
- `HAL_YOLO` — auto-approve tool permissions instead of denying (ACP mode only).
- `HAL_DATA_DIR` (`./data`) — transcript history (`sessions/*.json`), session map (`hermes_sessions.json`), ACP subprocess log (`acp.log`).
- `HAL_OFFLINE_PREFLIGHT` — probes connectivity before sending a turn to remote inference; returns a canned HAL "disconnected" line if offline. Relevant when Hermes is configured with a remote model provider.

## Conventions

- No secrets/API keys anywhere in this app — the local pipeline (faster-whisper, Piper, Hermes CLI) needs none. Don't reintroduce `.env`-based cloud API keys as part of a feature; that's the pre-fork design this repo deliberately moved away from.
- Session identity is a `hal_session` cookie (UUID, validated against `_SESSION_ID_RE`); there is no user auth, so the server is meant to stay bound to loopback (`HAL_HOST=127.0.0.1`).
- Text returned by Hermes goes through `speakable()` before TTS to strip markdown that would otherwise be read aloud literally — extend `_MD_PATTERNS` there if new markdown constructs leak into replies.
