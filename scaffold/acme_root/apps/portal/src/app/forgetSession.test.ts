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

describe("forgetSession", () => {
  it("drops the token and every cached answer together", async () => {
    const { forgetSession } = await import("./forgetSession");
    const { queryClient } = await import("./queryClient");
    const { useSessionStore } = await import("../store/session");
    useSessionStore.getState().setSession("tok_1", "ajax");
    queryClient.setQueryData(["me"], { role: "owner" });
    queryClient.setQueryData(["task", "open", "all"], { items: [] });

    forgetSession();

    expect(useSessionStore.getState().token).toBeNull();
    expect(useSessionStore.getState().orgSlug).toBeNull();
    expect(queryClient.getQueryCache().getAll()).toEqual([]);
  });

  it("leaves a newer session alone when an old one's refusal arrives late", async () => {
    const { forgetSessionIfHeld } = await import("./forgetSession");
    const { queryClient } = await import("./queryClient");
    const { useSessionStore } = await import("../store/session");
    useSessionStore.getState().setSession("ses_new", "beta");
    queryClient.setQueryData(["me"], { org: { slug: "beta" } });

    forgetSessionIfHeld("ses_old");
    expect(useSessionStore.getState().token).toBe("ses_new");
    expect(queryClient.getQueryData(["me"])).toEqual({ org: { slug: "beta" } });

    forgetSessionIfHeld("ses_new");
    expect(useSessionStore.getState().token).toBeNull();
    expect(queryClient.getQueryCache().getAll()).toEqual([]);
  });
});
