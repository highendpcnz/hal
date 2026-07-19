# Option 4 Design QA — Ember Chorus

Source reference: `docs/plans/2026-07-19-ember-chorus-design.md` (this
direction is specified by its causes, not a concept PNG).

Implementation surfaces: `static/index.html` (shared skeleton),
`static/bridge-shared.css` (base + mobile), `static/bridge-option4.css`
(summoned-void shell), `frontend/optic-chorus.ts` (stateless particle
scene), `frontend/directions.ts` (manifest, `ready: true`).

QA artifacts (runtime, on the viewscreen):
`data/viewscreen/option4-chorus-eye.jpeg` (the condensation eye while
speaking), `data/viewscreen/option4-chorus-denied.jpeg` (the denied
freeze-scatter).

## Method note

This direction's QA ran under **Playwright** rather than the in-app
browser pane: the pane reports `document.hidden`, which pauses
`requestAnimationFrame`, and this direction's signature moments are
*animations* (condensation eases over ~1.2s of real frames). Playwright
runs a real headed browser where rAF ticks, so state sequences were
exercised with real frame time. One trap worth recording: the static
mount serves with heuristic caching, so a rebuilt bundle can be
screenshot-tested stale — refresh with `fetch(url, {cache: "reload"})`
before reloading, or verify the served file contains the change.

## Verified

- Desktop 1440 × 1024, idle: the murmuration fills the void —
  ember-red particle field, denser toward the middle, hairline top bar
  (brand · nav · direction selector), whisper log fading top-left,
  status line and docked command bar bottom. A quiet session is visibly
  calm: no idle choreography.
- **Condensation** (the direction's signature), via the real `#eye`
  class contract with sustained `setAudioEnergy` feed: on `speaking`
  the swarm folds into a granular red iris with a white-hot core and
  halo (see the viewscreen artifact); on speech end it dissolves back
  to the field (label returns to Standby); `denied` freezes, chills to
  gray, and scatters wide — HAL refusing to take shape — and never
  coheres.
- Round-trip 04 → 01 → 04 through the rail buttons: each direction
  boots its own scene (`webgl-ready` true), selection persists.
- Tablet 900 × 900: same void, nav and four selector dots fit, field
  at the 24k tier.
- Mobile 390 × 844: shared card flow; the swarm renders inside the eye
  card at the 10k/portrait tier (tightened spread), four dots fit the
  mobile rail. No overflow.
- Console: zero errors across the full session (only the pre-existing
  deprecated `apple-mobile-web-app-capable` meta warning, which
  predates the directions work).
- `npm run check`, `tests/run.py` (manifest loop auto-extended to four
  directions), `ruff` — green.

## Iteration history

- P1, shader: `active` is a reserved word in GLSL ES — the vertex
  shader failed to compile and the canvas rendered white (a NaN-free
  but program-less state). Renamed to `orbitActive`.
- P1, energy: condensation multiplied per-pixel additive density ~50×
  and the eye blew out to a white sun. Fixed with energy conservation —
  per-sprite alpha and point size scale down with coherence
  (`mix(1.0, 0.34, uCoherence)` alpha, ~0.6× size) plus a lower
  speaking brightness/bloom profile.
- P2, palette: the field read dusty-tan and the eye amber — the hot
  ramp applied linearly everywhere. Heat now ramps quadratically and
  mostly under coherence: the drifting field stays ember-red, white
  belongs to the core alone.
- P2, buffer layout: the mission-satellite reserve originally sat at
  the buffer tail, outside every draw range; moved to the head so
  satellites draw at all tiers (inactive groups fold into the flock in
  the shader).

## Honest limitations

- The causal ledger's history/latency inputs are DOM reads of what the
  bridge already renders; if those elements are ever renamed, the swarm
  quietly loses those inputs (reads are defensive, defaulting to zero).
- Mission satellites and tool-pulse fronts were verified by code path
  and shader review, not live missions — exercising a real mission's
  satellite is a natural first-live-session check.
- The `:has()` gate hiding the empty missions card requires a 2023+
  browser; older engines show a dim empty card instead.

final result: passed
