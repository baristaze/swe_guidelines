import { describe, expect, it } from "vitest";
import { shortEmail } from "./accountModel";

describe("the account button", () => {
  it("shortens an address to the part before the @", () => {
    expect(shortEmail("owner@example.test")).toBe("owner");
  });

  it("keeps anything that is not an address whole", () => {
    expect(shortEmail("owner")).toBe("owner");
    expect(shortEmail("@example.test")).toBe("@example.test");
    expect(shortEmail("")).toBe("");
  });
});
