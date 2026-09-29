// @vitest-environment jsdom
// The view model over a fake transport: every write is held open, so a second
// write can start while the first is still in flight. That is the case the
// screen hits when someone ticks two tasks in a row, or submits one edit twice.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, createElement, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ApiError, type MeView, type TaskPageView, type TaskView, type UserPageView } from "../../api";
import { heldTask, placeTask, refreshTaskLists, removeTask, taskStamp } from "../../queries/taskCache";
import { fetchTask } from "../../queries/tasks";
import { parseEnvelope } from "../../realtime/envelopes";
import { routeEnvelope } from "../../realtime/router";
import { createTaskHints, HINT_WINDOW_MS } from "../../realtime/taskHints";
import { STALE_MESSAGE } from "./reorder";
import { useTasksVm, type TasksVm } from "./useTasksVm";

interface Held {
  method: string;
  path: string;
  body: unknown;
  /** The version the write names in If-Match, if it names one there. */
  ifMatch: number | undefined;
  resolve: (value: unknown) => void;
  reject: (cause: unknown) => void;
}

const net = vi.hoisted(() => {
  const reads = new Map<string, unknown>();
  const writes: Held[] = [];
  // Every read sent, in order: the lists, one task, the rest.
  const log: string[] = [];
  const read = (path: string) => {
    log.push(path);
    for (const [prefix, value] of reads) {
      if (!path.startsWith(prefix)) continue;
      return value instanceof Error ? Promise.reject(value) : Promise.resolve(value);
    }
    return Promise.reject(new Error(`no read stubbed for ${path}`));
  };
  // A DELETE has no body, so its options come second; every other write's third.
  const hold =
    (method: string) =>
    (path: string, ...rest: unknown[]) => {
      const body = method === "DELETE" ? undefined : rest[0];
      const options = (method === "DELETE" ? rest[0] : rest[1]) as { ifMatch?: number } | undefined;
      return new Promise((resolve, reject) =>
        writes.push({ method, path, body, ifMatch: options?.ifMatch, resolve, reject }),
      );
    };
  return { reads, writes, log, read, hold };
});

vi.mock("../../app/api", () => ({
  api: { get: net.read, post: net.hold("POST"), patch: net.hold("PATCH"), del: net.hold("DELETE") },
}));

vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
let root: ReturnType<typeof createRoot>;

const me: MeView = {
  app: "portal",
  role: "owner",
  permissions: ["read", "write"],
  user: { id: "u1", email: "owner@example.test", display_name: "Owner", created_at: "2026-09-01T00:00:00Z" },
  org: { id: "o1", name: "Ajax", slug: "ajax", kind: "team", created_at: "2026-09-01T00:00:00Z", deleted_at: null },
};

const taskOf = (id: string, title: string): TaskView => ({
  id,
  title,
  notes: "",
  status: "open",
  assignee_id: null,
  rank: "0",
  version: 1,
  created_by: "u1",
  deleted_at: null,
  due_on: null,
  reminded_at: null,
  created_at: "2026-09-20T10:00:00Z",
  updated_at: "2026-09-20T10:00:00Z",
});

const alpha = taskOf("t1", "Alpha");
const beta = taskOf("t2", "Beta");

// The view model as the screen sees it, taken after each commit rather than
// during render, so the probe stays a pure component.
const held: { vm?: TasksVm } = {};
const vm = () => held.vm!;

function Probe() {
  const current = useTasksVm();
  useEffect(() => {
    held.vm = current;
  });
  return null;
}

const tick = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });

let queryClient: QueryClient;

async function mount() {
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      createElement(QueryClientProvider, { client: queryClient }, createElement(Probe)),
    );
  });
  // The two lists and the member walk settle over a few turns of the loop.
  for (let turn = 0; turn < 50 && vm().loading; turn += 1) await tick();
  expect(vm().loading).toBe(false);
}

beforeEach(() => {
  const container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  held.vm = undefined;
  net.reads.clear();
  net.writes.length = 0;
  net.log.length = 0;
  net.reads.set("/v1/me", me);
  net.reads.set("/v1/users", { items: [me.user], next_cursor: null } satisfies UserPageView);
  net.reads.set("/v1/tasks?status=open", { items: [alpha, beta], next_cursor: null } satisfies TaskPageView);
  net.reads.set("/v1/tasks?status=done", { items: [], next_cursor: null } satisfies TaskPageView);
});

