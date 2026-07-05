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

## Known minor issues (documented, not yet fixed)

Deliberately left alone to keep the fix series small; all are edge cases:

- **History read-modify-write race.** `run_turn_text`'s load-append-save
  runs outside the per-session inference lock, and mission-trigger turns
  skip that lock entirely — two overlapping turns on one session can drop a
  transcript entry. Worst case is a missing scrollback line; Hermes' own
  memory is unaffected. Fix would be a per-session history lock (reusing
  `_KeyedLocks`).
- **WebSocket send interleaving.** A mission completion announcement can
  fire while a normal reply is still streaming PCM on the same socket;
  concurrent `send` calls can interleave frames and garble playback. Fix
  would be a per-session outbound speech queue.
- **No STT serialization.** Two simultaneous transcriptions (HTTP + WS)
  are safe but thrash the CPU; a lock or small semaphore would smooth
  worst-case latency.
- **`speakable()` truncates mid-sentence** at 1,500 chars; truncating at
  the last sentence boundary would sound better.

## Second pass: improvements and upgrades

### Highest-value gaps

1. **Mission results never reach HAL's brain.** A mission runs in its own
   Hermes session, which is dropped on completion; the result lives only in
   `data/missions/*.json` and one spoken line ("I'm ready to review the
   results with you"). Ask "so what did you find?" and the main session's
   Hermes has never heard of the mission. Feeding `mission.result` back into
   the owning session (as a context turn on completion, or lazily on the
   next prompt) would make the mission feature feel finished.
2. **Interactive permission approvals.** `HAL_YOLO` is all-or-nothing.
   The ACP `request_permission` callback plus the existing SSE/WS plumbing
   is everything needed for HAL to *ask*: "Dave, may I run `git push`?" —
   click or voice to approve, deny on timeout. This would replace the
   binary trade-off (safe-but-toolless vs. unattended shell access) with
   the interaction the HAL premise begs for.
3. **Missions API + UI panel.** `MissionManager.list_missions` exists but
   no endpoint exposes it; missions are only visible as transient SSE lines.
   A `GET /api/missions` plus the "Active Missions" cards from the Discovery
   One plan (status, elapsed time, result review) is mostly plumbing.

### Feature upgrades (Discovery One leftovers)

- **Wake word** ("HAL…") client-side — the plan's Pillar 2 leftover;
  energy-VAD already exists, a keyword spotter (e.g. openWakeWord WASM)
  would complete always-on mode.
- **Streaming STT partial transcripts** over the WebSocket for live
  captions while you speak.
- **Multi-step missions with progress** (the plan's `MissionStep` design)
  and richer mission prompts — currently a mission is one bare
  `"Execute mission: <title>"` prompt with no conversational context.
- **HAL-initiated triggers**: cron schedules and filesystem watchers that
  open missions and report over the duplex channel — the "HAL speaks
  first" infrastructure already works (mission completions prove it).
- **Mission guardrails**: cap concurrent missions per session and consider
  confirming voice-triggered missions — a misheard utterance currently
  spawns an agent run silently.

### Smaller improvements

- Cap the Bridge mission-log DOM (it grows unboundedly in long-lived tabs);
  stop the 30s telemetry poll while the bar is hidden (mobile).
- Persist tool-call log entries into history so the Bridge log survives
  reload, not just transcripts.
- Bring the `hal` launcher script (README references `~/.local/bin/hal`)
  into the repo, e.g. `bin/hal`.
- CI: the zero-dependency test suite is ideal for a tiny GitHub Actions
  workflow (`pip install -r requirements.txt`-lite + `python tests/run.py`).
- Packaging/lint: a `pyproject.toml` with ruff config matching the current
  style would keep contributions consistent.
- Reflection loop: retarget from the Antigravity CLI to Hermes transcripts
  (its docstring already flags this), and consider running it after failed
  missions — that's where the signal is.
