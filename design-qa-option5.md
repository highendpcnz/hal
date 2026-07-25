# Option 5 Design QA — Logic Memory Center

Source reference: `docs/plans/2026-07-26-logic-memory-center-design.md` (this
direction is specified by its causes; no concept PNG exists).

Implementation surfaces: `static/index.html` (shared skeleton, direction
link/button/ready-list, `resetSession` hook), `static/bridge-shared.css`
(base + mobile), `static/bridge-option5.css` (chamber shell),
`frontend/optic-lmc.ts` (instanced rack scene), `frontend/optic-api.ts`
(optional `playSessionEnd`), `frontend/directions.ts` (manifest), and the
generated `static/assets/optic-lmc.js`.

## Method note

Checked against a live server with a real session (6 log entries) rather
than fixtures, through the claude-in-chrome MCP. Headless Chrome's CLI
`--screenshot` does not work for this app — the page holds an SSE stream
and a WebSocket open and never reaches network-idle.

**Stylesheets and scene bundles cache aggressively here.** Every visual
check below was made after an explicit cache-bust; a plain reload served
stale assets and made a correct fix look like a no-op. This is recorded in
`CLAUDE.md` because it will recur.

## Verified

- **Concept reads.** The chamber encloses: racks recede to a vanishing
  point, the nearest rings leave the frustum and become the walls. It is
  not a box viewed from outside.
- **Cold/hot opposition holds.** Blocks render pale cyan-white against a
  low red ambient. Red never lights a block.
- **Population is honest.** A 6-entry session lights a small cluster at the
  far end of an otherwise dim chamber. Empty sockets remain visible as
  architecture, so a new session reads as *sparse*, not as *broken*.
- **Socket addresses.** Log entries carry `M-01`…`M-06` from a CSS counter,
  matching seat order in the rack.
- **Extraction.** `playSessionEnd()` exists on `window.HALOptic`, returns a
  Promise, and resolves in ~2505ms against its 2500ms budget. Captured
  mid-flight: blocks unseat from both walls and the ceiling and stream past
  the camera while the far end empties.
- **Contract is additive.** `playSessionEnd` is optional; directions 01–04
  do not implement it. Round-tripped to direction 01 afterwards — the shell,
  the optic, and the mission-log fix from `b98ce6d` are all intact.
- **Legacy chrome reset.** The fixed Systems pill (`.monitor-tab`) landed on
  top of the direction selector until 05 reset it, the same way 04 does.
- **Suite.** `ruff` clean, `npm run check` passes, and the manifest-driven
  contract loop auto-extended to generate 05's link/button/loader/stylesheet
  assertions without being told to.

## Iteration history

Four rounds, all caught by looking rather than by reasoning:

1. **Red wash killed the thesis.** The clear-colour lerp ran to 0.25 + 0.5×voice
   and turned the whole frame red-brown; the cold blocks read as mauve. Cut
   to 0.07 + 0.16×voice.
2. **Looking at a box, not standing in one.** Camera sat outside the racks.
   Moved it inside, widened the chamber (`HALF_W` 1.55 → 2.35) so the walls
   exit the frustum.
3. **Blown out to white.** Additive blending — correct for 04's emissive
   particles — accumulated across every overlapping slab along the corridor.
   Switched to normal blending, raised the bloom threshold to 0.82. This was
   a materials mistake, not a tuning one.
4. **Occupancy read as a progress bar.** Seats were ordered surface-major,
   so a sparse session lit one wall end-to-end and left the rest black.
   Reordered depth-major, then flipped so `d=0` is the *far* end: memory now
   extends away from you and grows toward you, and newest-first extraction
   starts at the blocks nearest the camera.

## Honest limitations

- **Timestamps are hidden in this direction.** Position in memory is the
  organizing principle here, and showing both address and time in a 306px
  strip is noise. Directions 01–04 still show timestamps, and `/api/history`
  always has them — but this is a deliberate loss, not an oversight.
- **Only desktop was checked visually.** The `min-width: 761px` block is
  written to cover tablet as well, and mobile falls through to the shared
  card flow, but neither was inspected at its own viewport.
- **Not exercised against real state:** `thinking` sequential access,
  `denied`, tool-kind column flares, and mission satellites are implemented
  and wired to the same DOM reads 04 uses, but were not driven through a
  live turn during this QA pass. The extraction and the idle/population
  behaviour were.
- **Extraction was invoked directly**, not through the New Session button —
  that path fires a `confirm()` dialog, which blocks the automation
  session. The `resetSession` wiring is pinned by assertions in
  `tests/run.py` instead; an end-to-end Playwright test is the right next
  step now that the plugin is installed.
- **Reduced motion** is implemented (no drift, no travel, extraction fades
  in place) but was not verified under an actual `prefers-reduced-motion`
  media state.
