export type BridgeDirectionId = "aperture" | "orrery" | "vault";

export interface BridgeDirectionManifest {
  readonly id: BridgeDirectionId;
  readonly label: string;
  readonly shortLabel: string;
  readonly ready: boolean;
}

export const BRIDGE_DIRECTIONS: readonly BridgeDirectionManifest[] = [
  {
    id: "aperture",
    label: "Aperture Sentinel",
    shortLabel: "01",
    ready: true
  },
  {
    id: "orrery",
    label: "Cognitive Orrery",
    shortLabel: "02",
    ready: false
  },
  {
    id: "vault",
    label: "Signal Vault",
    shortLabel: "03",
    ready: false
  }
] as const;

export const ACTIVE_DIRECTION: BridgeDirectionId = "aperture";
