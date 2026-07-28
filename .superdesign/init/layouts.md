# Shared layouts

The app has one page and no shared layout component modules. Its complete page implementation is `static/index.html`. The top-level shell is a cockpit layout whose regions are:

```html
<body>
  <div class="bridge-left">
    <header class="bridge-brand" aria-label="Selected bridge direction">...</header>
    <div class="eye-module" role="group" aria-label="HAL optical panel">...</div>
    <div class="waveform-wrap">...</div>
    <div id="missions-panel" class="missions-panel">...</div>
    <div id="chess-panel" class="chess-panel">...</div>
    <div id="viewscreen-panel" class="viewscreen-panel">...</div>
  </div>

  <div id="mission-log" class="mission-log">...</div>
  <div id="log" class="log"></div>
  <div id="caption" class="caption" aria-live="polite"></div>
  <div id="ticker" class="ticker"></div>
  <div id="permbar" class="permbar" role="alertdialog" aria-live="assertive">...</div>
  <div id="propbar" class="permbar propbar" role="alertdialog" aria-live="polite">...</div>

  <form id="cmdline" class="cmdline">...</form>
  <div id="telemetry" class="telemetry">...</div>
  <div class="bridge-input"><form id="bridge-form">...</form></div>

  <nav class="bridge-rail" aria-label="Bridge navigation and visual directions">...</nav>
  <button id="monitor-tab" class="monitor-tab" aria-label="Show system monitor">Systems</button>
  <div id="drawer" class="drawer">...</div>
</body>
```

Layout CSS:

- `static/bridge-shared.css`: fonts, tokens, accessibility focus, shared rail, and the direction-independent mobile layout.
- `static/bridge-option1.css`: Aperture Sentinel desktop/tablet grid.
- `static/bridge-option2.css`: Cognitive Orrery desktop/tablet grid.
- `static/bridge-option3.css`: Signal Vault desktop/tablet grid.
- `static/bridge-option4.css`: Ember Chorus desktop/tablet grid.
- `static/index.html`: legacy/base inline CSS and all DOM/application behavior.

Breakpoints are mobile at 760px and below, tablet from 761px through 1100px, and desktop at 1101px and above.
