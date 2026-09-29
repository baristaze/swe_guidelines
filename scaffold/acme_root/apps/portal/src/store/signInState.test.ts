import { describe, expect, it } from "vitest";
import {
  SIGN_IN_STATE_TTL_MS,
  consumeSignIn,
  newState,
  noteAccountDeleted,
  rememberSignIn,
  takeAccountDeleted,
} from "./signInState";

class MemoryStorage {
  private items = new Map<string, string>();
  getItem(key: string) {
    return this.items.get(key) ?? null;
  }
  setItem(key: string, value: string) {
    this.items.set(key, value);
  }
}

describe("the sign-ins a tab started", () => {
  it("makes a state of 32 random bytes in base64url", () => {
    const state = newState();
    expect(state).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(newState()).not.toBe(state);
  });

  it("hands back what it stored, once", () => {
    const storage = new MemoryStorage();
    rememberSignIn("abc", { returnTo: "/settings", invitationToken: "inv", codeVerifier: "ver" }, 1_000, storage);
    expect(consumeSignIn("abc", 2_000, storage)).toEqual({
      returnTo: "/settings",
      invitationToken: "inv",
      codeVerifier: "ver",
      startedAt: 1_000,
    });
    expect(consumeSignIn("abc", 3_000, storage)).toBeNull();
  });

  it("knows no state it never stored, and forgets an old one", () => {
    const storage = new MemoryStorage();
    rememberSignIn("mine", { returnTo: "/", invitationToken: null, codeVerifier: "ver" }, 0, storage);
    expect(consumeSignIn("theirs", 1, storage)).toBeNull();
    expect(consumeSignIn("mine", SIGN_IN_STATE_TTL_MS + 1, storage)).toBeNull();
  });

  it("treats unreadable storage as holding nothing", () => {
    const storage = new MemoryStorage();
    storage.setItem("acme.portal.signIn", "not json");
    expect(consumeSignIn("x", 0, storage)).toBeNull();
    expect(consumeSignIn("x", 0, undefined)).toBeNull();
  });
});

describe("an account deleted in this tab", () => {
  function memory() {
    const kept = new Map<string, string>();
    return {
      getItem: (key: string) => kept.get(key) ?? null,
      setItem: (key: string, value: string) => void kept.set(key, value),
      removeItem: (key: string) => void kept.delete(key),
    };
  }

  it("is said once on the page the browser lands on, across the provider's round trip", () => {
    const storage = memory();
    expect(takeAccountDeleted(storage)).toBe(false);
    noteAccountDeleted(storage);
    expect(takeAccountDeleted(storage)).toBe(true);
    expect(takeAccountDeleted(storage)).toBe(false);
  });
});
