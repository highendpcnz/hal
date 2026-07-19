# HAL 9000 frontend — Claude Code handoff

Read `AGENTS.md` before changing anything. It is the authoritative HAL persona and runtime guidance; preserve it. Replies from the running bridge are spoken aloud, so keep user-facing prose concise and address the user as Dave.

## Workspace

This checkout is `/Users/hal-9000/Documents/Codex/hal/hal-main`. It is a local HAL 9000 voice frontend for Hermes Agent. The Python bridge is the source of truth for sessions, missions, permissions, chess, telemetry, viewscreen events, STT, and TTS. The browser surface is in `static/index.html`; the current visual shell is `static/bridge-option1.css`; the procedural Three.js optic is authored in `frontend/hal-optic.ts` and `frontend/directions.ts` and emitted to `static/assets/` by Vite.

## Run and verify

From this directory:

```sh
./run.sh
curl -fsS http://127.0.0.1:8000/api/health
npm run check
npm audit --audit-level=high
HAL_SKIP_MODELS=1 /Users/hal-9000/.hermes/hermes-agent/venv/bin/python tests/run.py
```

`run.sh` auto-detects `/Users/hal-9000/.hermes/hermes-agent/venv`, with `HAL_HERMES_VENV` available as an override. Keep the bridge on loopback unless the host allowlist and authentication story are deliberately addressed. `HAL_SKIP_MODELS=1` is for fast tests; a live health response should report `status: operational` and an alive ACP bridge.

## Current product state

All three visual directions are implemented and selectable from the rail: 01 “Aperture Sentinel”, 02 “Cognitive Orrery”, 03 “Signal Vault” (`docs/plans/directions-2-3.md` is fully delivered). QA records: `design-qa.md`, `design-qa-option2.md`, `design-qa-option3.md`. Direction-independent base/mobile styles live in `static/bridge-shared.css`; each direction owns its desktop/tablet shell stylesheet. Note: `static/index.html`'s inline styles still contain the legacy pre-directions tablet/desktop blocks — direction stylesheets must reset against them (see the option 2 QA iteration history).

The direction runtime (Phase 0 of `docs/plans/directions-2-3.md`) is in place: selection persists in `localStorage` (`hal_direction`), a pre-paint inline script in `static/index.html` picks the direction stylesheet, and `frontend/hal-optic.ts` boots the direction’s scene module via dynamic import (`frontend/optic-aperture.ts` today; `optic-orrery.ts`/`optic-vault.ts` are stubs). The scene contract is `frontend/optic-api.ts`; `tests/run.py` pins the cross-file invariants.

Visual QA artifacts are in `data/viewscreen/`; `design-qa.md` records the comparison and interaction checks. Preserve existing behavior while iterating on the visual shell.

## Next priorities

Develop options 2 and 3 as separate visual systems behind the existing direction-selector contract. Reuse the bridge behavior and endpoints, but do not collapse the directions into color-only themes. Before any handoff, run the checks above and inspect desktop, tablet, and mobile layouts. Do not add cloud API keys or commit secrets.

## Known risks

The frontend bundle is intentionally Three.js-heavy; check mobile GPU behavior and reduced-motion behavior when adding effects. The bridge has no authentication and defaults to denying tool permissions. Treat `data/` as runtime state, and inspect `git status` before changing or deleting generated artifacts.
