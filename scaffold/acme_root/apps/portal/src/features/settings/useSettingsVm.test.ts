// @vitest-environment jsdom
// The view model over a fake transport: the revoke is held open, so a second
// revoke can start while the first is still in flight. A refusal is said.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, createElement, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  ApiError,
  type ApiKeyPageView,
  type ApiKeyView,
  type MembershipPageView,
  type MeView,
  type UserPageView,
} from "@acme/client";
import { useNoticesStore } from "../../store/notices";
import { useSettingsVm, type SettingsVm } from "./useSettingsVm";

interface Held {
  path: string;
  resolve: (value: unknown) => void;
  reject: (cause: unknown) => void;
}

const net = vi.hoisted(() => {
  const reads = new Map<string, unknown>();
  const writes: Held[] = [];
  const read = (path: string) => {
    for (const [prefix, value] of reads) if (path.startsWith(prefix)) return Promise.resolve(value);
    return Promise.reject(new Error(`no read stubbed for ${path}`));
  };
  const hold = (path: string) => new Promise((resolve, reject) => writes.push({ path, resolve, reject }));
  return { reads, writes, read, hold };
});

vi.mock("../../app/api", () => ({
  api: { get: net.read, post: net.hold, patch: net.hold, del: net.hold },
}));

vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
let root: ReturnType<typeof createRoot>;

const me: MeView = {
  app: "portal",
  role: "owner",
  permissions: ["read", "write", "manage_keys"],
  user: { id: "u1", email: "owner@example.test", display_name: "Owner", created_at: "2026-09-01T00:00:00Z" },
  org: { id: "o1", name: "Ajax", slug: "ajax", kind: "team", created_at: "2026-09-01T00:00:00Z", deleted_at: null },
};

const bob = { id: "u2", email: "bob@example.test", display_name: "Bob", created_at: "2026-09-02T00:00:00Z" };

const keyOf = (id: string, name: string): ApiKeyView => ({
  id,
  name,
  role: "member",
  user_id: "u1",
  created_at: "2026-09-01T00:00:00Z",
  expires_at: "2099-09-01T00:00:00Z",
  deleted_at: null,
});

// The view model as the screen sees it, taken after each commit rather than
// during render, so the probe stays a pure component.
const held: { vm?: SettingsVm } = {};
const vm = () => held.vm!;

function Probe() {
  const current = useSettingsVm();
  useEffect(() => {
    held.vm = current;
  });
  return null;
}

const tick = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });

async function mount() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(createElement(QueryClientProvider, { client: queryClient }, createElement(Probe)));
  });
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
  // Before "/v1/me", which is a prefix of it: the first prefix that matches answers.
  net.reads.set("/v1/memberships", {
    items: [
      { id: "m1", user_id: "u1", role: "owner", teams: [] },
      { id: "m2", user_id: "u2", role: "member", teams: [] },
    ],
    next_cursor: null,
  } satisfies MembershipPageView);
  net.reads.set("/v1/me", me);
  net.reads.set("/v1/users", { items: [me.user, bob], next_cursor: null } satisfies UserPageView);
  net.reads.set("/v1/api-keys", {
    items: [keyOf("k1", "first"), keyOf("k2", "second")],
    next_cursor: null,
  } satisfies ApiKeyPageView);
  useNoticesStore.setState({ notices: [] });
});

afterEach(async () => {
  await act(async () => root.render(null));
});

it("says the first revoke was refused even after a second revoke started", async () => {
  await mount();
  await act(async () => void vm().revokeApiKey("k1"));
  await act(async () => void vm().revokeApiKey("k2"));
  expect(net.writes.map((w) => w.path)).toEqual(["/v1/api-keys/k1", "/v1/api-keys/k2"]);
  await act(async () => {
    net.writes[0]!.reject(new ApiError(404, "not_found", "no such key", "req-1"));
    net.writes[1]!.resolve(keyOf("k2", "second"));
  });
  expect(useNoticesStore.getState().notices.map((n) => n.message)).toEqual(["No such key."]);
});

it("says a created key whose secret was lost, instead of showing nothing", async () => {
  await mount();
  await act(async () => vm().setNewKeyName("ci"));
  await act(async () => void vm().createApiKey());
  expect(net.writes.map((w) => w.path)).toEqual(["/v1/api-keys"]);
  await act(async () => {
    net.writes[0]!.resolve({ api_key: keyOf("k3", "ci"), key: null });
  });
  expect(vm().issuedKey).toBeNull();
  expect(useNoticesStore.getState().notices.map((n) => n.message)).toEqual([
    'The key "ci" was created, but its secret was lost on the way back; revoke it and create another.',
  ]);
});

it("shows each member's role, lets an owner give Bob another, and says a refusal", async () => {
  net.reads.set("/v1/me", { ...me, permissions: [...me.permissions, "manage_members"] });
  await mount();
  for (let turn = 0; turn < 20 && vm().members.some((m) => m.role === null); turn += 1) await tick();
  expect(vm().members.map((m) => [m.name, m.role, m.roles.length > 0])).toEqual([
    ["Owner", "owner", false],
    ["Bob", "member", true],
  ]);
  await act(async () => void vm().setRole("u2", "owner"));
  expect(net.writes.map((w) => w.path)).toEqual(["/v1/memberships/u2"]);
  await act(async () => net.writes[0]!.resolve({ id: "m2", user_id: "u2", role: "owner", teams: [] }));
  await act(async () => void vm().setRole("u2", "viewer"));
  await act(async () =>
    net.writes[1]!.reject(new ApiError(403, "not_authorized", "cannot change the role of a member above your own", "req-3")),
  );
  expect(useNoticesStore.getState().notices.map((n) => n.message)).toEqual([
    "Cannot change the role of a member above your own.",
  ]);
});
