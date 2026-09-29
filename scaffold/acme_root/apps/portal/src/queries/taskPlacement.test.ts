import { describe, expect, it } from "vitest";
import type { TaskPageView, TaskView } from "../api";
import {
  belongsIn,
  compareDone,
  compareOpen,
  compareRank,
  flatten,
  isVisible,
  microsOf,
  ON_TOP,
  placeTask,
  windowOf,
  type PlacementRules,
  type ScopeLists,
  type TaskPages,
} from "./taskPlacement";

const ME = "me";
const OTHER = "other";

const task = (id: string, overrides: Partial<TaskView> = {}): TaskView => ({
  id,
  title: `task ${id}`,
  notes: "",
  status: "open",
  assignee_id: null,
  rank: "0",
  created_at: "2026-09-20T10:00:00Z",
  updated_at: "2026-09-20T10:00:00Z",
  created_by: ME,
  deleted_at: null,
  archived_at: null,
  due_on: null,
  reminded_at: null,
  version: 1,
  ...overrides,
});

// An open task at a rank.
const open = (id: string, rank: number | string, overrides: Partial<TaskView> = {}) =>
  task(id, { rank: String(rank), ...overrides });
const done = (id: string, minute: number, overrides: Partial<TaskView> = {}) =>
  task(id, { status: "done", updated_at: `2026-09-20T10:${String(minute).padStart(2, "0")}:00Z`, ...overrides });

const page = (items: TaskView[], next_cursor: string | null = null): TaskPageView => ({ items, next_cursor });
const pages = (...list: TaskPageView[]): TaskPages => ({ pages: list, pageParams: list.map((_, i) => (i ? `c${i}` : null)) });
const ids = (data: TaskPages | undefined) => (data ? flatten(data).map((t) => t.id) : undefined);

const team: PlacementRules = { scope: "team", meId: ME, limits: { open: 200, done: 10 } };
const mine: PlacementRules = { ...team, scope: "mine" };
const small: PlacementRules = { ...team, limits: { open: 3, done: 3 } };

describe("the rank", () => {
  it("compares as a number, never as text or as a float", () => {
    expect(compareRank("9", "10")).toBeLessThan(0);
    expect(compareRank("-2", "-10")).toBeGreaterThan(0);
    expect(compareRank("-1.5", "-1.25")).toBeLessThan(0);
    expect(compareRank("0.5", "0.50")).toBe(0);
    expect(compareRank("1.2", "1.15")).toBeGreaterThan(0);
    expect(compareRank("0.1000000000000000000000001", "0.1000000000000000000000002")).toBeLessThan(0);
    expect(compareRank("-0.000000000000000000000000001", "0")).toBeLessThan(0);
  });

  it("orders the open list as the server does: rank, then id", () => {
    const tasks = [
      task("d", { rank: "2" }),
      task("b", { rank: "-1.75" }),
      task("c", { rank: "-1.75" }),
      task("a", { rank: "-10" }),
    ];
    expect([...tasks].sort(compareOpen).map((t) => t.id)).toEqual(["a", "b", "c", "d"]);
  });
});

