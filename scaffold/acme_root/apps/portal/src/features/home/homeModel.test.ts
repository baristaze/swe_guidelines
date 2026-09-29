import { describe, expect, it } from "vitest";
import type { MeView } from "../../api";
import { homeCard, membersLine } from "./homeModel";

const me: MeView = {
  app: "portal",
  role: "admin",
  permissions: ["read", "write", "manage_members"],
  user: { id: "u1", email: "ann@example.test", display_name: "Ann", created_at: "2026-09-01T00:00:00Z" },
  org: { id: "o1", name: "Ajax", slug: "ajax", kind: "team", created_at: "2026-09-01T00:00:00Z", deleted_at: null },
};

describe("home model", () => {
  it("names the org, the person, their role, and how many members it has", () => {
    expect(homeCard(me, 3)).toEqual({ orgName: "Ajax", personName: "Ann", role: "admin", members: "3 members" });
  });

  it("shows a person with no display name by their email", () => {
    expect(homeCard({ ...me, user: { ...me.user, display_name: " " } }, 1).personName).toBe("ann@example.test");
  });

  it("says the count in the singular and the plural", () => {
    expect([0, 1, 2].map(membersLine)).toEqual(["0 members", "1 member", "2 members"]);
  });
});
