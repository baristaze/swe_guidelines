// @vitest-environment jsdom
// The selection and the change of many tasks over a fake transport: what the
// bar and "Mark all" send, the count the question asks with, the Undo that
// inverts exactly what changed, the plan's bound, and Escape.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, createElement, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { BulkTasksView, TaskScope, TaskView } from "../../api";
import { useNoticesStore } from "../../store/notices";
import { useUpgradeStore } from "../../store/upgrade";
import { useBulkVm, type BulkVm } from "./useBulkVm";

interface Held {
  path: string;
  body: unknown;
  key: string | undefined;
  resolve: (value: unknown) => void;
  reject: (cause: unknown) => void;
}

const net = vi.hoisted(() => {
  const reads = new Map<string, unknown>();
  const writes: Held[] = [];
  const log: string[] = [];
  return {
    reads,
    writes,
    log,
    get: (path: string) => {
      log.push(path);
      for (const [prefix, value] of reads) if (path.startsWith(prefix)) return Promise.resolve(value);
      return Promise.reject(new Error(`no read stubbed for ${path}`));
    },
    post: (path: string, body: unknown, options?: { idempotencyKey?: string }) =>
      new Promise((resolve, reject) => writes.push({ path, body, key: options?.idempotencyKey, resolve, reject })),
  };
});

vi.mock("../../app/api", () => ({ api: { get: net.get, post: net.post } }));
vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);

