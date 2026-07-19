# Repository Analysis

*Written July 2026, against the state of `main` at `fcb7b99` plus the fixes
landed alongside this document. A point-in-time review — file names are
stable, line numbers are not.*

## Overview

This repo is a **fully local voice interface that puts HAL 9000's face and
voice on the Hermes Agent CLI**. A FastAPI server chains three stages —
faster-whisper (STT) → Hermes Agent over the Agent Client Protocol (the
"brain", with real tool access) → Piper TTS with a HAL-9000 voice model —
behind a single-file web UI centered on a CSS-rendered HAL eye.

It began in April 2026 as a cloud-backed MVP (Groq Whisper + Claude API,
forked from the `piclez/hal` Hugging Face Space), was rewired in July 2026 to
run with zero cloud keys, then expanded with the "Discovery One" upgrade
(`docs/plans/discovery_one_upgrade.md`): a desktop Bridge UI, full-duplex
voice over WebSocket, and background missions.

## Architecture

A voice turn flows:

```
browser (eye press / space bar / duplex VAD / typed input)
  └─ POST /api/talk | POST /api/say | WS /ws/conversation
       ├─ faster-whisper → transcript
       ├─ hermes_bridge.ask_hermes()
       │    · offline preflight (cached TCP probes)
       │    · per-session lock — Hermes sessions are single-writer
       │    · ACP mode: one persistent hermes-acp process, session/prompt
       │      per turn; subprocess mode: one `hermes chat -Q` per turn
       │    · tool-permission requests denied unless HAL_YOLO=1
       ├─ speakable() — strip markdown/tables/emoji for TTS
       └─ Piper → WAV, or sentence-by-sentence PCM streaming
```

| Component | Role |
|---|---|
| `main.py` | FastAPI app: HTTP + SSE + WebSocket endpoints, STT/TTS pipeline, per-cookie history in `data/sessions/` |
| `hermes_bridge.py` | Brain bridge (ACP default, subprocess fallback), cookie→Hermes session map, SSE event fan-out with mission aliasing |
| `mission_control.py` | Background missions: own Hermes session each, persisted in `data/missions/`, completion spoken over the live WebSocket |
| `static/index.html` | Entire frontend — vanilla JS, no build step; three responsive layouts (mobile eye, tablet, desktop Bridge) |
| `tests/run.py` | Zero-dependency suite; `HAL_SKIP_MODELS=1` keeps it model-free and fast |
| `reflection/` | Documented experiment: LLM drafts skills from agent transcripts, promotion gated behind human confirmation |
| `AGENTS.md` | The HAL persona, auto-injected because the agent runs with this directory as cwd |
| `Dockerfile`, `hal_prompt.py`, `download_model.py`, `.env.example` | Vestigial (pre-rewiring); the Dockerfile does not build a working image |

Design decisions worth knowing before touching the code:

- **Sessions are three-layered**: browser cookie (`hal_session`) → Hermes/ACP
  session id (persisted map in `data/hermes_sessions.json`) → Hermes' own
  state in `~/.hermes/state.db`. ACP `session/load` makes conversations
  survive restarts of both the bridge and the agent.
- **Concurrency is deliberate**: per-session turn locks (Hermes sessions are
  single-writer), a global TTS lock (espeak-ng keeps global state), and
  synthesis in a worker thread feeding a queue so the event loop never
  blocks and a slow consumer never holds the TTS lock.
- **Deny-by-default permissions**: the ACP `session/request_permission`
  callback rejects unless `HAL_YOLO=1`; denials surface in the UI (eye
  flicker + mission log entry).
- **Failure paths speak in character** — timeout, offline, and error cases
  all produce spoken HAL lines instead of silence.

## Strengths

- Comments explain *constraints*, not mechanics (why the TTS lock exists,
  why transcript headers are capped, why mission task references must be
  kept alive).
- Docs match the code. The README's env table, endpoint list, and protocol
  description are accurate — rare at any repo size.
- The test suite is pragmatic: zero dependencies, seconds to run, and it
  even statically asserts two frontend JS invariants (brace-matching
  function bodies out of `index.html`) to pin a specific UI-wedge regression.
- The reflection loop treats LLM output as untrusted input (slug-validated
  skill names because the name becomes a directory) and is honest that its
  "sandbox review" is advisory, with a human gate as the real control.

## Review findings

All of these were addressed in the same change series that added this
document:

| # | Finding | Resolution |
|---|---|---|
| 1 | **Lock-eviction race** in `hermes_bridge.ask_hermes`: `lock.locked()` is `False` between `release()` and a waiter re-acquiring, so a lock with waiters could be evicted and a third turn would mint a fresh lock — two turns concurrently on a single-writer session | Replaced with `_KeyedLocks`, which refcounts holders *and* waiters and evicts only at zero; regression-tested in `tests/run.py` |
| 2 | **Dockerfile silently broken**, not just vestigial: it never copied `hermes_bridge.py`/`mission_control.py`, so the image fails at import | Prominent "DOES NOT BUILD" header comment; README updated to say so |
| 3 | **DNS-rebinding exposure**: loopback binding was the only boundary, but a hostile page whose hostname resolves to 127.0.0.1 could reach the API cookie-less (`POST /api/say` drives the agent; with `HAL_YOLO=1` that is shell access) | `TrustedHostMiddleware` with `HAL_ALLOWED_HOSTS` (default `localhost,127.0.0.1`); covers HTTP and WebSocket |
| 4 | **TTS head-of-line blocking**: the HTTP `?stream=1` path iterated the sync Piper generator at the client's pace while holding the TTS lock, so one stalled reader delayed every other voice reply | `_stream_turn_response` now uses the async generator (worker thread + queue), holding the lock only at synthesis speed |
| 5 | `MAX_HISTORY_TURNS = 40` actually counted messages (20 turns) | Renamed `MAX_HISTORY_MESSAGES` |
| 6 | `run.sh` was zsh-only (`${0:a:h}`) | Now plain bash, works on macOS and Linux |
| 7 | `requirements.txt` omitted the ACP library and the reflection loop's `openai` | Documented as commented optional lines (they normally come from the Hermes venv) |

