import { QueryClient, type InfiniteData, type QueryKey } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import type { MeView, TaskPageView, TaskView } from "../api";
import { keys } from "./keys";
import { heldTask, placeTask, refreshTaskLists, removeTask, taskStamp } from "./taskCache";

const task = (id: string, overrides: Partial<TaskView> = {}): TaskView => ({
  id,
  title: `task ${id}`,
  notes: "",
  status: "open",
  assignee_id: null,
  rank: "0",
  created_at: "2026-09-20T10:00:00Z",
  updated_at: "2026-09-20T10:00:00Z",
  created_by: "u1",
  deleted_at: null,
  archived_at: null,
  due_on: null,
  reminded_at: null,
  version: 1,
  ...overrides,
});

const me = { user: { id: "u1" } } as MeView;
const listOf = (...items: TaskView[]): InfiniteData<TaskPageView> => ({
  pages: [{ items, next_cursor: null }],
  pageParams: [null],
});

function cache() {
  const queryClient = new QueryClient();
  const invalidated: QueryKey[] = [];
  queryClient.invalidateQueries = (filters) => {
    invalidated.push(filters?.queryKey ?? []);
    return Promise.resolve();
  };
  queryClient.setQueryData(keys.me, me);
  queryClient.setQueryData(keys.tasks.open("team"), listOf(task("a", { rank: "0" }), task("b", { rank: "1" })));
  queryClient.setQueryData(keys.tasks.done("team"), listOf());
  queryClient.setQueryData(keys.tasks.open("mine"), listOf(task("a", { rank: "0" })));
  queryClient.setQueryData(keys.tasks.done("mine"), listOf());
  const ids = (key: QueryKey) =>
    queryClient.getQueryData<InfiniteData<TaskPageView>>(key)!.pages.flatMap((p) => p.items.map((t) => t.id));
  return { queryClient, invalidated, ids };
}