const taskOf = (id: string, status: "open" | "done" = "open"): TaskView => ({
  id,
  title: id,
  notes: "",
  status,
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

const open = ["t1", "t2", "t3", "t4"].map((id) => taskOf(id));
const done = ["d1", "d2"].map((id) => taskOf(id, "done"));

const held: { vm?: BulkVm } = {};
const vm = () => held.vm!;

function Probe(props: { scope: TaskScope; open: TaskView[]; done: TaskView[]; canWrite: boolean }) {
  const current = useBulkVm(props);
  useEffect(() => {
    held.vm = current;
  });
  return null;
}

let root: ReturnType<typeof createRoot>;
let queryClient: QueryClient;
const render = (props: Partial<Parameters<typeof Probe>[0]> = {}) =>
  act(async () => {
    root.render(
      createElement(
        QueryClientProvider,
        { client: queryClient },
        createElement(Probe, { scope: "team", open, done, canWrite: true, ...props }),
      ),
    );
  });
const tick = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
const answer = (over: Partial<BulkTasksView>): BulkTasksView => ({
  action: "complete",
  changed: [],
  changed_count: 0,
  skipped: [],
  skipped_count: 0,
  plan_limit: null,
  ...over,
});
const notices = () => useNoticesStore.getState().notices;

beforeEach(async () => {
  root = createRoot(document.createElement("div"));
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  held.vm = undefined;
  net.reads.clear();
  net.writes.length = 0;
  net.log.length = 0;
  useNoticesStore.setState({ notices: [] });
  useUpgradeStore.setState({ limit: null });
  await render();
});

afterEach(async () => {
  await act(async () => root.render(null));
});

it("marks the rows picked with ⌘ done in one call, and Undo reopens exactly what changed", async () => {
  await act(async () => vm().select("open", "t3", "toggle"));
  await act(async () => vm().select("open", "t1", "toggle"));
  expect(vm().count).toBe(2);
  expect(vm().action).toBe("Mark done");
  await act(async () => vm().apply());
  expect(net.writes).toHaveLength(1);
  expect(net.writes[0]!.path).toBe("/v1/tasks/bulk");
  expect(net.writes[0]!.body).toEqual({ action: "complete", ids: ["t1", "t3"] });
  expect(net.writes[0]!.key).toBeTruthy();
  expect(vm().action).toBeNull();
  // The server skipped t3, which changed meanwhile, so the undo names t1 alone.
  await act(async () => {
    net.writes[0]!.resolve(
      answer({ changed: ["t1"], changed_count: 1, skipped: [{ id: "t3", reason: "changed" }], skipped_count: 1 }),
    );
  });
  expect(notices().map((n) => [n.message, n.tone, n.action?.label])).toEqual([
    ["1 task marked done, 1 skipped", "done", "Undo"],
  ]);
  await act(async () => useNoticesStore.getState().act(notices()[0]!.id));
  expect(net.writes[1]!.body).toEqual({ action: "reopen", ids: ["t1"] });
  await act(async () => net.writes[1]!.resolve(answer({ action: "reopen", changed: ["t1"], changed_count: 1 })));
  expect(notices().map((n) => [n.message, n.action?.label])).toEqual([["1 task reopened", undefined]]);
});

it("reaches with Shift from the anchor, and undoes a Mark done bottom first", async () => {
  await act(async () => vm().select("open", "t1", "toggle"));
  await act(async () => vm().select("open", "t3", "range"));
  expect(vm().count).toBe(3);
  await act(async () => vm().apply());
  expect(net.writes[0]!.body).toEqual({ action: "complete", ids: ["t1", "t2", "t3"] });
  await act(async () => net.writes[0]!.resolve(answer({ changed: ["t1", "t2", "t3"], changed_count: 3 })));
  await act(async () => useNoticesStore.getState().act(notices()[0]!.id));
  expect(net.writes[1]!.body).toEqual({ action: "reopen", ids: ["t3", "t2", "t1"] });
});

it("selects every task of a section with Select all, counted on the server, and names the list", async () => {
  net.reads.set("/v1/tasks/count?status=open&scope=mine", { status: "open", scope: "mine", count: 40 });
  await render({ scope: "mine" });
  await act(async () => vm().selectAll("open"));
  await tick();
  expect(vm().count).toBe(40);
  expect(vm().isSelected("open", "t4")).toBe(true);
  await act(async () => vm().apply());
  expect(net.writes[0]!.body).toEqual({ action: "complete", all: { scope: "mine", status: "open" } });
});

it("asks Mark all with the section's true count, and sends the whole list once confirmed", async () => {
  net.reads.set("/v1/tasks/count?status=done&scope=team", { status: "done", scope: "team", count: 12 });
  await act(async () => vm().askAll("done"));
  await tick();
  expect(vm().asking!.title).toBe("Reopen 12 tasks?");
  expect(vm().asking!.waiting).toBe(false);
  await act(async () => vm().confirmAll());
  expect(vm().asking).toBeNull();
  expect(net.writes[0]!.body).toEqual({ action: "reopen", all: { scope: "team", status: "done" } });
});

it("opens the upgrade dialog when a reopen met the plan's bound, and still says what it did", async () => {
  await act(async () => vm().select("done", "d1", "toggle"));
  await act(async () => vm().select("done", "d2", "toggle"));
  expect(vm().action).toBe("Reopen");
  await act(async () => vm().apply());
  // Named bottom first, so they keep their order on top of Open.
  expect(net.writes[0]!.body).toEqual({ action: "reopen", ids: ["d2", "d1"] });
  const plan = { lever: "active_tasks", plan: "free", limit: 10, suggested_plan: "pro" };
  await act(async () =>
    net.writes[0]!.resolve(
      answer({
        action: "reopen",
        changed: ["d2"],
        changed_count: 1,
        skipped: [{ id: "d1", reason: "plan_limit" }],
        skipped_count: 1,
        plan_limit: plan,
      }),
    ),
  );
  expect(useUpgradeStore.getState().limit).toEqual(plan);
  expect(notices()[0]!.message).toBe("1 task reopened");
});

it("lets the selection go on Escape, and a row that left its section", async () => {
  await act(async () => vm().select("open", "t1", "toggle"));
  await act(async () => vm().select("open", "t2", "toggle"));
  await render({ open: open.filter((t) => t.id !== "t2") });
  expect(vm().count).toBe(1);
  await act(async () => {
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
  });
  expect(vm().action).toBeNull();
});

it("keeps one section at a time, and picks nothing for someone who cannot write", async () => {
  await act(async () => vm().select("open", "t1", "toggle"));
  await act(async () => vm().select("done", "d1", "toggle"));
  expect(vm().isSelected("open", "t1")).toBe(false);
  expect(vm().action).toBe("Reopen");
  await act(async () => vm().clear());
  await render({ canWrite: false });
  await act(async () => vm().select("open", "t1", "toggle"));
  await act(async () => vm().askAll("open"));
  expect(vm().action).toBeNull();
  expect(vm().asking).toBeNull();
});

it("says a refused change and changes nothing more", async () => {
  await act(async () => vm().select("open", "t1", "toggle"));
  await act(async () => vm().apply());
  await act(async () => net.writes[0]!.reject(new Error("the network went away")));
  expect(notices().map((n) => n.tone)).toEqual(["problem"]);
  expect(net.writes).toHaveLength(1);
});
