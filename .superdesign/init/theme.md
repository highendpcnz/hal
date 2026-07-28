# Theme

## Compact token summary

### Core palette

- `--void: #020202` — primary canvas
- `--carbon: #070809` — inset control and panel surface
- `--machined: #17191b` — raised machinery
- `--signal: #ff2d1f` — primary HAL signal/action
- `--ember: #8f0a04` — deep signal state
- `--telemetry: #8e8a84` — primary interface text
- `--ghost: #514e4a` — metadata and dormant state
- `--hairline: rgba(196, 189, 180, 0.14)` — quiet structural border
- `--hairline-hot: rgba(255, 45, 31, 0.4)` — focus and active border

### Typography

- Display/labels: Azeret Mono, weights 400 and 600.
- Body/data/input: IBM Plex Mono, weights 400 and 500.
- Fallback: system monospace.
- UI labels are generally 9–12px with 0.12–0.18em tracking and uppercase.
- Conversation copy is generally 12–14px.

### Spacing, shape, and depth

- Base unit: 4px.
- Dense control spacing: 8–16px.
- Region padding: 20–24px.
- Input and action radius: capsule in legacy/mobile; direction styles may render machined rectangular forms.
- Depth strategy: borders and surface shifts. Avoid generic drop shadows, glass, and decorative gradients.
- Inputs are inset and darker than surrounding surfaces.

### Motion and breakpoints

- Interaction timing: about 180–200ms.
- Animate opacity, color, and transform only.
- Reduced-motion handling exists in `static/bridge-shared.css`.
- Mobile: max-width 760px.
- Tablet: 761–1100px.
- Desktop: min-width 1101px.

## Raw token source

Source: `static/bridge-shared.css`

```css
@font-face {
  font-family: "Azeret Mono";
  font-style: normal;
  font-weight: 400;
  font-display: swap;
  src: url("/static/fonts/azeret-mono-400.woff2") format("woff2");
}

@font-face {
  font-family: "Azeret Mono";
  font-style: normal;
  font-weight: 600;
  font-display: swap;
  src: url("/static/fonts/azeret-mono-600.woff2") format("woff2");
}

@font-face {
  font-family: "IBM Plex Mono";
  font-style: normal;
  font-weight: 400;
  font-display: swap;
  src: url("/static/fonts/ibm-plex-mono-400.woff2") format("woff2");
}

@font-face {
  font-family: "IBM Plex Mono";
  font-style: normal;
  font-weight: 500;
  font-display: swap;
  src: url("/static/fonts/ibm-plex-mono-500.woff2") format("woff2");
}

:root {
  --void: #020202;
  --carbon: #070809;
  --machined: #17191b;
  --signal: #ff2d1f;
  --ember: #8f0a04;
  --telemetry: #8e8a84;
  --ghost: #514e4a;
  --hairline: rgba(196, 189, 180, 0.14);
  --hairline-hot: rgba(255, 45, 31, 0.4);
}

html,
body {
  background: var(--void);
  color: var(--telemetry);
  font-family: "IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
}

button:focus-visible,
input:focus-visible,
.eye:focus-visible {
  outline: 1px solid var(--signal);
  outline-offset: 4px;
}
```

Complete raw CSS remains in `static/bridge-shared.css` and `static/bridge-option1.css` through `static/bridge-option4.css`.
