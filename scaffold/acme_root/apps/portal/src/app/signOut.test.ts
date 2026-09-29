import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../api";
import { isAlreadyGone, signOut, type SignOutEffects } from "./signOut";

function effects(revoke: SignOutEffects["revoke"]) {
  const forget = vi.fn<SignOutEffects["forget"]>();
  return {
    revoke: vi.fn(async () => {
      // The token must still be held while the server is asked.
      expect(forget).not.toHaveBeenCalled();
      return revoke();
    }),
    forget,
    report: vi.fn<SignOutEffects["report"]>(),
    leave: vi.fn<SignOutEffects["leave"]>(() => {
      // The session is gone here before the browser leaves.
      expect(forget).toHaveBeenCalledTimes(1);
    }),
  };
}

const PROVIDER_LOGOUT =
  "https://api.workos.com/user_management/sessions/logout?session_id=session_01&return_to=https%3A%2F%2Fapp.example%2Fsigned-out";

describe("signOut", () => {
  it("revokes the server session first, then forgets it here", async () => {
    const e = effects(() => Promise.resolve({ provider_logout_url: null }));
    await expect(signOut(e)).resolves.toBe("revoked");
    expect(e.revoke).toHaveBeenCalledTimes(1);
    expect(e.forget).toHaveBeenCalledTimes(1);
    expect(e.report).not.toHaveBeenCalled();
    // A session with no provider session behind it stays on the portal.
    expect(e.leave).not.toHaveBeenCalled();
  });

  it("then sends the browser to the provider's logout the server answered", async () => {
    const e = effects(() => Promise.resolve({ provider_logout_url: PROVIDER_LOGOUT }));
    await expect(signOut(e)).resolves.toBe("revoked");
    expect(e.leave).toHaveBeenCalledTimes(1);
    expect(e.leave).toHaveBeenCalledWith(PROVIDER_LOGOUT);
  });

  it("stays on the portal when the server could not be asked", async () => {
    const e = effects(() => Promise.reject(new TypeError("Failed to fetch")));
    await signOut(e);
    expect(e.leave).not.toHaveBeenCalled();
  });

  it("treats a 401 as already signed out and says nothing", async () => {
    const e = effects(() => Promise.reject(new ApiError(401, "unauthenticated", "Sign in first.", "req_1")));
    await expect(signOut(e)).resolves.toBe("already_gone");
    expect(e.forget).toHaveBeenCalledTimes(1);
    expect(e.report).not.toHaveBeenCalled();
  });

  it("finishes locally when the network fails, and says the server session stands", async () => {
    const e = effects(() => Promise.reject(new TypeError("Failed to fetch")));
    await expect(signOut(e)).resolves.toBe("not_revoked");
    expect(e.forget).toHaveBeenCalledTimes(1);
    expect(e.report).toHaveBeenCalledWith("Failed to fetch");
  });

  it("finishes locally on a server fault too, quoting its request id", async () => {
    const e = effects(() => Promise.reject(new ApiError(503, "unavailable", "Try again later.", "req_2")));
    await expect(signOut(e)).resolves.toBe("not_revoked");
    expect(e.forget).toHaveBeenCalledTimes(1);
    expect(e.report).toHaveBeenCalledWith("Try again later. Reference: req_2");
  });

  it("knows a 401 from any other failure", () => {
    expect(isAlreadyGone(new ApiError(401, "unauthenticated", "x", null))).toBe(true);
    expect(isAlreadyGone(new ApiError(500, "boom", "x", null))).toBe(false);
    expect(isAlreadyGone(new Error("offline"))).toBe(false);
  });
});
