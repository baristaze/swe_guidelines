import { describe, expect, it } from "vitest";
import { SCOPE_CHOICES, scopeHeading, scopeHeadingKind, shownScope } from "./scopeModel";

describe("the list the tasks page shows", () => {
  it("offers the switch when the org has more than one member, whatever its kind", () => {
    // A personal org with two members and a team org of three alike.
    expect(scopeHeadingKind(2)).toBe("switch");
    expect(scopeHeadingKind(3)).toBe("switch");
  });

  it("says a plain My tasks when the person is alone, a team org of one included", () => {
    expect(scopeHeadingKind(1)).toBe("alone");
    expect(scopeHeadingKind(0)).toBe("alone");
  });

  it("honours the saved team pick only where the switch shows", () => {
    expect(shownScope("team", 2)).toBe("team");
    expect(shownScope("mine", 2)).toBe("mine");
    expect(shownScope("team", 1)).toBe("mine");
    expect(shownScope("mine", 1)).toBe("mine");
  });

  it("keeps the pick while the member list is on its way", () => {
    expect(scopeHeadingKind(undefined)).toBe("pending");
    expect(shownScope("team", undefined)).toBe("team");
  });

  it("names the switch's two sides and the heading behind them", () => {
    expect(SCOPE_CHOICES.map((choice) => choice.label)).toEqual(["My tasks", "Team"]);
    expect(scopeHeading("mine")).toBe("My tasks");
    expect(scopeHeading("team")).toBe("Team tasks");
  });
});
