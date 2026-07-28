# Shared UI primitives

Framework: vanilla HTML, CSS, and browser JavaScript. There is no React or component library. The primary interface is a monolithic `static/index.html`; the snippets below are the complete source for the reusable composer controls that matter to this task.

## BridgeComposer

- Source: `static/index.html`
- Purpose: always-visible tablet and desktop text composer.
- Native semantics: form, text input, submit button.

```html
<div class="bridge-input">
  <form id="bridge-form">
    <input id="bridge-cmd" type="text" autocomplete="off" spellcheck="false"
           placeholder="Type to HAL — Enter sends, Space bar to speak" />
    <button type="submit">Send</button>
  </form>
</div>
```

```css
.bridge-input {
  display: none;
}
.bridge-input form {
  display: flex;
  gap: 8px;
}
.bridge-input input {
  flex: 1;
  background: #050505;
  border: 1px solid #1e1e1e;
  border-radius: 999px;
  color: #b84b3b;
  font: inherit;
  font-size: 12px;
  letter-spacing: 0.04em;
  padding: 9px 16px;
  outline: none;
  transition: border-color 0.2s;
}
.bridge-input input::placeholder { color: #3a2220; }
.bridge-input input:focus { border-color: #4a1710; }
.bridge-input button {
  border: 1px solid #1e1e1e;
  border-radius: 999px;
  background: #050505;
  color: #555;
  cursor: pointer;
  font: inherit;
  font-size: 10px;
  letter-spacing: 0.12em;
  padding: 9px 14px;
  text-transform: uppercase;
  transition: border-color 0.2s, color 0.2s;
}
.bridge-input button:hover {
  border-color: #4a1710;
  color: #b84b3b;
}
```

## MobileCommandLine

- Source: `static/index.html`
- Purpose: mobile composer opened with the `/` keyboard shortcut.

```html
<form id="cmdline" class="cmdline">
  <input id="cmd-input" type="text" autocomplete="off" spellcheck="false"
         placeholder="Type to HAL — Enter sends, Esc closes" />
</form>
```

## RailAction

- Source: `static/index.html`
- Purpose: native bridge navigation button pattern.

```html
<button class="rail-action" type="button" data-bridge-action="missions">Missions</button>
```

The corresponding base styles live in `static/bridge-shared.css`; each visual direction adds scoped overrides in `static/bridge-option1.css` through `static/bridge-option4.css`.
