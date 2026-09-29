import { describe, expect, it } from "vitest";
import { humanSize, usageLine } from "./storageModel";

describe("storage model", () => {
  it("says a size in the unit a person reads", () => {
    expect([0, 1023, 1024, 1536, 25 * 1024 * 1024].map(humanSize)).toEqual([
      "0 B",
      "1023 B",
      "1.0 KB",
      "1.5 KB",
      "25.0 MB",
    ]);
  });

  it("says the storage line in the singular and the plural", () => {
    expect(usageLine(1, 10)).toBe("1 file, 10 B");
    expect(usageLine(3, 3 * 1024 * 1024)).toBe("3 files, 3.0 MB");
  });
});