afterEach(async () => {
  await act(async () => root.render(null));
});

const refused = () => new ApiError(412, "precondition_failed", "the task changed since it was read", "req-1");

it("says the first tick was refused even after a second tick started", async () => {
  await mount();
  await act(async () => void vm().complete(alpha));
  await act(async () => void vm().complete(beta));
  expect(net.writes.map((w) => `${w.method} ${w.path} ${w.ifMatch}`)).toEqual([
    "PATCH /v1/tasks/t1 1",
    "PATCH /v1/tasks/t2 1",
  ]);
  expect(net.writes[0]!.body).toEqual({ status: "done" });
  await act(async () => {
    net.writes[0]!.reject(refused());
    net.writes[1]!.resolve({ ...beta, status: "done", version: 2 });
  });
  expect(vm().error).toBe(STALE_MESSAGE);
});

it("sends one write for a draft submitted twice before the first answers", async () => {
  await mount();
  const edit = { version: 1, title: "Alpha edited", notes: "", assigneeId: null };
  await act(async () => {
    void vm().save(alpha, edit);
    void vm().save(alpha, edit);
  });
  expect(net.writes).toHaveLength(1);
  expect(vm().saving).toBe(true);
  await act(async () => void net.writes[0]!.resolve({ ...alpha, title: "Alpha edited", version: 2 }));
  expect(vm().saving).toBe(false);
  expect(vm().error).toBeNull();
});

it("says the first delete was refused even after a second delete started", async () => {
  await mount();
  await act(async () => void vm().destroy(alpha));
  await act(async () => void vm().destroy(beta));
  expect(net.writes.map((w) => `${w.method} ${w.path} ${w.ifMatch}`)).toEqual([
    "DELETE /v1/tasks/t1 1",
    "DELETE /v1/tasks/t2 1",
  ]);
  await act(async () => {
    net.writes[0]!.reject(refused());
    net.writes[1]!.resolve({ ...beta, deleted_at: "2026-09-20T10:02:00Z" });
  });
  expect(vm().error).toBe(STALE_MESSAGE);
});

it("sends a due date only when the edit names one, and null to clear it", async () => {
  await mount();
  const base = { version: 1, title: "Alpha", notes: "", assigneeId: null };
  await act(async () => void vm().save(alpha, base));
  expect(net.writes[0]!.body).not.toHaveProperty("due_on");
  expect(net.writes[0]!.body).not.toHaveProperty("remind_at");
  await act(async () => void net.writes[0]!.resolve({ ...alpha, version: 2 }));
  await act(async () => void vm().save(alpha, { ...base, version: 2, dueOn: "2026-09-30" }));
  expect(net.writes[1]!.body).toMatchObject({ due_on: "2026-09-30" });
  expect(net.writes[1]!.body).not.toHaveProperty("remind_at");
  expect(net.writes[1]!.ifMatch).toBe(2);
  await act(async () => void net.writes[1]!.resolve({ ...alpha, version: 3, due_on: "2026-09-30" }));
  await act(async () => void vm().save(alpha, { ...base, version: 3, dueOn: null }));
  expect(net.writes[2]!.body).toMatchObject({ due_on: null });
  await act(async () => void net.writes[2]!.resolve({ ...alpha, version: 4 }));
});

it("says a due date edit was refused when the task changed since", async () => {
  await mount();
  await act(async () =>
    void vm().save(alpha, { version: 1, title: "Alpha", notes: "", assigneeId: null, dueOn: null }),
  );
  await act(async () => void net.writes[0]!.reject(refused()));
  expect(vm().error).toMatch(/changed while you were editing/);
});

it("quick-creates a task from its title alone, with no due date", async () => {
  await mount();
  expect(vm()).not.toHaveProperty("due");
  await act(async () => vm().setTitle("  Call the bank "));
  await act(async () => void vm().add());
  expect(net.writes[0]!.method).toBe("POST");
  expect(net.writes[0]!.body).toEqual({ title: "Call the bank", notes: "" });
  expect(vm().title).toBe("");
  await act(async () => void net.writes[0]!.resolve(taskOf("t3", "Call the bank")));
});

