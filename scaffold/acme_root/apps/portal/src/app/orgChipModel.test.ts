import { describe, expect, it } from "vitest";
import type { MembershipChoiceView } from "../api";
import { chipChoices, placeNote } from "./orgChipModel";

const place = (id: string, name: string, kind: "personal" | "team" = "team", role = "owner"): MembershipChoiceView =>
  ({
    org: { id, name, slug: id, kind, created_at: "2026-01-01T00:00:00Z" },
    user: { id: `u-${id}`, email: "dee@example.test", display_name: "Dee", created_at: "2026-01-01T00:00:00Z" },
    role,
  }) as MembershipChoiceView;

describe("chipChoices", () => {
  it("offers the other places, the personal one first, then by name", () => {
    const places = [place("z", "Zeta"), place("d", "Dee", "personal"), place("a", "Ajax"), place("c", "Cafe")];
    const choices = chipChoices(places, "c");
    expect(choices.canSwitch).toBe(true);
    expect(choices.others.map((m) => m.org.name)).toEqual(["Dee", "Ajax", "Zeta"]);
  });

  it("has nothing to switch to with one place, and nothing before the lists arrive", () => {
    expect(chipChoices([place("d", "Dee", "personal")], "d")).toEqual({ canSwitch: false, others: [] });
    expect(chipChoices(undefined, "d")).toEqual({ canSwitch: false, others: [] });
    expect(chipChoices([place("d", "Dee", "personal")], undefined).canSwitch).toBe(false);
  });
});

describe("placeNote", () => {
  it("says the role, and personal for the person's own", () => {
    expect(placeNote(place("d", "Dee", "personal"))).toBe("owner · personal");
    expect(placeNote(place("a", "Ajax", "team", "member"))).toBe("member");
  });
});
