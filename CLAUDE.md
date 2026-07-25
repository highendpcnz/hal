# HAL 9000 frontend — Claude Code handoff

Read `AGENTS.md` before changing anything. It is the authoritative HAL persona and
runtime guidance; preserve it. Replies from the running bridge are spoken aloud, so
keep user-facing prose concise and address the user as Dave.

## Workspace

The repo root is whichever directory contains `run.sh` — **do not hardcode a
checkout path here**; the previous revision of this file named a machine that no
longer exists and misled every agent that read it.

This is a local HAL 9000 voice frontend for Hermes Agent. The Python bridge is the
source of truth for sessions, missions, permissions, chess, telemetry, viewscreen
events, STT, and TTS. The browser surface is `static/index.html`; direction
stylesheets are `static/bridge-option{1..4}.css` over `static/bridge-shared.css`; the
procedural Three.js optic is authored in `frontend/*.ts` and emitted to
`static/assets/` by Vite.

## Run and verify

The app does **not** run inside the Hermes venv on every machine — that venv is
missing `faster_whisper`/`piper` on some installs, and `run.sh` auto-detects it
first anyway. Prefer an isolated venv and override the three detection knobs:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt agent-client-protocol sherpa-onnx
python3 download_model.py     # models/hal.onnx (61 MB, gitignored) — run.sh falls back to it

HAL_HERMES_VENV="$PWD/.venv" \
HAL_HERMES_BIN="$HOME/.hermes/hermes-agent/venv/bin/hermes" \
HAL_HERMES_ACP_BIN="$HOME/.hermes/hermes-agent/venv/bin/hermes-acp" \
  ./run.sh
```

`hermes-acp` is spawned as a subprocess, so only that binary needs to exist outside
this venv; the ACP *client library* must be installed inside it. On Linux
`espeak-ng` is **not** a required system package — the `piper-tts` wheel bundles it.

Then:

```sh
curl -fsS http://127.0.0.1:8000/api/health     # want status: operational, bridge alive
.venv/bin/python tests/run.py                  # zero-dep, seconds, "all tests passed"
.venv/bin/ruff check .
npm run check                                  # tsc --noEmit && vite build
```

`vite build` output is committed under `static/assets/` and is currently
byte-identical to a fresh build — keep it that way; a drifting bundle is a silent
bug. Keep the bridge on loopback unless the host allowlist and an authentication
story are deliberately addressed.

## Verifying visual work

`google-chrome --headless --screenshot` **hangs and never writes the file** — the
page holds an SSE stream and a WebSocket open, so it never reaches network-idle. No
flag combination fixes this. Use the claude-in-chrome MCP; WebGL renders correctly
there. Screenshot-based design QA is mandatory for shell/optic changes: the mission
log bug below was invisible in source and obvious on sight.

## Current product state

Four visual directions are implemented and selectable from the rail: 01 "Aperture
Sentinel" (default), 02 "Cognitive Orrery", 03 "Signal Vault"
(`docs/plans/directions-2-3.md`), 04 "Ember Chorus"
(`docs/plans/2026-07-19-ember-chorus-design.md`). QA records: `design-qa*.md`.
Direction-independent base/mobile styles live in `static/bridge-shared.css`; each
direction owns its desktop/tablet shell stylesheet.

Selection persists in `localStorage` (`hal_direction`); a pre-paint inline script in
`static/index.html` picks the stylesheet and `frontend/hal-optic.ts` dynamically
imports the scene module. The scene contract is `frontend/optic-api.ts`;
`tests/run.py` pins the cross-file invariants.

## Known defects

- **Bridge mission log wraps role-tagged entries to a one-word column** (direction
  01, desktop). Both `You` and `HAL` entries render their timestamp and role tag on
  one row, then drop the message body to the panel's left edge at the timestamp
  column's width. Untagged system lines render correctly. Origin is the
  `appendToMissionLog` markup in `static/index.html` against the grid/flex rules the
  direction stylesheet inherits from the legacy inline block. Reproduce by loading
  the desktop Bridge and sending any turn.
- `docs/ANALYSIS.md` is a stale point-in-time review: it predates the ledger,
  voiceprints, chess, the viewscreen, and the whole directions system, describes
  two-mode permissions (there are three), and lists trigger-state persistence as
  open when it shipped. Treat the README as authoritative where they disagree.

## Known risks

`static/index.html` is ~3,000 lines: ~1,100 of legacy inline CSS (which still owns
the tablet and desktop media blocks every direction stylesheet must reset against)
and ~1,600 of untyped inline JS carrying the whole behavior layer — WS protocol, PCM
playback, VAD, permission bar, chess, missions — pinned only by substring assertions
in `tests/run.py`. Treat edits there as unpinned until you have looked at the result.

The frontend bundle is intentionally Three.js-heavy; check mobile GPU and
reduced-motion behavior when adding effects. The bridge has no authentication and
defaults to denying tool permissions. Treat `data/` as runtime state, and inspect
`git status` before changing or deleting generated artifacts. Do not add cloud API
keys or commit secrets.
