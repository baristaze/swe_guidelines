import { describe, expect, it } from "vitest";
import { ApiError } from "@acme/client";
import { confirms, GONE_LINE, listed, ownedAlone, refusalText, strandedHere } from "./deleteAccountModel";

const ajax = { id: "o1", name: "Ajax", slug: "ajax" };
const globex = { id: "o2", name: "Globex", slug: "globex" };

function lastOwner(...orgs: (typeof ajax)[]) {
  return new ApiError(409, "last_owner", "you are the last owner", "req_1", undefined, null, orgs);
}

describe("delete account model", () => {
  it("goes on only once the account's email is typed, space and case forgiven", () => {
    expect(confirms("", "bob@example.test")).toBe(false);
    expect(confirms("bob@example", "bob@example.test")).toBe(false);
    expect(confirms("  Bob@Example.TEST ", "bob@example.test")).toBe(true);
    expect(confirms("bob@example.test", undefined)).toBe(false);
  });

  it("says what becomes of the data, backups included", () => {
    expect(GONE_LINE).toBe("Gone now, and gone from backups within 7 days.");
  });

  it("names every org the person is the last owner of, and both ways out", () => {
    expect(refusalText(lastOwner(ajax))).toBe(
      "You are the last owner of Ajax. Make someone else an owner, or delete the organization, first. " +
        "Both are done in that organization's Settings.",
    );
    expect(refusalText(lastOwner(ajax, globex))).toBe(
      "You are the last owner of Ajax and Globex. In each, make someone else an owner, or delete the organization, first. " +
        "Both are done in that organization's Settings.",
    );
    expect(ownedAlone(lastOwner(ajax))).toEqual([ajax]);
  });

  it("links to this page's ways out only when the org it is in is one of them", () => {
    expect(strandedHere([ajax, globex], "o2")).toBe(true);
    expect(strandedHere([ajax], "o2")).toBe(false);
    expect(strandedHere([ajax], undefined)).toBe(false);
  });

  it("says any other refusal in the server's words", () => {
    const operator = new ApiError(403, "operator_role_held", "an operator's account is kept", "req_2");
    expect(refusalText(operator)).toBe("An operator's account is kept.");
    expect(ownedAlone(operator)).toEqual([]);
  });

  it("lists names the way a sentence does", () => {
    expect(listed([])).toBe("");
    expect(listed(["A"])).toBe("A");
    expect(listed(["A", "B", "C"])).toBe("A, B, and C");
  });
});
