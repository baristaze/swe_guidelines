// The upgrade dialog, one for the whole app: open with the bound a refusal
// met, closed by the person. A write refused for a plan's bound opens it
// instead of leaving a notice, since the answer to it is a plan, not a retry.
import { create } from "zustand";
import { ApiError, type PlanLimit } from "../api";

interface UpgradeState {
  limit: PlanLimit | null;
  open: (limit: PlanLimit) => void;
  close: () => void;
}

export const useUpgradeStore = create<UpgradeState>()((set) => ({
  limit: null,
  open: (limit) => set({ limit }),
  close: () => set({ limit: null }),
}));

/** The bound a failure met, when it is a plan's refusal. */
export function planLimitOf(caught: unknown): PlanLimit | null {
  if (!(caught instanceof ApiError) || caught.code !== "plan_limit_reached") return null;
  return caught.planLimit ?? { lever: "unknown", plan: "free", limit: null, suggested_plan: null };
}

export function isPlanLimit(caught: unknown): boolean {
  return planLimitOf(caught) !== null;
}

/** Opens the dialog for a plan's refusal; true when it did. For code outside React. */
export function offerUpgrade(caught: unknown): boolean {
  const limit = planLimitOf(caught);
  if (limit === null) return false;
  useUpgradeStore.getState().open(limit);
  return true;
}