describe("the server's rules", () => {
  it("shows every task in team, and in mine the ones assigned to me or unassigned and mine", () => {
    expect(isVisible(task("a", { created_by: OTHER, assignee_id: OTHER }), "team", ME)).toBe(true);
    expect(isVisible(task("a", { created_by: OTHER, assignee_id: ME }), "mine", ME)).toBe(true);
    expect(isVisible(task("a", { created_by: ME, assignee_id: OTHER }), "mine", ME)).toBe(false);
    expect(isVisible(task("a", { created_by: ME, assignee_id: null }), "mine", ME)).toBe(true);
    expect(isVisible(task("a", { created_by: OTHER, assignee_id: null }), "mine", ME)).toBe(false);
    expect(isVisible(task("a"), "mine", null)).toBeNull();
  });

  it("keeps archived and deleted tasks out of both lists", () => {
    expect(belongsIn(task("a", { archived_at: "2026-09-20T10:00:00Z" }), "open", team)).toBe(false);
    expect(belongsIn(done("a", 1, { archived_at: "2026-09-20T10:00:00Z" }), "done", team)).toBe(false);
    expect(belongsIn(task("a", { deleted_at: "2026-09-20T10:00:00Z" }), "open", team)).toBe(false);
    expect(belongsIn(task("a"), "done", team)).toBe(false);
  });

  it("orders done by updated_at to the microsecond, then id, newest first", () => {
    expect(microsOf("2026-09-20T10:00:00.000002Z") - microsOf("2026-09-20T10:00:00.000001Z")).toBe(1);
    expect(microsOf("2026-09-20T10:00:00.5Z")).toBe(microsOf("2026-09-20T10:00:00.500000Z"));
    const early = done("a", 1, { updated_at: "2026-09-20T10:00:00.000001Z" });
    const late = done("b", 1, { updated_at: "2026-09-20T10:00:00.000002Z" });
    expect([early, late].sort(compareDone).map((t) => t.id)).toEqual(["b", "a"]);
    expect([done("a", 1), done("b", 1)].sort(compareDone).map((t) => t.id)).toEqual(["b", "a"]);
  });
});

