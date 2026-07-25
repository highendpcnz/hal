# Direction 05 "Logic Memory Center" — design

*Written 2026-07-26 against the axis banked at the end of the Ember Chorus
doc ("Inside HAL's mind"). Like 04, this direction has no concept PNG — it
is specified by its causes.*

## Concept

Directions 01–03 render HAL as an object you face: a lens, an instrument,
an architecture. 04 renders him as weather — a population with no face.
All four put HAL *out there*.

05 puts you **inside**. The stage is the Logic Memory Center: a chamber
whose walls are racked arrays of translucent memory blocks, lit from
within. The camera does not orbit and cannot leave. You are held in place
inside the machine, and the machine is thinking around you.

Three defining beats:

1. **The blocks are the conversation.** Not decoration standing in for
   memory — one seated block per stored exchange, read from the log the
   bridge already renders. A fresh session is a nearly empty chamber, dark
   and sparse. A long thread is dense and luminous. You can see how much
   HAL is holding by looking at the room.
2. **Thinking is visible as access.** On `thinking`, blocks flare in
   sequence — a scan travelling the racks. This is not an idle animation
   borrowed from the other directions; it is the literal thing the state
   means for a computer with a memory bank.
3. **Reset is an extraction.** `POST /api/session/reset` today returns
   JSON and reloads the page. Here it unseats the blocks — newest memory
   first — and drifts them past the camera while the chamber goes dark.
   The destructive action becomes legible: you are watching the memory
   leave.

Pillars: interior · cold memory in a hot mind · addressed storage ·
extraction as consequence.

## The aesthetic risk: cold blocks, hot room

Directions 01–04 are all red-on-black. A fifth red-on-black direction is a
skin, and this repo's own `CLAUDE.md` warns against collapsing directions
into colour-only themes.

So 05 breaks the family palette in exactly one place. The film's memory
blocks are *clear* — pale, internally lit acrylic — in a room washed red.
That opposition is the thesis: **your memories are cold and legible; the
mind holding them is hot.**

| Token | Value | Role |
|---|---|---|
| `--void` | `#050506` | chamber black |
| `--signal` | `#ff2d1f` | HAL red — shared with 01–04, keeps the family |
| `--memory` | `#cfe6ea` | block glow, pale cyan-white — **the departure** |
| `--memory-dim` | `#3f4d52` | empty socket, dormant rack |
| `--warm-wash` | `#2a0906` | red ambient bounce on chamber surfaces |
| `--type` | `#ece7e0` | text, unchanged from the shared shell |

Red never lights a block. Cyan never lights the room. When they do meet —
a denial, an extraction — it reads as a fault, because the two systems are
never otherwise in contact.

No new typefaces. Azeret Mono and IBM Plex Mono are already committed as
woff2 and carry the whole product; adding a fifth family for a fifth
direction is bundle weight with no argument behind it.

## The structural device: socket addresses

Log entries carry a rack address (`A-07`, `B-13`) rather than a decorative
index. This is the one place numbering is honest here — the blocks *are* an
addressed array, the address maps to a real seat in the rack, and the film's
own blocks were labelled. Order carries information the reader needs: it is
where in HAL's memory that exchange physically sits.

## The engine (frontend/optic-lmc.ts)

Instanced blocks, one `THREE.InstancedMesh` draw. Each instance carries a
static seat (rack, column, depth), a `seed`, and a per-instance
`activation` written each frame from a small CPU-side array — history depth
decides how many seats are *occupied*, and occupancy eases rather than
snapping, so a new exchange lights its block rather than popping it in.

Why instancing rather than the 04 approach (stateless GPU particles): the
blocks are discrete, addressable, and few (hundreds, not tens of
thousands). They need to be individually addressable for sequential access
and for the extraction — both of which are per-block choreography, which is
exactly what a stateless position function cannot express.

The class (`LmcOptic`) mirrors the other scenes' skeleton: same
`HalOpticApi` contract, same `#eye`-class hook, same damped-uniform animate
loop, same composer stack, same `destroy()` teardown.

## The causal ledger

