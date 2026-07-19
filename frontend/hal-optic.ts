// Direction runtime: resolves the selected visual direction, wires the
// selector rail, and boots that direction's scene module. The scene chunk
// is fetched on demand so each direction only pays for its own code.
//
// The stored-selection contract (localStorage key, ready-gating, fallback
// to the default direction) is mirrored by the inline pre-paint script in
// static/index.html that picks the direction stylesheet — keep them in sync.

import {
  ACTIVE_DIRECTION,
  BRIDGE_DIRECTIONS,
  type BridgeDirectionId,
  type BridgeDirectionManifest
} from "./directions";
import type { CreateOptic } from "./optic-api";

const DIRECTION_STORAGE_KEY = "hal_direction";

const SCENE_LOADERS: Record<BridgeDirectionId, () => Promise<{ createOptic: CreateOptic }>> = {
  aperture: () => import("./optic-aperture"),
  orrery: () => import("./optic-orrery"),
  vault: () => import("./optic-vault")
};

function resolveDirection(): BridgeDirectionManifest {
  let stored: string | null = null;
  try {
    stored = localStorage.getItem(DIRECTION_STORAGE_KEY);
  } catch {
    // Storage unavailable (private mode) — run the default direction.
  }
  const manifest = BRIDGE_DIRECTIONS.find((direction) => direction.id === stored);
  if (manifest?.ready) return manifest;
  if (stored !== null) {
    // Unknown or not-yet-ready selection: drop it so a direction that
    // ships later doesn't surprise-activate from a stale preference.
    try {
      localStorage.removeItem(DIRECTION_STORAGE_KEY);
    } catch {
      /* ignore */
    }
  }
  return BRIDGE_DIRECTIONS.find((direction) => direction.id === ACTIVE_DIRECTION)!;
}

function selectDirection(id: BridgeDirectionId): void {
  try {
    localStorage.setItem(DIRECTION_STORAGE_KEY, id);
  } catch {
    console.warn("Direction selection needs localStorage; staying on the current direction.");
    return;
  }
  // The server holds all conversational state; a reload is the cheap,
  // teardown-proof way to swap shells (stylesheet + scene together).
  location.reload();
}

function setupDirectionSelector(active: BridgeDirectionManifest): void {
  document.documentElement.dataset.bridgeDirection = active.id;
  const brandTitle = document.querySelector(".bridge-brand-title");
  if (brandTitle) brandTitle.textContent = active.label;
  const buttons = document.querySelectorAll<HTMLButtonElement>("[data-direction-id]");
  for (const button of buttons) {
    const id = button.dataset.directionId;
    const manifest = BRIDGE_DIRECTIONS.find((direction) => direction.id === id);
    if (!manifest) continue;
    button.textContent = manifest.shortLabel;
    button.title = manifest.ready ? manifest.label : `${manifest.label} — in development`;
    button.setAttribute("aria-label", `Select ${manifest.label}`);
    button.disabled = !manifest.ready;
    button.setAttribute("aria-pressed", String(manifest.id === active.id));
    button.addEventListener("click", () => {
      if (!manifest.ready || manifest.id === active.id) return;
      selectDirection(manifest.id);
    });
  }
}

function setupBridgeRail(): void {
  const bridgeCommand = document.getElementById("bridge-cmd") as HTMLInputElement | null;
  const mobileCommand = document.getElementById("cmd-input") as HTMLInputElement | null;
  const mobileCommandLine = document.getElementById("cmdline");
  const bridgeLeft = document.querySelector<HTMLElement>(".bridge-left");
  const chessPanel = document.getElementById("chess-panel");
  const actions = document.querySelectorAll<HTMLButtonElement>("[data-bridge-action]");
  for (const action of actions) {
    action.addEventListener("click", () => {
      switch (action.dataset.bridgeAction) {
        case "missions":
          if (bridgeLeft) delete bridgeLeft.dataset.activeSurface;
          const command = window.matchMedia("(min-width: 761px)").matches ? bridgeCommand : mobileCommand;
          if (command) {
            if (!command.value) command.value = "/mission ";
            if (command === mobileCommand) mobileCommandLine?.classList.add("open");
            command.focus();
          }
          break;
        case "chess":
          if (bridgeLeft) bridgeLeft.dataset.activeSurface = "chess";
          document.getElementById("chess-new")?.click();
          break;
        case "viewscreen":
          if (bridgeLeft) bridgeLeft.dataset.activeSurface = "viewscreen";
          break;
        case "systems":
          document.getElementById("monitor-tab")?.click();
          break;
      }
    });
  }

  if (bridgeLeft && chessPanel) {
    const chessObserver = new MutationObserver(() => {
      if (chessPanel.classList.contains("on")) bridgeLeft.dataset.activeSurface = "chess";
      else if (bridgeLeft.dataset.activeSurface === "chess") delete bridgeLeft.dataset.activeSurface;
    });
    chessObserver.observe(chessPanel, { attributes: true, attributeFilter: ["class"] });
  }
}

async function boot(): Promise<void> {
  const active = resolveDirection();
  setupDirectionSelector(active);
  setupBridgeRail();
  const container = document.getElementById("optic-stage");
  const eye = document.getElementById("eye");
  if (!container || !eye) return;

  try {
    const { createOptic } = await SCENE_LOADERS[active.id]();
    window.HALOptic?.destroy();
    window.HALOptic = createOptic(container, eye);
    window.addEventListener("pagehide", () => window.HALOptic?.destroy(), { once: true });
  } catch (error) {
    console.warn("HAL optic renderer unavailable; retaining the CSS fallback.", error);
  }
}

void boot();
