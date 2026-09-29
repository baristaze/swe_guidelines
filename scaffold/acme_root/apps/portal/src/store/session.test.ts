// The store runs here without a browser: two in-memory Storage twins stand in
// for the tab's session storage and the origin's local storage.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const KEY = "acme.portal.session";

class MemoryStorage implements Storage {
  private items = new Map<string, string>();
  get length() {
    return this.items.size;
  }
  clear() {
    this.items.clear();
  }
  getItem(key: string) {
    return this.items.get(key) ?? null;
  }
  key(index: number) {
    return [...this.items.keys()][index] ?? null;
  }
  removeItem(key: string) {
    this.items.delete(key);
  }
  setItem(key: string, value: string) {
    this.items.set(key, value);
  }
}

let session: MemoryStorage;
let local: MemoryStorage;

beforeEach(() => {
  session = new MemoryStorage();
  local = new MemoryStorage();
  vi.stubGlobal("sessionStorage", session);
  vi.stubGlobal("localStorage", local);
  vi.resetModules();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("session store", () => {
  it("persists the token in session storage and never in local storage", async () => {
    const { useSessionStore } = await import("./session");
    useSessionStore.getState().setSession("tok_1", "ajax");

    const stored = session.getItem(KEY);
    expect(stored).not.toBeNull();
    expect(JSON.parse(stored as string).state).toEqual({ token: "tok_1", orgSlug: "ajax" });
    expect(local.getItem(KEY)).toBeNull();
    expect(local.length).toBe(0);
  });

  it("reads the session back from session storage on load", async () => {
    session.setItem(KEY, JSON.stringify({ state: { token: "tok_2", orgSlug: "ajax" }, version: 0 }));
    const { useSessionStore } = await import("./session");
    expect(useSessionStore.getState().token).toBe("tok_2");
    expect(useSessionStore.getState().orgSlug).toBe("ajax");
  });

  it("clears the persisted session on sign-out", async () => {
    const { useSessionStore } = await import("./session");
    useSessionStore.getState().setSession("tok_3", "ajax");
    useSessionStore.getState().clear();
    expect(JSON.parse(session.getItem(KEY) as string).state).toEqual({ token: null, orgSlug: null });
  });
});