// The realtime side as the provider wires it, over the same query cache.
function hintsOver(client: QueryClient) {
  return createTaskHints({
    readTask: fetchTask,
    held: (id, version) => heldTask(client, id, version),
    isGone: (cause) => cause instanceof ApiError && cause.status === 404,
    stamp: () => taskStamp(client),
    place: (task, since) => placeTask(client, task, { since }),
    remove: (id, since) => removeTask(client, id, { since }),
    refreshLists: () => refreshTaskLists(client),
  });
}

function pushAbout(kind: string, id: string, version?: number) {
  return parseEnvelope(
    JSON.stringify({
      type: "event",
      sent_at: null,
      topic: "entity_changed",
      payload: { kind, target_id: id, seq: 9, actor_id: "u1", ...(version === undefined ? {} : { version }) },
    }),
  )!;
}

const windowPasses = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, HINT_WINDOW_MS + 20));
  });

const listReads = () => net.log.filter((path) => path.startsWith("/v1/tasks?"));

it("ticks with one PATCH and reads nothing on its own push, which names the version the answer holds", async () => {
  await mount();
  net.log.length = 0;
  const hints = hintsOver(queryClient);
  await act(async () => void vm().complete(alpha));
  await tick();
  const completed: TaskView = { ...alpha, status: "done", version: 2, updated_at: "2026-09-20T10:05:00Z" };
  await act(async () => void net.writes[0]!.resolve(completed));
  await tick();
  routeEnvelope(queryClient, pushAbout("tasks.task.updated", "t1", 2), hints);
  await windowPasses();
  expect(net.writes.map((w) => `${w.method} ${w.path}`)).toEqual(["PATCH /v1/tasks/t1"]);
  expect(net.log).toEqual([]);
  expect(vm().done.map((r) => r.task.id)).toEqual(["t1"]);
  hints.stop();
});

it("reads nothing when its own push arrives before the write's answer, within the window", async () => {
  await mount();
  net.log.length = 0;
  const hints = hintsOver(queryClient);
  await act(async () => void vm().complete(alpha));
  await tick();
  routeEnvelope(queryClient, pushAbout("tasks.task.updated", "t1", 2), hints);
  const completed: TaskView = { ...alpha, status: "done", version: 2, updated_at: "2026-09-20T10:05:00Z" };
  await act(async () => void net.writes[0]!.resolve(completed));
  await windowPasses();
  expect(net.log).toEqual([]);
  expect(vm().done[0]!.task.version).toBe(2);
  hints.stop();
});

it("ticks with one PATCH, reads that one task on a push that names no version, and reads no list", async () => {
  await mount();
  net.log.length = 0;
  const hints = hintsOver(queryClient);
  await act(async () => void vm().complete(alpha));
  await tick();
  const completed: TaskView = { ...alpha, status: "done", version: 2, updated_at: "2026-09-20T10:05:00Z" };
  await act(async () => void net.writes[0]!.resolve(completed));
  await tick();
  // The answer is in the done list before any push arrives.
  expect(vm().done.map((r) => r.task.id)).toEqual(["t1"]);
  expect(vm().done[0]!.task.version).toBe(2);
  // The write's own push comes back, and reads the task once.
  net.reads.set("/v1/tasks/t1", completed);
  routeEnvelope(queryClient, pushAbout("tasks.task.updated", "t1"), hints);
  await windowPasses();
  expect(net.writes.map((w) => `${w.method} ${w.path}`)).toEqual(["PATCH /v1/tasks/t1"]);
  expect(net.log).toEqual(["/v1/tasks/t1"]);
  expect(listReads()).toEqual([]);
  expect(vm().done.map((r) => r.task.id)).toEqual(["t1"]);
  hints.stop();
});

