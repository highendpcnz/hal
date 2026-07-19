# Option 1 Design QA

Source reference: `data/viewscreen/hal-concept-1.png`

Implementation surfaces: `static/index.html`, `static/bridge-option1.css`, `frontend/hal-optic.ts`, `frontend/directions.ts`, and the generated `static/assets/hal-optic.js`

Comparison artifacts: `data/viewscreen/option1-qa-comparison.png` and `data/viewscreen/option1-qa-optic-comparison.png`

Verified viewports and states:

- Desktop layout: 1440 × 1024 CSS viewport, idle voice state, Aperture Sentinel selected. The in-app browser's visible desktop capture surface is 1440 × 931, so the desktop comparison uses the matching top crop of the source reference. DOM geometry checks cover the full 1440 × 1024 layout, including the 64 px direction rail.
- Tablet layout: 900 × 900, idle voice state, no horizontal or vertical overflow.
- Mobile layout: 390 × 844, idle voice state, no horizontal overflow, initial content fits the viewport, and the viewscreen remains closed until selected.
- Interaction states: option 01 selected; options 02 and 03 visible but disabled while in development; Missions prefill works on desktop and mobile; Viewscreen changes the active surface; Systems opens and closes the existing drawer.

## Iteration history

### Pass 1 — blocking

- P1, layout: the desktop mission-status and command hierarchy sat too close to the bottom rail compared with the reference. Fixed by allocating dedicated 124 px and 120 px rows, restoring the reference's lower-right pacing, and adding the Mission status eyebrow.
- P1, responsiveness: the tablet grid inherited centered alignment and the fixed camera distance clipped the optic in its narrow column. Fixed with explicit grid stretching and an aspect-aware camera distance.
- P2, behavior: the populated viewscreen rendered by default and displaced the intended standby hierarchy. Fixed with exclusive Missions, Chess, and Viewscreen surface states controlled from the rail.
- P2, behavior: the mobile Missions action focused the hidden desktop field. Fixed by opening and prefilling the mobile command line at the mobile breakpoint.
- P2, accessibility: the optical control exposed button semantics but lacked Enter-key push-to-talk behavior. Fixed with matched keydown and keyup handling while retaining Space, pointer, and touch input.

### Pass 2 — passed

- Fonts and typography: local Azeret Mono and IBM Plex Mono files render the display/body split with the narrow uppercase hierarchy and restrained tracking shown in the reference.
- Spacing and layout: the 40/60 desktop split, aligned 82 px headers, large optic stage, mission-log void, lower status band, command surface, and bottom navigation preserve the reference's hierarchy.
- Viewport resilience: desktop, tablet, and mobile checks show no overflow, overlapping controls, clipped optic, or inaccessible primary action.
- Colors and tokens: carbon black, machined metal, signal red, muted telemetry gray, and low-intensity borders map consistently across the live interface.
- Image and signature fidelity: the focal optic is a real-time Three.js scene with physical materials, glass, emissive state, bloom, calibration rings, retainers, highlights, and pointer parallax rather than a raster or CSS substitute.
- Copy and content: all static labels are coherent in context; live mission, telemetry, permission, chess, viewscreen, and voice content remains connected to the existing application.
- Icons and controls: rail icons use one geometric line family; selected and disabled direction states are visibly distinct; focus indicators are present.
- Accessibility: semantic labels, keyboard reachability, reduced-motion handling, visible focus, touch targets, and the retained CSS fallback are present.
- Runtime: one WebGL canvas initializes, option 01 is selected, options 02 and 03 remain disabled, the final browser reload produced no new warnings or errors, and the repaired launcher reports the Hermes ACP bridge as operational.

Intentional differences from the concept are functional: the reference's placeholder mission-status copy is replaced by live Bridge, STT, Voice, Tools, Uptime, and Lag data; the lower rail includes the required three-direction selector; and the captured idle state shows a quiet waveform rather than fabricated listening audio.

final result: passed
