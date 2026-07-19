import type { HalOpticApi } from "./optic-api";

// Direction 03 "Signal Vault" — scene lands with Phase 2 of
// docs/plans/directions-2-3.md. Unreachable until directions.ts marks
// "vault" ready; the boot guard keeps the CSS fallback if it ever loads.
export function createOptic(): HalOpticApi {
  throw new Error("Signal Vault scene is not implemented yet (direction 03).");
}