describe("the task cache", () => {
  it("places a write's answer in every cached scope, each by its own rule", () => {
    const { queryClient, invalidated, ids } = cache();
    placeTask(queryClient, task("n", { rank: "-1", created_by: "u2" }));
    expect(ids(keys.tasks.open("team"))).toEqual(["n", "a", "b"]);
    // Someone else's unassigned task is not in mine.
    expect(ids(keys.tasks.open("mine"))).toEqual(["a"]);
    expect(invalidated).toEqual([]);
  });

  it("moves a ticked task to done in every scope, reading no list", () => {
    const { queryClient, invalidated, ids } = cache();
    placeTask(queryClient, task("a", { status: "done", version: 2 }));
    expect(ids(keys.tasks.open("team"))).toEqual(["b"]);
    expect(ids(keys.tasks.done("team"))).toEqual(["a"]);
    expect(ids(keys.tasks.done("mine"))).toEqual(["a"]);
    expect(invalidated).toEqual([]);
  });

  it("drops an answer older than one already placed, even after the task left the lists", () => {
    const { queryClient, ids } = cache();
    placeTask(queryClient, task("a", { deleted_at: "2026-09-20T11:00:00Z", version: 3 }));
    expect(ids(keys.tasks.open("team"))).toEqual(["b"]);
    // A read issued before the delete answers late with the task as it was.
    placeTask(queryClient, task("a", { version: 2 }), { since: 0 });
    expect(ids(keys.tasks.open("team"))).toEqual(["b"]);
  });

  it("keeps a task a newer answer placed when an older read answers 404", () => {
    const { queryClient, ids } = cache();
    const issued = taskStamp(queryClient);
    placeTask(queryClient, task("n", { rank: "5", version: 4 }));
    removeTask(queryClient, "n", { since: issued });
    expect(ids(keys.tasks.open("team"))).toContain("n");
    // A 404 on a read issued after the answer does take it out.
    removeTask(queryClient, "n", { since: taskStamp(queryClient) });
    expect(ids(keys.tasks.open("team"))).not.toContain("n");
  });

  it("does not bring a task back with a read issued before its 404 landed", () => {
    const { queryClient, ids } = cache();
    const issued = taskStamp(queryClient);
    removeTask(queryClient, "b", { since: issued });
    placeTask(queryClient, task("b", { rank: "1", version: 1 }), { since: issued });
    expect(ids(keys.tasks.open("team"))).toEqual(["a"]);
  });

  it("reads again only the list whose window cannot tell", () => {
    const { queryClient, invalidated } = cache();
    queryClient.setQueryData(keys.tasks.done("team"), {
      pages: [{ items: [task("x", { status: "done" })], next_cursor: "more" }],
      pageParams: [null],
    });
    removeTask(queryClient, "x");
    expect(invalidated).toEqual([keys.tasks.done("team")]);
  });

  it("takes a restored task out of the archive and puts it in done", () => {
    const { queryClient, ids } = cache();
    queryClient.setQueryData<TaskPageView>(keys.tasks.archived("team"), {
      items: [task("r", { status: "done", archived_at: "2026-06-01T00:00:00Z" })],
      next_cursor: null,
    });
    placeTask(queryClient, task("r", { status: "done", version: 2, updated_at: "2026-09-20T12:00:00Z" }));
    expect(queryClient.getQueryData<TaskPageView>(keys.tasks.archived("team"))!.items).toEqual([]);
    expect(ids(keys.tasks.done("team"))).toEqual(["r"]);
  });

  it("asks the archive to be read again when a task is archived", () => {
    const { queryClient, invalidated, ids } = cache();
    queryClient.setQueryData<TaskPageView>(keys.tasks.archived("team"), { items: [], next_cursor: null });
    placeTask(queryClient, task("a", { status: "done", archived_at: "2026-09-20T12:00:00Z", version: 2 }));
    expect(invalidated).toEqual([keys.tasks.archived("team")]);
    expect(ids(keys.tasks.open("team"))).toEqual(["b"]);
  });

  it("places a change again once a list read in flight lands, since it may predate the change", async () => {
    const queryClient = new QueryClient();
    queryClient.setQueryData(keys.me, me);
    let answer!: (data: TaskPageView) => void;
    const read = queryClient.fetchInfiniteQuery({
      queryKey: keys.tasks.open("team"),
      initialPageParam: null as string | null,
      queryFn: () => new Promise<TaskPageView>((resolve) => (answer = resolve)),
    });
    placeTask(queryClient, task("n", { rank: "-1" }));
    // The list's answer was read before the task was created.
    answer({ items: [task("a")], next_cursor: null });
    await read;
    const ids = queryClient
      .getQueryData<InfiniteData<TaskPageView>>(keys.tasks.open("team"))!
      .pages.flatMap((p) => p.items.map((t) => t.id));
    expect(ids).toEqual(["n", "a"]);
  });

  it("holds a task a write's answer placed, at that version and every older one", () => {
    const { queryClient } = cache();
    const answer = task("a", { status: "done", version: 2 });
    placeTask(queryClient, answer);
    expect(heldTask(queryClient, "a", 2)).toEqual(answer);
    expect(heldTask(queryClient, "a", 1)).toEqual(answer);
    expect(heldTask(queryClient, "a", 3)).toBeNull();
  });

  it("holds nothing it only loaded in a list or edited optimistically", () => {
    // A list read is not placed, so another scope's list may be older; only
    // a placed answer is in every list.
    const { queryClient } = cache();
    expect(heldTask(queryClient, "b", 1)).toBeNull();
    placeTask(queryClient, task("b", { status: "done", version: 2 }), { optimistic: true });
    expect(heldTask(queryClient, "b", 1)).toBeNull();
  });

  it("holds nothing of a task a 404 took out, even at the version it had", () => {
    const { queryClient } = cache();
    placeTask(queryClient, task("a", { version: 2 }));
    removeTask(queryClient, "a", { since: taskStamp(queryClient) });
    expect(heldTask(queryClient, "a", 2)).toBeNull();
  });

  it("reads every task list once on refresh", () => {
    const { queryClient, invalidated } = cache();
    refreshTaskLists(queryClient);
    expect(invalidated).toEqual([keys.tasks.all]);
  });
});
