// The hint reader over fake effects and fake timers: what it reads for the
// pushes it hears, and what it places, removes, and reads again.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createHints, HINT_BURST, HINT_WINDOW_MAX_MS, HINT_WINDOW_MS, type HintEffects, type Hints } from "./hints";

interface Item {
  id: string;
  version: number;
}

class Gone extends Error {}

/** A server of items, and a record of every effect the reader asked for. */
function fakes(held: Record<string, number> = {}) {
  const server = new Map<string, Item>();
  const reads: string[] = [];
  const placed: { item: Item; since: number }[] = [];
  const removed: { id: string; since: number }[] = [];
  let collectionReads = 0;
  let clock = 0;
  let failing: unknown = null;
  const effects: HintEffects<Item> = {
    read(id) {
      reads.push(id);
      if (failing) return Promise.reject(failing);
      const item = server.get(id);
      return item ? Promise.resolve(item) : Promise.reject(new Gone());
    },
    isGone: (cause) => cause instanceof Gone,
    holds: (id, version) => (held[id] ?? -1) >= version,
    stamp: () => (clock += 1),
    place: (item, since) => placed.push({ item, since }),
    remove: (id, since) => removed.push({ id, since }),
    readCollections: () => (collectionReads += 1),
  };
  return {
    effects,
    server,
    reads,
    placed,
    removed,
    collectionReads: () => collectionReads,
    failWith: (cause: unknown) => (failing = cause),
  };
}

/** Lets a window close and its reads settle. */
const settle = () => vi.advanceTimersByTimeAsync(HINT_WINDOW_MS);

let hints: Hints | null = null;

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  hints?.stop();
  hints = null;
  vi.useRealTimers();
});

describe("a hint", () => {
  it("reads the one record it names and places it", async () => {
    const f = fakes();
    f.server.set("a", { id: "a", version: 3 });
    hints = createHints(f.effects);
    hints.hint("a", 3);
    expect(f.reads).toEqual([]);
    await settle();
    expect(f.reads).toEqual(["a"]);
    expect(f.placed).toEqual([{ item: { id: "a", version: 3 }, since: 1 }]);
    expect(f.collectionReads()).toBe(0);
  });

  it("removes a record the read finds nothing of", async () => {
    const f = fakes();
    hints = createHints(f.effects);
    hints.hint("gone");
    await settle();
    expect(f.reads).toEqual(["gone"]);
    expect(f.placed).toEqual([]);
    expect(f.removed).toEqual([{ id: "gone", since: 1 }]);
    expect(f.collectionReads()).toBe(0);
  });

  it("reads nothing for a version the cache already holds, as the tab that wrote it", async () => {
    const f = fakes({ a: 4 });
    f.server.set("a", { id: "a", version: 4 });
    hints = createHints(f.effects);
    hints.hint("a", 4);
    hints.hint("a", 3);
    await settle();
    expect(f.reads).toEqual([]);
    expect(f.placed).toEqual([]);
  });

  it("reads a record held at an older version, or named with no version", async () => {
    const f = fakes({ a: 4, b: 9 });
    f.server.set("a", { id: "a", version: 5 });
    f.server.set("b", { id: "b", version: 9 });
    hints = createHints(f.effects);
    hints.hint("a", 5);
    hints.hint("b");
    await settle();
    expect([...f.reads].sort()).toEqual(["a", "b"]);
  });

  it("reads the collections once when a read fails for a reason that leaves the place unknown", async () => {
    const f = fakes();
    f.failWith(new Error("timeout"));
    hints = createHints(f.effects);
    hints.hint("a");
    hints.hint("b");
    await settle();
    expect(f.placed).toEqual([]);
    expect(f.removed).toEqual([]);
    expect(f.collectionReads()).toBe(1);
  });
});

