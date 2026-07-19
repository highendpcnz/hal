# Directions 02 & 03 — scope

Scope for building the two remaining visual directions behind the existing
direction-selector contract: **02 Cognitive Orrery** and **03 Signal Vault**.
Concept references: `data/viewscreen/hal-concept-2.png` and
`data/viewscreen/hal-concept-3.png`. Constraint from the handoff notes
(CLAUDE.md): each direction is an independent visual system — its own
composition, scene, and layout — **not** a recolor of Option 1.

## Where the contract stands today

What exists:

- `frontend/directions.ts` — the manifest registry (`id`, `label`,
  `shortLabel`, `ready`) plus `ACTIVE_DIRECTION`, a **build-time constant**.
- `setupDirectionSelector()` in `frontend/hal-optic.ts` stamps
  `<html data-bridge-direction>`, fills the three rail buttons from the
  manifest, and disables the not-ready ones. It attaches **no click
  handlers** — runtime switching does not exist yet.
- `window.HALOptic` — the behavior layer's only handle on the visual scene:
  `destroy() / setAudioEnergy(energy) / setState(state) / setToolKind(kind)`,
  with states `idle | listening | thinking | speaking | denied` and tool
  kinds `fetch | execute | search | read | null`. Any new scene implements
  exactly this interface; the inline JS in `static/index.html` never needs
  to know which direction is live.
- A flat, ID-stable panel skeleton in `static/index.html` (`.bridge-left`,
  `#mission-log`, `#telemetry`, `.bridge-input`, `.bridge-rail`, `#permbar`,
  `#missions-panel`, `#chess-panel`, `#viewscreen-panel`, drawer, overlays)
  laid out by CSS grid in `static/bridge-option1.css`. `tests/run.py`
  asserts this wiring — the IDs are contract.
- One statically linked stylesheet and one Vite lib-mode bundle
  (`static/assets/hal-optic.js`, single entry).

Known small defects to clean up while in here: the hardcoded rail
`aria-label`s say "Obsidian Orrery" / "Monolith Vault" while the manifests
say "Cognitive Orrery" / "Signal Vault" (concept 2's own header reads
COGNITIVE ORRERY — the manifests win); `.bridge-brand-title` hardcodes
"Aperture Sentinel" instead of reading the active manifest.

## Phase 0 — direction runtime (prerequisite, shared)

Turn "a direction" into a first-class runtime concept. No visual changes.

1. **Switching.** Click handlers on `.direction-option`: persist the choice
   (`localStorage`, key `hal_direction`), stamp `data-bridge-direction`,
   update `aria-pressed` and `.bridge-brand-title`/`.bridge-mark` from the
   manifest. `ACTIVE_DIRECTION` becomes the *default*, not the selection.
   Switching may simply `location.reload()` after persisting — the server
   holds all conversational state, a reload costs ~nothing, and it avoids a
   whole class of live-teardown bugs (WS reconnect logic already exists and
   is tested). Live swap without reload is a later nicety, enabled by
   `HALOptic.destroy()`.
2. **Per-direction CSS.** One stylesheet per direction
   (`bridge-option1.css` stays; add `bridge-option2.css`,
   `bridge-option3.css`), all linked with `disabled` toggled at boot before
   first paint (tiny inline head script reading `localStorage`) to avoid a
   flash of the wrong shell. Shared tokens (colors, fonts, z-index scale)
   extracted to `bridge-shared.css` only where genuinely common — resist
   premature extraction; directions are allowed to diverge.
3. **Scene modules + code splitting.** Split `hal-optic.ts` into a shared
   core (renderer/composer setup, resize + aspect-aware camera, state-lerp
   machinery, reduced-motion and WebGL-fallback handling, the
   `HalOpticApi` plumbing) and per-direction scene modules
   (`optic-aperture.ts`, `optic-orrery.ts`, `optic-vault.ts`) loaded via
   dynamic `import()` so each direction fetches only its own chunk. Vite
   moves from lib mode to a normal build with a single entry + code
   splitting (`three` stays in the shared chunk — it dominates the 667 KB
   and is common to all three).
4. **Fixes.** Stale aria-labels; brand title from manifest; extend
   `tests/run.py` protocol checks (selector persists, all three
   stylesheets referenced, manifests consistent with the rail).

Exit criteria: direction 01 looks and behaves exactly as today (design-qa.md
still passes), buttons 02/03 remain disabled, `npm run check` and
`tests/run.py` green.

## Phase 1 — Option 02 "Cognitive Orrery"

**Read of the concept:** an instrument, not a corridor. Dead-center optic
inside a 3D orrery — concentric glass/bronze calibration rings with
satellite lens discs orbiting on the horizontal axis — and a live waveform
beam running the full midline of the viewport through the eye. Symmetric
chrome: mission log as a dotted timeline rail on the left, a card stack on
the right (Active Mission, Permission with prominent AUTHORIZE/DENY,
Permissions level), tab-style nav along the top, full-width command bar +
status strip (VOICE LINK / THINKING / SYSTEMS) at the bottom. Warmer
material palette than 01 (brass/glass against carbon), brighter core.

