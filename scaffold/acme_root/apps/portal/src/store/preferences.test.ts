// The store runs here without a browser: an in-memory Storage twin stands in
// for the origin's local storage.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const KEY = "acme.portal.preferences";
const LEGACY = "acme.portal.taskScope";

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

let local: MemoryStorage;

beforeEach(() => {
  local = new MemoryStorage();
  vi.stubGlobal("localStorage", local);
  vi.resetModules();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("preferences store", () => {
  it("starts on my tasks and keeps the chosen scope in local storage", async () => {
    const { usePreferencesStore } = await import("./preferences");
    expect(usePreferencesStore.getState().taskScope).toBe("mine");
    usePreferencesStore.getState().setTaskScope("team");
    expect(JSON.parse(local.getItem(KEY) as string).state).toEqual({ taskScope: "team", theme: "system", folded: {} });
  });

  it("follows the system until a theme is picked, and keeps the pick", async () => {
    const { usePreferencesStore } = await import("./preferences");
    expect(usePreferencesStore.getState().theme).toBe("system");
    usePreferencesStore.getState().setTheme("dark");
    expect(JSON.parse(local.getItem(KEY) as string).state.theme).toBe("dark");
  });

  it("reads a stored theme back, and an unknown one as the system's", async () => {
    local.setItem(KEY, JSON.stringify({ state: { taskScope: "team", theme: "light" }, version: 0 }));
    let { usePreferencesStore } = await import("./preferences");
    expect(usePreferencesStore.getState().theme).toBe("light");
    vi.resetModules();
    local.setItem(KEY, JSON.stringify({ state: { taskScope: "team", theme: "sepia" }, version: 0 }));
    ({ usePreferencesStore } = await import("./preferences"));
    expect(usePreferencesStore.getState().theme).toBe("system");
    expect(usePreferencesStore.getState().taskScope).toBe("team");
  });

  it("reads the scope back on the next visit", async () => {
    local.setItem(KEY, JSON.stringify({ state: { taskScope: "team" }, version: 0 }));
    const { usePreferencesStore } = await import("./preferences");
    expect(usePreferencesStore.getState().taskScope).toBe("team");
  });

  it("adopts the scope an earlier build kept as a bare word, once", async () => {
    local.setItem(LEGACY, "team");
    const { usePreferencesStore } = await import("./preferences");
    expect(usePreferencesStore.getState().taskScope).toBe("team");
    expect(local.getItem(LEGACY)).toBeNull();
  });

  it("ignores a bare word that is not a scope, and a storage that throws", async () => {
    const { adoptLegacyScope, legacyScope } = await import("./preferences");
    expect(legacyScope("everything")).toBeNull();
    expect(legacyScope(null)).toBeNull();
    const throwing = {
      getItem: () => {
        throw new Error("blocked");
      },
      removeItem: () => undefined,
    };
    expect(adoptLegacyScope(throwing)).toBeNull();
    expect(adoptLegacyScope(undefined)).toBeNull();
  });

  it("folds Done and not Open at first, and keeps each fold per org", async () => {
    const { isFolded, usePreferencesStore } = await import("./preferences");
    const folded = () => usePreferencesStore.getState().folded;
    expect(isFolded(folded(), "org-a", "open")).toBe(false);
    expect(isFolded(folded(), "org-a", "done")).toBe(true);
    expect(isFolded(folded(), null, "done")).toBe(true);
    usePreferencesStore.getState().setFolded("org-a", "done", false);
    usePreferencesStore.getState().setFolded("org-a", "open", true);
    expect(isFolded(folded(), "org-a", "done")).toBe(false);
    expect(isFolded(folded(), "org-a", "open")).toBe(true);
    expect(isFolded(folded(), "org-b", "done")).toBe(true);
    expect(JSON.parse(local.getItem(KEY) as string).state.folded).toEqual({
      "org-a": { done: false, open: true },
    });
  });

  it("reads the folds back on the next visit, and drops folds it cannot read", async () => {
    local.setItem(KEY, JSON.stringify({ state: { taskScope: "team", folded: { "org-a": { done: false } } }, version: 0 }));
    const first = await import("./preferences");
    expect(first.isFolded(first.usePreferencesStore.getState().folded, "org-a", "done")).toBe(false);
    vi.resetModules();
    local.setItem(KEY, JSON.stringify({ state: { folded: "nonsense" }, version: 0 }));
    const second = await import("./preferences");
    expect(second.usePreferencesStore.getState().folded).toEqual({});
  });
});
