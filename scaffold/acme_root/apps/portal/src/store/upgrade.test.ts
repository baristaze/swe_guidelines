import { afterEach, describe, expect, it } from "vitest";
import { ApiError } from "../api";
import { queryClient } from "../app/queryClient";
import { isPlanLimit, offerUpgrade, planLimitOf, useUpgradeStore } from "./upgrade";

const LIMIT = { lever: "members", plan: "free", limit: 1, suggested_plan: "team" };

function refusal() {
  return new ApiError(402, "plan_limit_reached", "the free plan allows 1 member", "req_1", undefined, LIMIT);
}

afterEach(() => useUpgradeStore.getState().close());

describe("the upgrade dialog's store", () => {
  it("opens with the bound a plan's refusal met and closes", () => {
    expect(offerUpgrade(refusal())).toBe(true);
    expect(useUpgradeStore.getState().limit).toEqual(LIMIT);
    useUpgradeStore.getState().close();
    expect(useUpgradeStore.getState().limit).toBeNull();
  });

  it("leaves every other failure to the screen that made the call", () => {
    for (const other of [
      new ApiError(409, "conflict", "taken", "req_2"),
      new ApiError(503, "billing_unavailable", "internal error", "req_3"),
      new Error("offline"),
      "not an error",
    ]) {
      expect(isPlanLimit(other)).toBe(false);
      expect(offerUpgrade(other)).toBe(false);
    }
    expect(useUpgradeStore.getState().limit).toBeNull();
  });

  it("still opens for a refusal whose envelope carried no detail", () => {
    const bare = new ApiError(402, "plan_limit_reached", "the plan is at its bound", "req_4");
    expect(planLimitOf(bare)).toMatchObject({ plan: "free", suggested_plan: null });
  });

  it("is opened by any write the shared cache sees refused for a plan's bound", async () => {
    const write = queryClient.getMutationCache().build(queryClient, {
      mutationFn: () => Promise.reject(refusal()),
    });
    await write.execute(undefined).catch(() => undefined);
    expect(useUpgradeStore.getState().limit).toEqual(LIMIT);
  });
});