Work:

- **Scene** (`optic-orrery.ts`): center eye reusing 01's iris/core shader
  work where it fits; ring systems with independent orbital speeds;
  satellite lens groups left and right; the midline beam as a
  camera-facing ribbon whose amplitude is driven by `setAudioEnergy` (this
  is the direction's signature — the existing 2D `#waveform` canvas hides
  on desktop in this direction). State mapping: listening = beam gain +
  core bloom; thinking = orbital acceleration; speaking = beam pulse from
  TTS energy; denied = ring stall + dim. Pointer parallax as in 01.
- **Layout** (`bridge-option2.css`): 3-column desktop grid (timeline log /
  stage / card column) scoped under
  `html[data-bridge-direction="orrery"]`; the right-column cards restyle
  the *existing* `#permbar`, `#propbar`, mission cards and telemetry data —
  new wrappers may be added to the shared skeleton only if hidden in other
  directions and additive (no ID moves). Top nav restyles `.rail-actions`.
- **Responsive:** tablet drops to 2 columns (cards fold under the log);
  mobile keeps the existing eye-first mobile flow (mobile is shared across
  directions below 761px — directions are a desktop/tablet concern, same
  as today).
- **QA:** a `design-qa-option2.md` pass mirroring the option-1 protocol
  (1440×1024 / 900×900 / 390×844, all five visual states, reduced motion,
  no overflow, comparison shots against `hal-concept-2.png` into
  `data/viewscreen/`).

Sizing: **medium**. The scene is a recomposition of familiar elements
(rings, discs, bloom, one new beam ribbon); the layout is a grid exercise
over existing panels.

## Phase 2 — Option 03 "Signal Vault"

**Read of the concept:** a film still. The optic is embedded in an angular
black vault wall seen in three-quarter perspective on the right, raking a
red beam across the frame; the hero element is *typography* — the live
transcript rendered as huge monospace type on the left ("I'M SORRY,
DAVE.") with a cursor. Compact timeline log top-left; a segmented telemetry
console band above the command bar (Voice Energy waveform / Executing +
progress / Mission Progress / Permission with hex lock); "hold anywhere on
the optic to speak" with a corner-bracket reticle — a much larger hit
target than the eye alone.

Work:

- **Scene** (`optic-vault.ts`): the hard one. Corridor/vault environment
  (angular extruded panels, PBR normal-mapped surfaces — geometry +
  materials, no photo textures), optic assembly recessed in the wall,
  perspective camera with subtle pointer drift, beam as emissive geometry +
  bloom (no true volumetrics — cost). State mapping: listening = beam
  sweep toward camera + core flare; thinking = beam scan cycling;
  speaking = flare pulses on TTS energy; denied = beam cut, embers.
  **Perf tiers required:** desktop full; tablet reduced (no env reflections,
  capped DPR); mobile and `prefers-reduced-motion` get a static-camera
  simplified scene or the CSS fallback — decide by measurement on a real
  phone, budget in scope.
- **Type-first transcript layer:** the direction's second signature. The
  live caption/transcript data already flows (`#caption`, mission log
  entries, WS `transcript` frames); this direction promotes the current
  HAL utterance into the hero type block with the type ramp doing the
  hierarchy. Needs care with long replies (clamp + fade), interim duplex
  captions, and `aria-live` not double-announcing.
- **Enlarged speak target:** the whole optic stage becomes the
  push-to-talk surface in this direction (pointer handlers already live on
  `#eye`; extend the hit region via a direction-scoped overlay that
  forwards to the same handlers).
- **Layout** (`bridge-option3.css`): asymmetric desktop grid (type column /
  stage right), segmented console band restyling `#telemetry` + the
  waveform canvas + mission progress out of existing data, command bar at
  the bottom edge.
- **Responsive + QA:** as Phase 1, against `hal-concept-3.png`, plus an
  explicit mobile-GPU check (CLAUDE.md flags this risk) and a
  reduced-motion pass.

Sizing: **large** — the environment scene, the perf tiers, and the
typographic layer are each real work; none reuse much of 01/02.

## Sequencing and risks

Order: Phase 0 → 02 → 03. The orrery derisks the switching machinery on
the simpler scene; the vault builds on a proven runtime and inherits the
perf-tier patterns 02 shakes out.

| Risk | Mitigation |
|---|---|
| Bundle growth (three scenes) | Code splitting in Phase 0; `three` shared; budget: no direction's chunk beyond ~150 KB gzip on top of shared |
| Mobile GPU on the vault scene | Perf tiers + measured fallback; reduced-motion path mandatory |
| Behavior regressions while re-styling live panels | IDs never move; `tests/run.py` wiring checks extended per direction; design-qa protocol re-run on 01 after Phase 0 |
| Scope creep into "themes" | Each phase ships a distinct scene module + distinct grid — the acceptance test is the concept comparison, not a palette diff |

Non-goals: no backend changes (all three directions speak to the same
endpoints and `HALOptic` contract), no auth work, no persona (AGENTS.md)
changes, no mobile redesign below 761px.
