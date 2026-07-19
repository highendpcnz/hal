import type { HalOpticApi } from "./optic-api";

// Direction 02 "Cognitive Orrery" — scene lands with Phase 1 of
// docs/plans/directions-2-3.md. Unreachable until directions.ts marks
// "orrery" ready; the boot guard keeps the CSS fallback if it ever loads.
export function createOptic(): HalOpticApi {
  throw new Error("Cognitive Orrery scene is not implemented yet (direction 02).");
}
