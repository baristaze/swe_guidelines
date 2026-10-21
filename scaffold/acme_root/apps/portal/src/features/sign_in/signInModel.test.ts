import { ApiError, type tenancy } from "@acme/client";
import { describe, expect, it } from "vitest";
import type { PendingSignIn } from "../../store/signInState";
import { callbackStep, chooseOrg, landingPath, readStart, startFailure } from "./signInModel";

const membership = (name: string, kind: "personal" | "team" = "team"): tenancy.MembershipChoiceView => ({
  org: { id: name, name, slug: name.toLowerCase(), kind, created_at: "2026-01-01T00:00:00Z" },
  user: { id: "u", email: "a@b.c", display_name: "A", created_at: "2026-01-01T00:00:00Z" },
  role: "member",
});

const pending: PendingSignIn = {
  returnTo: "/settings",
  invitationToken: null,
  codeVerifier: "v".repeat(43),
  startedAt: 0,
};

describe("chooseOrg", () => {
  it("picks the only org and puts the personal one first among several", () => {
    expect(chooseOrg([])).toEqual({ kind: "none" });
    expect(chooseOrg([membership("Ajax")]).kind).toBe("single");
    const several = chooseOrg([membership("Zeta"), membership("Ajax"), membership("Zed", "personal")]);
    expect(several.kind === "several" && several.memberships.map((m) => m.org.name)).toEqual([
      "Zed",
      "Ajax",
      "Zeta",
    ]);
  });
});

describe("where signing in lands", () => {
  it("returns to an in-app page and nowhere else", () => {
    expect(landingPath("/settings")).toBe("/settings");
    for (const notOurs of [undefined, 42, "https://evil.test/", "//evil.test", "/\\evil.test", "settings"]) {
      expect(landingPath(notOurs)).toBe("/");
    }
  });

  it("never lands on a page of the sign-in itself", () => {
    for (const path of ["/login", "/auth/callback?code=x", "/sign-in", "/sign-up"]) {
      expect(landingPath(path)).toBe("/");
    }
  });
});

describe("what /login was asked for", () => {
  it("carries an invitation's token and the sign-up screen", () => {
    expect(readStart("?invitation_token=inv&screen_hint=sign-up")).toEqual({
      invitationToken: "inv",
      signUp: true,
    });
    expect(readStart("")).toEqual({ invitationToken: null, signUp: false });
  });
});

describe("what /login says when the sign-in cannot start", () => {
  it("says sign-in is not available when the API cannot reach a provider", () => {
    const unavailable = new ApiError(503, "unavailable", "internal error", "req_1");
    expect(startFailure(unavailable)).toBe("Sign-in is not available right now.");
  });

  it("says any other failure with its reference", () => {
    expect(startFailure(new ApiError(500, "internal", "internal error", "req_2"))).toBe(
      "Internal error. Reference: req_2",
    );
  });
});

describe("what a callback may do", () => {
  const known = (state: string) => (state === "mine" ? pending : null);

  it("exchanges a code whose state this tab stored", () => {
    expect(callbackStep("?code=c1&state=mine", known)).toEqual({ kind: "exchange", code: "c1", pending });
  });

  it("refuses a state it never stored, or none", () => {
    expect(callbackStep("?code=c1&state=theirs", known).kind).toBe("refused");
    expect(callbackStep("?code=c1", known).kind).toBe("refused");
  });

  it("says the provider's refusal and consumes the state all the same", () => {
    const consumed: string[] = [];
    const step = callbackStep("?error=access_denied&error_description=No%20thanks&state=mine", (state) => {
      consumed.push(state);
      return pending;
    });
    expect(step).toEqual({ kind: "refused", message: "Sign-in was refused: No thanks" });
    expect(consumed).toEqual(["mine"]);
  });

  it("refuses a callback with no code", () => {
    expect(callbackStep("?state=mine", known).kind).toBe("refused");
  });
});
