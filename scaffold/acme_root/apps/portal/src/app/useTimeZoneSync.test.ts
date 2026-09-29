// @vitest-environment jsdom
// The time zone is recorded once, after sign-in, and only when it differs;
// a failure is dropped without a word.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { IdentityView } from "../api";
import { useTimeZoneSync } from "./useTimeZoneSync";

const net = vi.hoisted(() => ({
  identity: null as unknown,
  patches: [] as { path: string; body: unknown }[],
  fail: false,
}));

vi.mock("./api", () => ({
  api: {
    get: () => Promise.resolve(net.identity),
    patch: (path: string, body: { time_zone: string }) => {
      net.patches.push({ path, body });
      return net.fail
        ? Promise.reject(new Error("refused"))
        : Promise.resolve({ ...(net.identity as IdentityView), time_zone: body.time_zone });
    },
  },
}));
vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);

const identity = (time_zone: string | null): IdentityView => ({
  id: "i1", email: "ann@example.test", operator_role: null, created_at: "2026-09-01T00:00:00Z", time_zone,
});

let root: ReturnType<typeof createRoot>;
const tick = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });

function Probe({ zone }: { zone: string | null }) {
  useTimeZoneSync(() => zone);
  return null;
}

async function mount(zone: string | null) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => root.render(createElement(QueryClientProvider, { client }, createElement(Probe, { zone }))));
  for (let turn = 0; turn < 5; turn += 1) await tick();
}

beforeEach(() => {
  root = createRoot(document.createElement("div"));
  net.patches.length = 0;
  net.fail = false;
});
afterEach(async () => { await act(async () => root.render(null)); });

it("records the browser's zone when the identity holds another", async () => {
  net.identity = identity("Europe/Istanbul");
  await mount("Asia/Tokyo");
  expect(net.patches).toEqual([{ path: "/v1/me/identity", body: { time_zone: "Asia/Tokyo" } }]);
});

it("records it when the identity holds none", async () => {
  net.identity = identity(null);
  await mount("America/New_York");
  expect(net.patches).toEqual([{ path: "/v1/me/identity", body: { time_zone: "America/New_York" } }]);
});

it("sends nothing when the zone is already the identity's", async () => {
  net.identity = identity("Asia/Tokyo");
  await mount("Asia/Tokyo");
  expect(net.patches).toEqual([]);
});

it("drops a refusal without asking again in the same session", async () => {
  net.identity = identity(null);
  net.fail = true;
  await mount("Not/AZone");
  expect(net.patches).toHaveLength(1);
});