| Real signal | Behavior |
|---|---|
| History depth (`#mlog-entries` children) | Occupied seats — the chamber's population |
| `listening` | Racks lean in; a ripple travels inward toward the camera |
| `thinking` | Sequential access — blocks flare in scan order along the racks |
| `speaking` | Chamber ambient pulses with the voice; blocks hold steady and bright |
| `denied` | Blocks darken in a wave; red ambient spikes and goes cold |
| Live voice energy (mic / TTS) | Ambient intensity and block shimmer |
| Tool call (kind) | One rack column flares in the tool's tint |
| Active missions (`#mission-cards`) | One block per mission pulses out of phase, off-rack |
| Turn latency (`#telem-lag`) | Access-scan tempo — laggy reads slower |
| Session reset | **The extraction** (below) |

Inputs are the existing contract plus the same defensive DOM reads 04
already makes. No API changes.

## The signature: extraction

`resetSession()` in `static/index.html` is a clean choke point — confirm,
`POST /api/session/reset`, `location.reload()`. The confirm already carries
the decision; the extraction carries the *consequence*, and plays between
the two.

- Blocks unseat newest-first, drift out of their sockets and past the
  camera. Reverse-chronological is the film's own order.
- The chamber's red ambient drains as the racks empty.
- HAL speaks once, over the live socket if one is open. The film's line is
  earned here and nowhere else — the same discipline the chess engine uses,
  reserving its line for mate.
- Total ~2.5s, not the film's three minutes. Skippable: any key or click
  cuts to the reload. Reduced motion fades blocks in place with no travel.

This needs one signal the scene contract does not carry, so `HalOpticApi`
gains an **optional** method:

```ts
playSessionEnd?: () => Promise<void>;
```

Optional is the whole point: directions 01–04 don't implement it and are
completely unaffected; `resetSession()` awaits it only if present, with a
timeout so a hung scene can never strand the reset. This is the minimum
contract surface that expresses the moment honestly — the alternative,
sniffing the DOM for a cleared log, is racy and lies about intent.

Afterwards the chamber is dark and nearly empty. The instruction line reads
**"Memory clear. Speak, and I'll begin again."** — an empty state that
tells you what to do, not one that performs a mood.

## The shell (static/bridge-option5.css)

You are inside the volume, so floating panels — the language of 03 and 04 —
are wrong. Chrome is racked to the chamber's own geometry: a thin top rail
(brand · status · nav · direction selector), and the log as an **inscription
strip** down the left edge, small and dense, each entry led by its socket
address. Type is the interface here, not a panel of it.

- Command line docks at the bottom, full width, hairline top border.
- Mission cards and chess/viewscreen reuse the 02/03 card pattern, entering
  from the right, only while they exist.
- The whole stage is push-to-talk: `#eye` stretches full-bleed ("hold
  anywhere"), as in 04.
- `#telemetry` stays in the DOM but reads as rack instrumentation along the
  bottom rail — it is machine status, and we are inside the machine.

One `min-width: 761px` block covers desktop and tablet. Below that, mobile
keeps the shared card flow with the chamber at its lowest tier.

## Performance, failure, tests

- Tiers: ~420 / 260 / 120 blocks by surface width; one instanced draw at
  every tier. DPR caps follow the vault's (1.75 / 1.5 / 1.25).
- Reduced motion: no camera drift, no travel; access-scan becomes a
  brightness crossfade; extraction fades in place.
- DOM reads defensive (missing element = zero, never a crash). WebGL
  failure keeps the shared CSS-fallback eye.
- Tests: the manifest-driven contract loop auto-extends. Deliberate edits
  are the manifest count assertion (four → five), the new link/button/loader
  entries it asserts, and a new assertion that `resetSession` awaits
  `playSessionEnd` when present.
- Playwright now covers the behaviour layer, so the extraction gets a real
  end-to-end test rather than a substring assertion: click New Session,
  assert the scene hook ran and the reload followed.

## Delivery

Scene → shell → enable (`directions.ts`, inline ready-list) → contract
extension + `resetSession` hook → browser QA (three viewports, state
contract, extraction via the real reset path, round-trip through all five
directions) → `design-qa-option5.md` → commit.

The new craft here is per-instance choreography and the reset hook; the
shader work is lighter than 04's.