describe("placeTask", () => {
  const lists = (): ScopeLists => ({
    open: pages(page([open("a", 0), open("b", 1), open("c", 2)])),
    done: pages(page([done("x", 30), done("y", 20)])),
  });

  it("inserts a created task where its rank sorts, and leaves done alone", () => {
    const placed = placeTask(lists(), { task: open("n", -1) }, team);
    expect(ids(placed.open.data)).toEqual(["n", "a", "b", "c"]);
    expect(placed.open.outcome).toBe("inserted");
    expect(placed.done.outcome).toBe("unchanged");
    expect(ids(placeTask(lists(), { task: open("n", 1.5) }, team).open.data)).toEqual(["a", "b", "n", "c"]);
  });

  it("breaks a tie on rank by id, as the server does", () => {
    expect(ids(placeTask(lists(), { task: open("ab", 0) }, team).open.data)).toEqual(["a", "ab", "b", "c"]);
  });

  it("places by every digit of the rank, where the floats beside it tie", () => {
    const close = {
      open: pages(page([open("a", "0.1000000000000000000000001"), open("c", "0.1000000000000000000000003")])),
    };
    const placed = placeTask(close, { task: open("b", "0.1000000000000000000000002") }, team);
    expect(ids(placed.open.data)).toEqual(["a", "b", "c"]);
  });

  it("puts a reopen shown before the answer on top", () => {
    const shown = task("y", { rank: ON_TOP, version: 2 });
    const placed = placeTask(lists(), { task: shown }, team, { optimistic: true });
    expect(ids(placed.open.data)).toEqual(["y", "a", "b", "c"]);
  });

  it("moves a ticked task from open to the top of done", () => {
    const placed = placeTask(lists(), { task: done("b", 40, { version: 2 }) }, team);
    expect(ids(placed.open.data)).toEqual(["a", "c"]);
    expect(placed.open.outcome).toBe("removed");
    expect(ids(placed.done.data)).toEqual(["b", "x", "y"]);
    expect(placed.done.outcome).toBe("inserted");
  });

  it("moves a reopened task from done to the top of open", () => {
    const placed = placeTask(lists(), { task: open("y", -1, { version: 2 }) }, team);
    expect(ids(placed.open.data)).toEqual(["y", "a", "b", "c"]);
    expect(ids(placed.done.data)).toEqual(["x"]);
  });

  it("reorders an open task whose rank moved, and replaces one whose title changed", () => {
    const moved = placeTask(lists(), { task: open("a", 1.5, { version: 2 }) }, team);
    expect(ids(moved.open.data)).toEqual(["b", "a", "c"]);
    expect(moved.open.outcome).toBe("reordered");
    const renamed = placeTask(lists(), { task: open("b", 1, { version: 2, title: "renamed" }) }, team);
    expect(renamed.open.outcome).toBe("replaced");
    expect(flatten(renamed.open.data!)[1]!.title).toBe("renamed");
  });

  it("brings an edited done task to the top, since the done list is newest first", () => {
    const placed = placeTask(lists(), { task: done("y", 50, { version: 2, title: "edited" }) }, team);
    expect(ids(placed.done.data)).toEqual(["y", "x"]);
    expect(placed.done.outcome).toBe("reordered");
  });

  it("removes a deleted, an archived, and a gone task", () => {
    expect(ids(placeTask(lists(), { task: open("b", 1, { version: 2, deleted_at: "2026-09-20T11:00:00Z" }) }, team).open.data)).toEqual(["a", "c"]);
    expect(ids(placeTask(lists(), { task: done("x", 30, { version: 2, archived_at: "2026-09-20T11:00:00Z" }) }, team).done.data)).toEqual(["y"]);
    const gone = placeTask(lists(), { gone: "c" }, team);
    expect(ids(gone.open.data)).toEqual(["a", "b"]);
    expect(gone.done.outcome).toBe("unchanged");
  });

  it("follows the mine scope: reassigning away removes, assigning to me inserts", () => {
    const away = placeTask(lists(), { task: open("b", 1, { version: 2, assignee_id: OTHER }) }, mine);
    expect(ids(away.open.data)).toEqual(["a", "c"]);
    const toMe = placeTask(lists(), { task: open("n", 5, { created_by: OTHER, assignee_id: ME }) }, mine);
    expect(ids(toMe.open.data)).toEqual(["a", "b", "c", "n"]);
    const others = placeTask(lists(), { task: open("n", 5, { created_by: OTHER }) }, mine);
    expect(others.open.outcome).toBe("unchanged");
    // The same task goes in the team lists.
    expect(ids(placeTask(lists(), { task: open("n", 5, { created_by: OTHER }) }, team).open.data)).toEqual(["a", "b", "c", "n"]);
  });

  it("reads the list again when mine cannot be decided without the person", () => {
    const placed = placeTask(lists(), { task: open("n", 5) }, { ...mine, meId: null });
    expect(placed.open.outcome).toBe("invalidate");
    // A task that belongs to no list here needs no person to decide.
    expect(placeTask(lists(), { task: done("n", 1) }, { ...mine, meId: null }).open.outcome).toBe("unchanged");
  });

  it("keeps the newer version when an older answer arrives after it", () => {
    const held: ScopeLists = { open: pages(page([open("a", 0, { version: 3 })])) };
    const stale = placeTask(held, { task: done("a", 1, { version: 2 }) }, team);
    expect(stale.open.outcome).toBe("unchanged");
    expect(stale.open.data).toBe(held.open);
    // The same version is not news either: it is the row already held.
    expect(placeTask(held, { task: done("a", 1, { version: 3 }) }, team).open.outcome).toBe("unchanged");
    // A newer one is.
    expect(placeTask(held, { task: done("a", 1, { version: 4 }) }, team).open.outcome).toBe("removed");
  });

  it("places an optimistic edit whatever its version, and the answer replaces it", () => {
    const optimistic = placeTask(lists(), { task: done("b", 40) }, team, { optimistic: true });
    expect(ids(optimistic.done.data)).toEqual(["b", "x", "y"]);
    const after: ScopeLists = { open: optimistic.open.data, done: optimistic.done.data };
    const answer = placeTask(after, { task: done("b", 41, { version: 2 }) }, team);
    expect(answer.done.outcome).toBe("replaced");
    expect(flatten(answer.done.data!)[0]!.version).toBe(2);
  });

  it("keeps a dragged row in place when asked, with the answer's fields", () => {
    const dragged: ScopeLists = { open: pages(page([open("b", 1), open("a", 0), open("c", 2)])) };
    const placed = placeTask(dragged, { task: open("a", 7, { version: 2 }) }, team, { inPlace: true });
    expect(ids(placed.open.data)).toEqual(["b", "a", "c"]);
    expect(flatten(placed.open.data!)[1]!.version).toBe(2);
  });

  it("leaves lists that are not cached alone", () => {
    expect(placeTask({}, { task: open("a", 0) }, team)).toEqual({
      open: { data: undefined, outcome: "unchanged" },
      done: { data: undefined, outcome: "unchanged" },
    });
  });

  describe("at the window's edge", () => {
    // Three rows a page; the last page's cursor names the window's last row.
    const full = (): ScopeLists => ({
      open: pages(page([open("a", 0), open("b", 1), open("c", 2)], "cursor-c")),
      done: pages(page([done("x", 30), done("y", 20), done("z", 10)], "cursor-z")),
    });

    it("leaves out a task that sorts past the edge of a window with more", () => {
      const placed = placeTask(full(), { task: open("n", 9) }, small);
      expect(placed.open.outcome).toBe("unchanged");
      expect(ids(placed.open.data)).toEqual(["a", "b", "c"]);
    });

    it("appends a task past the last row when nothing follows it", () => {
      expect(ids(placeTask(lists(), { task: open("n", 9) }, small).open.data)).toEqual(["a", "b", "c", "n"]);
    });

    it("holds a task inserted into a full done window past the page, which shows its limit", () => {
      const placed = placeTask(full(), { task: done("n", 40) }, small);
      expect(ids(placed.done.data)).toEqual(["n", "x", "y", "z"]);
      // The page shows three; z is still held, so Show more finds it before
      // the cursor, which names z, fetches what comes after.
      expect(windowOf(flatten(placed.done.data!), 1, 3).map((t) => t.id)).toEqual(["n", "x", "y"]);
      expect(windowOf(flatten(placed.done.data!), 1, 3, 1).map((t) => t.id)).toEqual(["n", "x", "y", "z"]);
      expect(placed.done.data!.pages.at(-1)!.next_cursor).toBe("cursor-z");
    });

    it("reads a window with more again when a removal leaves it short", () => {
      const placed = placeTask(full(), { task: open("a", 0, { version: 2, status: "done", updated_at: "2026-09-20T09:00:00Z" }) }, small);
      expect(placed.open.outcome).toBe("invalidate");
      // Done takes it only where it sorts inside its window: here, past it.
      expect(placed.done.outcome).toBe("unchanged");
      expect(placeTask(full(), { gone: "y" }, small).done.outcome).toBe("invalidate");
    });

    it("removes without a read when the window still holds a full page", () => {
      const held = placeTask(full(), { task: done("n", 40) }, small);
      const placed = placeTask({ done: held.done.data }, { gone: "x" }, small);
      expect(placed.done.outcome).toBe("removed");
      expect(ids(placed.done.data)).toEqual(["n", "y", "z"]);
    });

    it("removes without a read when nothing follows the window", () => {
      expect(placeTask(lists(), { gone: "a" }, small).open.outcome).toBe("removed");
    });

    it("reads the list again when the row the cursor names moves", () => {
      expect(placeTask(full(), { task: open("c", -1, { version: 2 }) }, small).open.outcome).toBe("invalidate");
      expect(placeTask(full(), { task: done("z", 50, { version: 2 }) }, small).done.outcome).toBe("invalidate");
      // Edited in place, it stays the edge.
      const renamed = placeTask(full(), { task: open("c", 2, { version: 2, title: "renamed" }) }, small);
      expect(renamed.open.outcome).toBe("replaced");
    });

    it("places across the loaded pages and keeps the last page's cursor", () => {
      const two: ScopeLists = { open: pages(page([open("a", 0), open("b", 1), open("c", 2)], "cursor-c"), page([open("d", 3)], "cursor-d")) };
      const placed = placeTask(two, { task: open("n", 2.5) }, small);
      expect(ids(placed.open.data)).toEqual(["a", "b", "c", "n", "d"]);
      // The whole window rides the first page; the last keeps its cursor.
      expect(placed.open.data!.pages[1]!.items).toEqual([]);
      expect(placed.open.data!.pages[1]!.next_cursor).toBe("cursor-d");
      expect(placeTask(two, { gone: "b" }, small).open.outcome).toBe("invalidate");
    });
  });
});
