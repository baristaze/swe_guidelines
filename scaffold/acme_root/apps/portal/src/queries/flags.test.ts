// @vitest-environment jsdom
// The session's flags are one snapshot under a key of the key factory. A
// switch drops it with every other answer of the old tenant, so one org's
// flags never show in another's session, and an open tab reads it again on
// focus and on its interval, so a kill switch reaches it without a reload.
// The shell alone reads it again, so the screens that read a flag add no
// read of their own.
import { focusManager, QueryClientProvider, QueryObserver } from "@tanstack/react-query";
import { act, createElement, type FunctionComponent } from "react";
import { createRoot } from "react-dom/client";
import type { FlagsView } from "@acme/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { adoptSession } from "../app/adoptSession";
import { queryClient, STALE_MS } from "../app/queryClient";
import { useSessionStore } from "../store/session";
import { FLAGS_REFRESH_MS, flagOn, flagsQuery, useFlag, useFlags } from "./flags";
import { keys } from "./keys";

const net = vi.hoisted(() => ({ reads: [] as string[] }));
vi.mock("../app/api", () => ({
  api: {
    get: (path: string) => {
      net.reads.push(path);
      return Promise.resolve({ flags: { "media-uploads": true } } satisfies FlagsView);
    },
  },
}));

vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);

const snapshot = (on: boolean): FlagsView => ({ flags: { "media-uploads": on } });
const org = (slug: string) => ({ id: slug, name: slug, slug, kind: "team" as const, created_at: "2026-09-01T00:00:00Z" });

beforeEach(() => {
  queryClient.clear();
  net.reads.length = 0;
});

afterEach(() => {
  focusManager.setFocused(undefined);
  useSessionStore.getState().clear();
  vi.useRealTimers();
});

describe("the flags snapshot", () => {
  it("is kept under the key factory's key", () => {
    expect(flagsQuery.queryKey).toEqual(keys.flags);
  });

  it("goes with the old tenant's answers on a switch", () => {
    useSessionStore.getState().setSession("ses_ajax", "ajax");
    queryClient.setQueryData(keys.flags, snapshot(false));

    adoptSession({ token: "ses_beta", org: org("beta") });

    expect(queryClient.getQueryData(keys.flags)).toBeUndefined();
  });

  it("is read again on its interval, and when the window regains focus", async () => {
    vi.useFakeTimers();
    queryClient.mount();
    const observer = new QueryObserver(queryClient, flagsQuery);
    const stop = observer.subscribe(() => undefined);
    try {
      await vi.advanceTimersByTimeAsync(0);
      expect(net.reads).toEqual(["/v1/flags"]);

      await vi.advanceTimersByTimeAsync(FLAGS_REFRESH_MS);
      expect(net.reads).toHaveLength(2);

      await vi.advanceTimersByTimeAsync(STALE_MS);
      focusManager.setFocused(false);
      focusManager.setFocused(true);
      await vi.advanceTimersByTimeAsync(0);
      expect(net.reads).toHaveLength(3);
    } finally {
      stop();
      queryClient.unmount();
    }
  });

  it("is read once an interval with the shell and two flag readers mounted", async () => {
    function Shell() {
      useFlags();
      return null;
    }
    function Reader() {
      useFlag("media-uploads");
      return null;
    }
    vi.useFakeTimers();
    const roots: ReturnType<typeof createRoot>[] = [];
    const mount = async (component: FunctionComponent) => {
      const root = createRoot(document.createElement("div"));
      roots.push(root);
      await act(async () =>
        root.render(createElement(QueryClientProvider, { client: queryClient }, createElement(component))),
      );
    };
    const wait = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms));
    try {
      // The screens that read a flag open after the shell, as in the app.
      await mount(Shell);
      await wait(60_000);
      await mount(Reader);
      await wait(60_000);
      await mount(Reader);
      await wait(0);

      const mounted = net.reads.length;
      await wait(FLAGS_REFRESH_MS);
      expect(net.reads).toHaveLength(mounted + 1);
      await wait(FLAGS_REFRESH_MS);
      expect(net.reads).toHaveLength(mounted + 2);
    } finally {
      for (const root of roots) await act(async () => root.render(null));
    }
  });

  it("reads a flag off until the snapshot arrives, and off for a name it does not carry", () => {
    expect(flagOn(undefined, "media-uploads")).toBe(false);
    expect(flagOn({ flags: {} }, "media-uploads")).toBe(false);
    expect(flagOn(snapshot(false), "media-uploads")).toBe(false);
    expect(flagOn(snapshot(true), "media-uploads")).toBe(true);
  });
});
