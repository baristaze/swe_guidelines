import { describe, expect, it } from "vitest";
import { ApiError } from "../../api";
import { checkNewOrg, isSlug, newOrgBody, newOrgRefusal, suggestSlug } from "./newOrgModel";

describe("checkNewOrg", () => {
  it("needs a name and takes the short name as optional", () => {
    expect(checkNewOrg({ name: "Dee's Bakery", slug: "" })).toEqual({ ok: true, message: null });
    expect(checkNewOrg({ name: "Dee's Bakery", slug: "bakery" }).ok).toBe(true);
    expect(checkNewOrg({ name: "  ", slug: "" }).message).toMatch(/Name the organization/);
    expect(checkNewOrg({ name: "Bakery", slug: "Not A Slug" }).message).toMatch(/short name/);
  });
});

describe("the short name", () => {
  it("is suggested from the name", () => {
    expect(suggestSlug("Dee's Bakery")).toBe("dee-s-bakery");
    expect(suggestSlug("  Café Zürich  ")).toBe("cafe-zurich");
    expect(suggestSlug("!!!")).toBe("");
    expect(suggestSlug("a".repeat(60))).toHaveLength(48);
  });

  it("is what the server accepts", () => {
    expect(isSlug("ajax-labs")).toBe(true);
    expect(isSlug("ajax--labs")).toBe(false);
    expect(isSlug("-ajax")).toBe(false);
    expect(isSlug("a".repeat(49))).toBe(false);
  });

  it("is sent only when typed", () => {
    expect(newOrgBody({ name: " Bakery ", slug: "" })).toEqual({ name: "Bakery" });
    expect(newOrgBody({ name: "Bakery", slug: "bakery" })).toEqual({ name: "Bakery", slug: "bakery" });
  });
});

describe("newOrgRefusal", () => {
  it("says a taken short name as the server did, as a sentence", () => {
    const taken = new ApiError(409, "conflict", "org slug 'ajax' is taken", "r1");
    expect(newOrgRefusal(taken)).toBe("Org slug 'ajax' is taken.");
  });
});
