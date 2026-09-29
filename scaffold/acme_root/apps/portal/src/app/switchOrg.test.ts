import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, type IssuedSessionView } from "@acme/client";
import { chipChoices } from "./orgChipModel";
import { switchOrg, type SwitchEffects } from "./switchOrg";

const org = (id: string, name: string) => ({ id, name, slug: id, kind: "team" as const, created_at: "2026-09-01T00:00:00Z" });
const user = { id: "u", email: "ann@example.test", display_name: "Ann", created_at: "2026-09-01T00:00:00Z" };
const issued: IssuedSessionView = {
  token: "ses_new",
  expires_at: "2026-09-02T00:00:00Z",
  org: org("beta", "Beta"),
  user,
  role: "member",
};

function effects(exchange: SwitchEffects["exchange"], refusedWhileHeld = false) {
  const adopt = vi.fn<SwitchEffects["adopt"]>();
  const release = vi.fn(() => refusedWhileHeld);
  return {
    exchange: vi.fn(async () => {
      // The old session must still be held while the server is asked.
      expect(adopt).not.toHaveBeenCalled();
      return exchange();
    }),
    hold: vi.fn(() => ({ release })),
    release,
    adopt,
    forget: vi.fn<SwitchEffects["forget"]>(),
    report: vi.fn<SwitchEffects["report"]>(),
  };
}

describe("switchOrg", () => {
  it("exchanges first, then drops the old tenant and takes up the new session", async () => {
    const e = effects(() => Promise.resolve(issued));
    await expect(switchOrg(e)).resolves.toBe("switched");
    expect(e.adopt).toHaveBeenCalledWith(issued);
    expect(e.report).not.toHaveBeenCalled();
    expect(e.forget).not.toHaveBeenCalled();
  });

  it("holds the session through the exchange and lets go only once the new one is taken up", async () => {
    const e = effects(() => Promise.resolve(issued));
    e.adopt.mockImplementation(() => expect(e.release).not.toHaveBeenCalled());
    await switchOrg(e);
    expect(e.hold).toHaveBeenCalledBefore(e.exchange);
    expect(e.release).toHaveBeenCalledTimes(1);
  });

  it("signs the tab out on a 401 to the exchange", async () => {
    const e = effects(() => Promise.reject(new ApiError(401, "not_authenticated", "session revoked", "r1")));
    await expect(switchOrg(e)).resolves.toBe("signed_out");
    expect(e.adopt).not.toHaveBeenCalled();
    expect(e.forget).toHaveBeenCalledTimes(1);
    expect(e.report).not.toHaveBeenCalled();
  });

  it("signs the tab out when the held session was refused and the exchange then failed", async () => {
    const e = effects(() => Promise.reject(new TypeError("Failed to fetch")), true);
    await expect(switchOrg(e)).resolves.toBe("signed_out");
    expect(e.forget).toHaveBeenCalledTimes(1);
    expect(e.report).not.toHaveBeenCalled();
  });

  it("stays in the current org on any other refusal, and says it", async () => {
    const e = effects(() => Promise.reject(new ApiError(403, "not_authorized", "no longer a member", "r2")));
    await expect(switchOrg(e)).resolves.toBe("refused");
    expect(e.adopt).not.toHaveBeenCalled();
    expect(e.forget).not.toHaveBeenCalled();
    expect(e.report).toHaveBeenCalledWith("No longer a member.");
  });
});

// The tab's session storage, in memory, as forgetSession's own test has it.
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

describe("the switch against the session store", () => {
  beforeEach(() => {
    vi.stubGlobal("sessionStorage", new MemoryStorage());
    vi.stubGlobal("localStorage", new MemoryStorage());
    vi.resetModules();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  async function wired() {
    const { forgetSession, forgetSessionIfHeld, holdSession } = await import("./forgetSession");
    const { adoptSession } = await import("./adoptSession");
    const { useSessionStore } = await import("../store/session");
    useSessionStore.getState().setSession("ses_old", "ajax");
    const seen: (string | null)[] = [];
    useSessionStore.subscribe((state) => seen.push(state.token));
    const run = (exchange: SwitchEffects["exchange"]) =>
      switchOrg({ exchange, hold: holdSession, adopt: adoptSession, forget: forgetSession, report: () => undefined });
    return { forgetSessionIfHeld, useSessionStore, seen, run };
  }

  it("keeps the tab signed in when the old session's socket closes before the answer arrives", async () => {
    const { forgetSessionIfHeld, useSessionStore, seen, run } = await wired();
    // The server ends the old session and closes its socket with 4401 in the
    // same write; the close is read before the exchange's answer.
    const outcome = await run(async () => {
      forgetSessionIfHeld("ses_old");
      return issued;
    });

    expect(outcome).toBe("switched");
    expect(seen).toEqual(["ses_new"]);
    expect(useSessionStore.getState().token).toBe("ses_new");
    // The old session's refusals that come later still say nothing.
    forgetSessionIfHeld("ses_old");
    expect(useSessionStore.getState().token).toBe("ses_new");
  });

  it("signs the tab out when the held session was ended and no new one came back", async () => {
    const { forgetSessionIfHeld, useSessionStore, run } = await wired();
    const outcome = await run(async () => {
      forgetSessionIfHeld("ses_old");
      throw new TypeError("Failed to fetch");
    });

    expect(outcome).toBe("signed_out");
    expect(useSessionStore.getState().token).toBeNull();
  });

  it("acts on a refusal of the held session again once the switch is over", async () => {
    const { forgetSessionIfHeld, useSessionStore, run } = await wired();
    await run(() => Promise.reject(new ApiError(403, "not_authorized", "no", "r3")));
    expect(useSessionStore.getState().token).toBe("ses_old");

    forgetSessionIfHeld("ses_old");
    expect(useSessionStore.getState().token).toBeNull();
  });
});

describe("chipChoices", () => {
  const place = (id: string, name: string) => ({ org: org(id, name), user, role: "member" as const });

  it("offers nothing with one place, or before the lists arrive", () => {
    expect(chipChoices([place("ajax", "Ajax")], "ajax")).toEqual({ canSwitch: false, others: [] });
    expect(chipChoices(undefined, "ajax").canSwitch).toBe(false);
    expect(chipChoices([place("ajax", "Ajax")], undefined).canSwitch).toBe(false);
  });

  it("offers the other places by name, never the current one", () => {
    const choices = chipChoices([place("zeta", "Zeta"), place("ajax", "Ajax"), place("beta", "Beta")], "ajax");
    expect(choices.canSwitch).toBe(true);
    expect(choices.others.map((m) => m.org.name)).toEqual(["Beta", "Zeta"]);
  });
});