Cookie-auth paths were checked and are fine as-is: `SameSite=lax` covers
CSRF for the POST endpoints, the WebSocket requires the session cookie, and
session ids are regex-validated before becoming file paths (tested).

## Known minor issues — fixed in the follow-up series

All four were closed by the "serialize history, socket speech, and STT"
change:

- **History read-modify-write race** — per-session history locks
  (`KeyedLocks`) now guard every load-append-save, including the
  mission-trigger path that skipped the inference lock entirely.
- **WebSocket send interleaving** — per-session speech locks serialize
  reply/announcement TTS on a socket, so PCM frames can't interleave.
- **STT serialization** — a lock keeps concurrent whisper decodes (HTTP +
  WS + interim transcripts) from doubling each other's latency.
- **Mid-sentence truncation** — `speakable()` truncates at a sentence (or
  word) boundary via `_truncate_speech`.

## Second pass: improvements and upgrades — implemented

Everything below shipped in the same series as this revision. Status notes
mark the deliberate scope choices.

### The three highest-value gaps ✓

1. **Mission results reach HAL's brain.** Completed/failed mission reports
   queue as system notes injected into the owner's next Hermes prompt
   (`MissionManager._notes` / `drain_notes`), so "what did you find?" is
   answerable. Mission prompts also carry the recent conversation, and the
   completion announcement speaks a sentence-truncated result summary.
2. **Interactive permission approvals.** `HAL_PERMISSION_MODE=ask` sits
   between `deny` and `yolo`: the ACP `request_permission` callback
   publishes a `permission_request` event, HAL asks aloud over the live
   socket, and the turn waits on a future resolved by the UI's Allow/Deny
   bar (`POST /api/permission/{id}`) or a spoken/typed yes-or-no
   (intercepted in `run_turn_text`), with deny-on-timeout
   (`HAL_PERMISSION_TIMEOUT`, 30s). WebSocket turns now run as background
   tasks so a spoken answer can arrive while the asking turn is blocked —
   per-session inference/history/speech locks provide the real
   serialization. Ownership checks stop one browser session from approving
   another's tools.
3. **Missions API + UI panel.** `GET /api/missions` plus a Missions panel
   on the desktop Bridge: live cards with status dot, elapsed time,
   per-mission tool counts (tool events are tagged `mission_session` when
   published through an alias), and click-to-expand results.

### Discovery One leftovers ✓ (with scope notes)

- **Wake word** — implemented as server-side gating on the existing
  duplex path (Duplex: OFF → ON → WAKE): local VAD segments speech, the
  transcript must match "HAL, …"/"Hey HAL …" or the utterance is silently
  dropped; a bare "HAL." earns a spoken "Yes, Dave?". *Scope note:* the
  plan's client-side WASM keyword spotter was deliberately skipped — the
  server is local, so audio never leaves the machine either way, and
  transcript-level gating needs no new dependency.
- **Interim transcripts** — while a duplex utterance records, the buffered
  audio is re-transcribed every ~3s and captioned live. *Scope note:* true
  incremental streaming STT isn't something faster-whisper offers; this is
  caption polish at ~zero risk, flagged honestly as such (`HAL_INTERIM_STT`).
- **HAL-initiated triggers** — `data/triggers.json` supports interval
  (`every_minutes`) and filesystem-watch (`watch` glob) triggers; a
  scheduler loop opens missions, which report over the duplex channel to
  every connected session. The file is re-read each scan, so edits apply
  live.
- **Mission guardrails** — `HAL_MAX_ACTIVE_MISSIONS` (default 3) caps
  concurrent missions per session; HAL declines in-register at the cap.
- **Multi-step missions** — *deliberately not* built as the plan's
  `MissionStep` chain: Hermes is already agentic within a single prompt
  (multi-step tool use included), so step-chaining would duplicate the
  agent's own planning. Progress visibility comes from live tool telemetry
  on the mission cards instead.

### Smaller improvements ✓

- Bridge mission-log DOM capped at 500 entries; telemetry/mission polling
  pauses while the tab is hidden or the panels aren't rendered.
- Terminal tool/permission/mission events journal per session
  (`data/sessions/*.events.jsonl`, bounded), served via `/api/history`, and
  interleaved into the Bridge log on reload.
- `bin/hal` launcher lives in the repo (symlink to `~/.local/bin/hal`).
- CI: GitHub Actions runs `ruff check .` + `tests/run.py` on every push;
  `pyproject.toml` pins the ruff config.
- Reflection loop: failed missions journal to `data/missions/failed.jsonl`,
  ready for `reflection_loop.py --transcript`. Retargeting the loop's
  transcript defaults to Hermes' own store remains open — it needs
  knowledge of Hermes' transcript format that this repo doesn't have.

### Still open (honest list)

- Voice yes/no for permissions arrives over whichever transport is free;
  in duplex the VAD only reopens the mic once HAL finishes asking — if you
  answer over him, use barge-in or the buttons.
- Trigger state (`next_run`, watch baselines) is in-memory: a restart
  re-arms intervals and re-baselines watchers rather than firing missed
  runs.
- The interim-transcript pass re-transcribes the whole buffered utterance
  each time — fine for spoken turns, quadratic for minute-long dictation.
