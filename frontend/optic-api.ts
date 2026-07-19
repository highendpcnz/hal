// The contract between the behavior layer (inline JS in static/index.html)
// and whichever direction's scene is live. Every scene module exports
// `createOptic(container, eye): HalOpticApi`; the behavior layer only ever
// talks to window.HALOptic and never knows which direction rendered it.

export type HalVisualState = "idle" | "listening" | "thinking" | "speaking" | "denied";
export type HalToolKind = "fetch" | "execute" | "search" | "read" | null;

export interface HalOpticApi {
  destroy: () => void;
  setAudioEnergy: (energy: number) => void;
  setState: (state: HalVisualState) => void;
  setToolKind: (kind: HalToolKind) => void;
}

export type CreateOptic = (container: HTMLElement, eye: HTMLElement) => HalOpticApi;

declare global {
  interface Window {
    HALOptic?: HalOpticApi;
  }
}
