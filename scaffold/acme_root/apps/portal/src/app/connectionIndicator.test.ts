import { describe, expect, it } from "vitest";
import { indicatorFor } from "./connectionIndicator";

describe("connection indicator", () => {
  it("is green only while the channel is open, and names the status", () => {
    expect(indicatorFor("open")).toEqual({ tone: "live", label: "live updates: open" });
    for (const status of ["connecting", "degraded", "paused", "closed"] as const) {
      expect(indicatorFor(status)).toEqual({ tone: "pending", label: `live updates: ${status}` });
    }
  });
});
