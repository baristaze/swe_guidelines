import type { InfiniteData } from "@tanstack/react-query";
import type { MeView, TaskPageView, TaskView, UserView } from "../../api";
import { describe, expect, it } from "vitest";
import {
  canAdd,
  canWrite,
  flattenPages,
  pagesWithOrder,
  placement,
  taskRow,
  withLeaving,
} from "./tasksModel";

const task = (id: string, overrides: Partial<TaskView> = {}): TaskView => ({
  id,
  title: `task ${id}`,
  notes: "",
  status: "open",
  assignee_id: null,
  rank: "0",
  created_at: "2026-09-17T10:00:00Z",
  updated_at: "2026-09-17T10:00:00Z",
  created_by: "ann",
  deleted_at: null,
  due_on: null,
  reminded_at: null,
  version: 1,
  ...overrides,
});

const user = (id: string, name: string): UserView => ({
  id,
  email: `${id}@example.test`,
  display_name: name,
  created_at: "2026-09-01T00:00:00Z",
});

const ids = (tasks: TaskView[] | undefined) => (tasks ?? []).map((t) => t.id);
const page = (...items: TaskView[]): TaskPageView => ({ items, next_cursor: null });
const pages = (...pages: TaskPageView[]): InfiniteData<TaskPageView> => ({
  pages,
  pageParams: pages.map((_, index) => (index === 0 ? null : `cursor-${index}`)),
});

describe("tasks model", () => {
  it("labels who created and who is assigned, naming the caller 'you'", () => {
    const users = new Map([["ann", user("ann", "Ann")], ["bob", user("bob", "Bob")]]);
    expect(taskRow(task("1", { assignee_id: "bob" }), users, "ann")).toMatchObject({
      createdBy: "you",
      assignee: "Bob",
      done: false,
    });
    expect(taskRow(task("2", { created_by: "gone", status: "done" }), users, "ann")).toMatchObject({
      createdBy: "Former member",
      assignee: null,
      done: true,
    });
  });

  it("gates writing and adding", () => {
    const me = { permissions: ["read", "write"] } as unknown as MeView;
    expect(canWrite(me)).toBe(true);
    expect(canWrite({ permissions: ["read"] } as unknown as MeView)).toBe(false);
    expect(canWrite(undefined)).toBe(false);
    expect(canAdd("  migrate DB ")).toBe(true);
    expect(canAdd("   ")).toBe(false);
  });

  it("places a dragged task before or after a target and names the task above it", () => {
    const open = [task("a"), task("b"), task("c"), task("d")];
    expect(placement(open, "d", "a", "before")).toEqual({ order: [open[3], open[0], open[1], open[2]], afterId: null });
    expect(ids(placement(open, "a", "c", "after")?.order)).toEqual(["b", "c", "a", "d"]);
    expect(placement(open, "a", "c", "after")?.afterId).toBe("c");
    expect(placement(open, "c", "a", "after")?.afterId).toBe("a");
    expect(placement(open, "b", "a", "after")).toBeNull(); // already there
    expect(placement(open, "b", "b", "before")).toBeNull();
    expect(placement(open, "x", "a", "before")).toBeNull();
  });

  it("shows a dragged order across the loaded pages", () => {
    const data = pages(page(task("x"), task("y")), page(task("z")));
    // A reorder spans every loaded page: the order lands on the first page
    // and the rest empty, so no task shows twice.
    const reordered = pagesWithOrder(data, [task("z"), task("x"), task("y")]);
    expect(ids(flattenPages(reordered))).toEqual(["z", "x", "y"]);
    expect(reordered?.pages.length).toBe(2);
    expect(pagesWithOrder(undefined, [])).toBeUndefined();
    expect(flattenPages(undefined)).toEqual([]);
  });

  it("keeps a leaving task in its place until the motion ends", () => {
    const open = [task("a"), task("c")];
    const leaving = [{ task: task("b", { status: "done" }), index: 1 }];
    expect(ids(withLeaving(open, leaving))).toEqual(["a", "b", "c"]);
    // A refetch that still has it, or already lost it, renders the same.
    expect(ids(withLeaving([task("a"), task("b"), task("c")], leaving))).toEqual(["a", "b", "c"]);
    expect(ids(withLeaving([], [{ task: task("b"), index: 5 }]))).toEqual(["b"]);
  });
});