it("writes create, edit, reopen, move, and delete answers into the lists without reading one", async () => {
  await mount();
  net.log.length = 0;
  await act(async () => vm().setTitle("Gamma"));
  await act(async () => void vm().add());
  await tick();
  await act(async () => void net.writes[0]!.resolve({ ...taskOf("t3", "Gamma"), rank: "-1" }));
  await tick();
  expect(vm().open.map((r) => r.task.id)).toEqual(["t3", "t1", "t2"]);

  await act(async () => void vm().save(beta, { version: 1, title: "Beta edited", notes: "", assigneeId: null }));
  await tick();
  await act(async () => void net.writes[1]!.resolve({ ...beta, title: "Beta edited", version: 2 }));
  await tick();
  expect(vm().open.find((r) => r.task.id === "t2")!.row.title).toBe("Beta edited");

  await act(async () => void vm().drop("t3", "t2", "after"));
  await tick();
  await act(async () => void net.writes[2]!.resolve({ ...taskOf("t3", "Gamma"), rank: "1", version: 2 }));
  await tick();
  expect(vm().open.map((r) => r.task.id)).toEqual(["t1", "t2", "t3"]);

  const doneBeta: TaskView = { ...beta, title: "Beta edited", status: "done", version: 3, updated_at: "2026-09-20T10:06:00Z" };
  queryClient.setQueryData(["task", "done", "team"], { pages: [{ items: [doneBeta], next_cursor: null }], pageParams: [null] });
  queryClient.setQueryData(["task", "open", "team"], {
    pages: [{ items: [alpha, { ...taskOf("t3", "Gamma"), rank: "1", version: 2 }], next_cursor: null }],
    pageParams: [null],
  });
  await act(async () => void vm().reopen(doneBeta));
  await tick();
  expect(vm().done).toEqual([]);
  await act(async () => void net.writes[3]!.resolve({ ...doneBeta, status: "open", rank: "-2", version: 4 }));
  await tick();
  expect(vm().open.map((r) => r.task.id)).toEqual(["t2", "t1", "t3"]);

  await act(async () => void vm().destroy(alpha));
  await tick();
  expect(vm().open.map((r) => r.task.id)).toEqual(["t2", "t3"]);
  await act(async () => void net.writes[4]!.resolve({ ...alpha, deleted_at: "2026-09-20T10:07:00Z", version: 2 }));
  await tick();
  expect(vm().open.map((r) => r.task.id)).toEqual(["t2", "t3"]);

  expect(net.writes.map((w) => w.method)).toEqual(["POST", "PATCH", "POST", "PATCH", "DELETE"]);
  expect(net.log).toEqual([]);
});

it("updates from someone else's push with one read of that task, and drops a task that answers 404", async () => {
  await mount();
  net.log.length = 0;
  const hints = hintsOver(queryClient);
  net.reads.set("/v1/tasks/t2", { ...beta, title: "Beta, from the other tab", version: 2 });
  // This tab holds version 1; the push names 2, so it reads.
  routeEnvelope(queryClient, pushAbout("tasks.task.updated", "t2", 2), hints);
  await windowPasses();
  expect(vm().open.map((r) => r.row.title)).toEqual(["Alpha", "Beta, from the other tab"]);
  expect(net.log).toEqual(["/v1/tasks/t2"]);

  net.reads.set("/v1/tasks/t1", new ApiError(404, "not_found", "task t1 not found", "req-2"));
  routeEnvelope(queryClient, pushAbout("tasks.task.deleted", "t1"), hints);
  await windowPasses();
  expect(vm().open.map((r) => r.task.id)).toEqual(["t2"]);
  expect(listReads()).toEqual([]);
  hints.stop();
});

it("reads the lists once for a burst of pushes, not task by task", async () => {
  await mount();
  net.log.length = 0;
  const hints = hintsOver(queryClient);
  for (let i = 0; i < 50; i += 1) routeEnvelope(queryClient, pushAbout("tasks.task.created", `n${i}`), hints);
  await windowPasses();
  expect(net.log.filter((path) => path.startsWith("/v1/tasks/"))).toEqual([]);
  // One read of each list the page shows.
  expect(listReads().map((path) => path.split("&")[0])).toEqual(["/v1/tasks?status=open", "/v1/tasks?status=done"]);
  hints.stop();
});
