// Runs without a browser: an in-memory Storage stands in for the tab's
// session storage, as adoptSession's own test does.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { IssuedSessionView } from "../api";
import type { TakeUpEffects } from "./takeUpSignIn";

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

function issued(token: string, slug: string): IssuedSessionView {
  return { token, org: { id: slug, name: slug, slug, kind: "team", created_at: "2026-09-01T00:00:00Z" } } as IssuedSessionView;
}

/** The real store and adoptSession; the logout is a fake that records the
 * token it was made with and what the tab held at that moment. */
async function tab(end: (token: string) => Promise<unknown> = () => Promise.resolve({})) {
  const { adoptSession } = await import("./adoptSession");
  const { takeUpSignIn } = await import("./takeUpSignIn");
  const { useSessionStore } = await import("../store/session");
  const heldWhenEnded: (string | null)[] = [];
  const effects = {
    held: () => useSessionStore.getState().token,
    adopt: adoptSession,
    end: vi.fn<TakeUpEffects["end"]>((token) => {
      heldWhenEnded.push(useSessionStore.getState().token);
      return end(token);
    }),
  };
  return { takeUpSignIn, useSessionStore, effects, heldWhenEnded };
}

describe("takeUpSignIn", () => {
  it("ends nothing on a tab that held no session", async () => {
    const t = await tab();
    await expect(t.takeUpSignIn(issued("ses_first", "ajax"), t.effects)).resolves.toBe("nothing_held");
    expect(t.effects.end).not.toHaveBeenCalled();
    expect(t.useSessionStore.getState().token).toBe("ses_first");
  });

  it("ends the replaced session once, with its own token, after the new one is taken up", async () => {
    const t = await tab();
    await t.takeUpSignIn(issued("ses_first", "ajax"), t.effects);

    await expect(t.takeUpSignIn(issued("ses_second", "beta"), t.effects)).resolves.toBe("ended");

    expect(t.effects.end).toHaveBeenCalledTimes(1);
    expect(t.effects.end).toHaveBeenCalledWith("ses_first");
    expect(t.effects.end).not.toHaveBeenCalledWith("ses_second");
    // The tab already held the new session when the old one was ended.
    expect(t.heldWhenEnded).toEqual(["ses_second"]);
    expect(t.useSessionStore.getState().token).toBe("ses_second");
    expect(t.useSessionStore.getState().orgSlug).toBe("beta");
  });

  it("keeps the new session when the old one was gone already", async () => {
    // The modules are loaded afresh per test, so the error is the class they load.
    const { ApiError } = await import("../api");
    const t = await tab(() => Promise.reject(new ApiError(401, "not_authenticated", "Sign in first.", "req_1")));
    t.useSessionStore.getState().setSession("ses_old", "ajax");

    await expect(t.takeUpSignIn(issued("ses_new", "ajax"), t.effects)).resolves.toBe("already_gone");
    expect(t.useSessionStore.getState().token).toBe("ses_new");
  });

  it("keeps the new session when the server could not end the old one", async () => {
    const t = await tab(() => Promise.reject(new TypeError("Failed to fetch")));
    t.useSessionStore.getState().setSession("ses_old", "ajax");

    await expect(t.takeUpSignIn(issued("ses_new", "ajax"), t.effects)).resolves.toBe("not_ended");
    expect(t.effects.end).toHaveBeenCalledTimes(1);
    expect(t.useSessionStore.getState().token).toBe("ses_new");
  });

  it("takes up the new session before the logout is even answered", async () => {
    let answer: (value: unknown) => void = () => undefined;
    const t = await tab(() => new Promise((resolve) => (answer = resolve)));
    t.useSessionStore.getState().setSession("ses_old", "ajax");

    const ending = t.takeUpSignIn(issued("ses_new", "beta"), t.effects);
    expect(t.useSessionStore.getState().token).toBe("ses_new");
    answer({});
    await expect(ending).resolves.toBe("ended");
  });
});