describe("hints that arrive together", () => {
  it("read a record named many times once, with the newest version named", async () => {
    const f = fakes({ a: 6 });
    f.server.set("a", { id: "a", version: 7 });
    hints = createHints(f.effects);
    for (const version of [5, 7, 6]) hints.hint("a", version);
    await settle();
    expect(f.reads).toEqual(["a"]);
    expect(f.placed.map((p) => p.item)).toEqual([{ id: "a", version: 7 }]);
  });

  it("read a record once a push with no version joins the window, however new the others", async () => {
    const f = fakes({ a: 7 });
    f.server.set("a", { id: "a", version: 7 });
    hints = createHints(f.effects);
    hints.hint("a", 7);
    hints.hint("a");
    await settle();
    expect(f.reads).toEqual(["a"]);
  });

  it("are read once together: one stamp, and every answer placed after the last is back", async () => {
    const f = fakes();
    for (const id of ["a", "b", "c"]) f.server.set(id, { id, version: 1 });
    hints = createHints(f.effects);
    hints.hint("a");
    await vi.advanceTimersByTimeAsync(HINT_WINDOW_MS - 1);
    hints.hint("b");
    await vi.advanceTimersByTimeAsync(HINT_WINDOW_MS - 1);
    hints.hint("c");
    expect(f.reads).toEqual([]);
    await settle();
    expect(f.reads).toEqual(["a", "b", "c"]);
    expect(f.placed.map((p) => p.since)).toEqual([1, 1, 1]);
  });

  it("are read by the longest window while a steady stream keeps coming", async () => {
    const f = fakes();
    hints = createHints(f.effects);
    for (let at = 0; at < HINT_WINDOW_MAX_MS; at += HINT_WINDOW_MS / 2) {
      expect(f.reads).toEqual([]);
      hints.hint(`r${at}`);
      await vi.advanceTimersByTimeAsync(HINT_WINDOW_MS / 2);
    }
    expect(f.reads).toHaveLength(HINT_WINDOW_MAX_MS / (HINT_WINDOW_MS / 2));
  });

  it("past the bound read the collections once and no record on its own", async () => {
    const f = fakes();
    hints = createHints(f.effects);
    for (let n = 0; n <= HINT_BURST; n += 1) hints.hint(`r${n}`);
    await settle();
    expect(f.reads).toEqual([]);
    expect(f.collectionReads()).toBe(1);
  });

  it("at the bound are still read one by one", async () => {
    const f = fakes();
    hints = createHints(f.effects);
    for (let n = 0; n < HINT_BURST; n += 1) hints.hint(`r${n}`);
    await settle();
    expect(f.reads).toHaveLength(HINT_BURST);
    expect(f.collectionReads()).toBe(0);
  });
});

describe("the reads a client makes", () => {
  it("stay within the bound of each window however many pushes it hears", async () => {
    // A thousand pushes about five records, then a thousand about a thousand
    // records: five reads, then one read of the collections.
    const f = fakes();
    hints = createHints(f.effects);
    for (let n = 0; n < 1000; n += 1) hints.hint(`r${n % 5}`);
    await settle();
    expect(f.reads).toHaveLength(5);
    for (let n = 0; n < 1000; n += 1) hints.hint(`s${n}`);
    await settle();
    expect(f.reads).toHaveLength(5);
    expect(f.collectionReads()).toBe(1);
  });
});

describe("stop", () => {
  it("drops the open window and places no answer still on its way", async () => {
    const f = fakes();
    f.server.set("a", { id: "a", version: 1 });
    hints = createHints(f.effects);
    hints.hint("a");
    hints.stop();
    await settle();
    expect(f.reads).toEqual([]);

    let answer!: (item: Item) => void;
    const slow = fakes();
    slow.effects.read = () => new Promise<Item>((resolve) => (answer = resolve));
    hints = createHints(slow.effects);
    hints.hint("a");
    await settle();
    hints.stop();
    answer({ id: "a", version: 1 });
    await settle();
    expect(slow.placed).toEqual([]);
    hints.hint("b");
    await settle();
    expect(slow.placed).toEqual([]);
  });
});
