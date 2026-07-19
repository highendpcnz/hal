# Direction 04 "Ember Chorus" — design

*Validated 2026-07-19 through the brainstorming dialogue. This document is
the direction's visual reference — no concept PNG exists; the sculpture is
specified by its causes, not a picture.*

## Concept

HAL without a face — a mind made visible as weather. Directions 01–03 all
render HAL as an object you face (a lens, an instrument, an architecture).
04 renders him as a **population**: tens of thousands of ember-red
particles in black, behaving like a starling murmuration.

Three defining beats:

1. **The swarm is HAL's presence.** Idle, it only breathes. Listening, it
   leans toward you and tightens. Thinking, it winds into vortices.
   Denied, it flash-freezes, chills, and scatters.
2. **The eye exists only when HAL coheres.** On real speech the swarm
   condenses into a recognizable red iris with a hot core, holds formation
   while the voice lasts, and dissolves back into signal when it ends.
   Barge-in shatters it instantly. Denial never condenses.
3. **Causal honesty.** Every visible change has a real cause. The medium
   is flow-field motion (always alive, never dead), but nothing *fires*
   decoratively: no idle choreography clock, no fake events. A quiet
   session is visibly, honestly calm.

Pillars, as decided: signal sculpture · murmuration · causal honesty ·
summoned void.

## The engine (frontend/optic-chorus.ts)

**Stateless GPU particles.** One `THREE.Points` draw; each particle
carries only static attributes (random `seed`, a pre-assigned `formation`
seat on the iris disc, a `heat` value, a `group` id for mission
satellites). Position is a pure vertex-shader function
`f(seed, time, uniforms)`: a layered-trig flow field advects the drift,
and state uniforms bend it.

Why stateless (vs. ping-pong GPGPU): nothing can diverge or NaN over an
hours-long session; condensation is `mix(flowPath, seat, uCoherence)` —
one eased uniform folds the swarm into the eye and back; reduced motion is
"stop advancing flow time"; perf tiers are a draw-range number.

The TypeScript class (`ChorusOptic`) mirrors the other scenes' skeleton:
same `HalOpticApi` + `#eye`-class contract, same damped-uniform animate
loop, same composer stack with low bloom (additive sprites carry the
glow), same destroy/teardown.

## The causal ledger

| Real signal | Behavior |
|---|---|
| Live voice energy (mic / TTS) | Turbulence amplitude + particle brightness |
| `listening` | Swarm leans toward camera, radius tightens |
| `thinking` | Vortex currents |
| `speaking` | Coherence → condensation eye; cadence pulses the core |
| `denied` | Freeze, chill to gray-red, cold scatter; never coheres |
| Tool call (kind) | One pulse-front sweeps the swarm, tinted per kind |
| Active missions (DOM count) | One orbiting satellite sub-swarm per running mission |
| History depth + uptime (boot) | Population (~8k–48k clamp) and drift tempo |
| Turn latency (telemetry DOM) | Color-temperature bias (laggy = cooler) |

Inputs are exactly the existing contract plus defensive 5-second DOM reads
of values the bridge already renders (`#mission-cards`, `#telem-lag`,
`#mlog-entries`). No API changes.

## The condensation eye

Formation seats pre-sampled on an iris disc right of center (~8% in a hot
core), facing the camera. `speaking` eases coherence up over ~1.2s —
particles sweep in along noise-space curves, so the eye assembles out of
weather. TTS energy micro-jitters the seats (the iris shimmers with the
voice). Release sheds outer rings first, core last. Barge-in cuts
coherence in ~0.15s.

## The shell (static/bridge-option4.css) — summoned void

Permanent chrome is two hairlines: a thin top bar (brand · live status ·
nav · direction selector) and the docked command line with the
status/instruction whisper above it. Everything else is summoned by
reality:

- Log entries fade in as borderless whisper-lines left, then decay to
  near-transparency (~20s) via CSS insertion animations — no JS.
- Mission cards drift in top-right only while missions exist
  (`:has(.mission-card)` gate; degrades to a dim panel without `:has`).
- Chess/viewscreen slide in as floating cards on demand; permission and
  proposal reuse the 02/03 card pattern.
- The whole stage is push-to-talk: `#eye` stretches full-bleed between
  the bars ("hold anywhere").
- `#telemetry` is hidden but stays in the DOM (the scene reads it).

One `min-width: 761px` rule block covers desktop and tablet (the layout is
minimal; the direction-scoped selectors out-rank the legacy inline blocks
at any width). Mobile below 761px stays the shared card flow with the
swarm at its lowest tier.

## Performance, failure, tests

- Tiers: ~40k / 24k / 10k particles by surface width, with the vault's
  DPR caps (1.75 / 1.5 / 1.25). One draw call at every tier.
- Reduced motion: flow time freezes (still constellation); the eye forms
  as a crossfade; pulses brighten in place instead of traveling.
- DOM reads are defensive (missing element = zero, never a crash). WebGL
  failure keeps the shared CSS-fallback eye, centered plainly.
- Tests: the manifest-driven contract loop auto-extends; deliberate edits
  are the manifest count assertion (three → four) and the new
  link/button/loader entries it asserts.

## Delivery

Scene → shell → enable (`directions.ts`, inline ready-list) → browser QA
(three viewports, state contract, condensation via the real caption/class
path, round-trip through all four directions) → `design-qa-option4.md` →
commit. Effort comparable to the orrery; the shaders are the new craft.

## Banked axes (not in this build)

Candidate concepts for any future direction, from the same session:
**Inside HAL's mind** (the Logic Memory Center — you float among memory
banks; session reset as "my mind is going"), **HAL's point of view**
(perception rendered as UI), **The ship itself** (Discovery One as live
schematic).
