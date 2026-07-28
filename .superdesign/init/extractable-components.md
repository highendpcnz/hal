# Extractable components

The frontend is a single vanilla HTML document, not a component application. There are no reusable React/Vue/Svelte layout modules to convert into Superdesign DraftComponents.

Potential future extraction candidates:

## BridgeComposer

- Source: `static/index.html`
- Category: basic
- Description: shared text input, submit action, and command suggestions for desktop and tablet.
- Extractable props: placeholder, commandMenuOpen, activeCommand
- Hardcoded: HAL labels, slash prefix, native form behavior, direction-scoped CSS.

## BridgeRail

- Source: `static/index.html`
- Category: layout
- Description: missions, chess, viewscreen, systems, and visual-direction navigation.
- Extractable props: activeItem, activeDirection
- Hardcoded: action labels, direction numbers, bridge-specific marks.

Do not extract these for the current task: their source is interleaved with the page runtime, and Superdesign should consume the real HTML/CSS ranges directly.
