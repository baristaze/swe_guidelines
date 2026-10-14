// Runs without a browser: an in-memory Storage stands in for the tab's
// session storage, the way the session store's own tests do it.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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

beforeEach(() => {
  vi.stubGlobal("sessionStorage", new MemoryStorage());
  vi.stubGlobal("localStorage", new MemoryStorage());
  vi.resetModules();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("forgetAuthSession", () => {
  it("drops the token and every cached answer together", async () => {
    const { forgetAuthSession } = await import("./forgetAuthSession");
    const { queryClient } = await import("./queryClient");
    const { useAuthSessionStore } = await import("../store/authSession");
    useAuthSessionStore.getState().setAuthSession("tok_1", "ajax");
    queryClient.setQueryData(["me"], { role: "owner" });
    queryClient.setQueryData(["user", "list", 200], { pages: [], pageParams: [] });

    forgetAuthSession();

    expect(useAuthSessionStore.getState().token).toBeNull();
    expect(useAuthSessionStore.getState().orgSlug).toBeNull();
    expect(queryClient.getQueryCache().getAll()).toEqual([]);
  });

  it("leaves a newer session alone when an old one's refusal arrives late", async () => {
    const { forgetAuthSessionIfHeld } = await import("./forgetAuthSession");
    const { queryClient } = await import("./queryClient");
    const { useAuthSessionStore } = await import("../store/authSession");
    useAuthSessionStore.getState().setAuthSession("ses_new", "beta");
    queryClient.setQueryData(["me"], { org: { slug: "beta" } });

    forgetAuthSessionIfHeld("ses_old");
    expect(useAuthSessionStore.getState().token).toBe("ses_new");
    expect(queryClient.getQueryData(["me"])).toEqual({ org: { slug: "beta" } });

    forgetAuthSessionIfHeld("ses_new");
    expect(useAuthSessionStore.getState().token).toBeNull();
    expect(queryClient.getQueryCache().getAll()).toEqual([]);
  });
});
