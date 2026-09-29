import { describe, expect, it } from "vitest";
import type { MeView } from "@acme/client";
import { confirmsName, mayDeleteOrg, ORG_GONE_LINE } from "./deleteOrgModel";

const owner: MeView = {
  user: { id: "u1", email: "ann@example.test", display_name: "Ann", created_at: "2026-09-01T00:00:00Z" },
  org: { id: "o1", name: "Ajax", slug: "ajax", kind: "team", created_at: "2026-09-01T00:00:00Z" },
  role: "owner",
  permissions: ["read", "write", "manage_members", "manage_keys"],
  app: "portal",
};

describe("delete org model", () => {
  it("shows the card to an owner of a team org, and to nobody else", () => {
    expect(mayDeleteOrg(owner)).toBe(true);
    expect(mayDeleteOrg({ ...owner, role: "admin" })).toBe(false);
    expect(mayDeleteOrg({ ...owner, role: "member" })).toBe(false);
    expect(mayDeleteOrg({ ...owner, org: { ...owner.org, kind: "personal" } })).toBe(false);
    expect(mayDeleteOrg(undefined)).toBe(false);
  });

  it("goes on only once the org's name is typed as it is spelled", () => {
    expect(confirmsName("", "Ajax")).toBe(false);
    expect(confirmsName("ajax", "Ajax")).toBe(false);
    expect(confirmsName("Ajax Inc", "Ajax")).toBe(false);
    expect(confirmsName("  Ajax ", "Ajax")).toBe(true);
    expect(confirmsName("Ajax", undefined)).toBe(false);
  });

  it("says how long the data is kept", () => {
    expect(ORG_GONE_LINE).toBe("Its data is kept for 30 days, then purged, and gone from backups 7 days after that.");
  });
});
