// @vitest-environment jsdom
// A switch lands in the new org on fresh screens: the signed-in shell is
// mounted once per org, so the page on show, its queries, and its state start
// over under the new session. The real routes, shell, home page, and org chip
// run over a fake transport that answers by the token the tab holds; the
// socket is left out, since its reopening is the channel's own test.
import { act, createElement, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { FlagsView, IssuedSessionView, MeView, MembershipChoiceView, OrgView, UserView } from "@acme/client";
import { keys } from "../queries/keys";
import { useSessionStore } from "../store/session";
import { queryClient } from "./queryClient";
import { routes } from "./routes";

interface Call {
  method: "get" | "post";
  path: string;
  token: string | null;
}

const net = vi.hoisted(() => ({
  calls: [] as Call[],
  token: (): string | null => null,
  get: (path: string): Promise<unknown> => Promise.reject(new Error(`no read for ${path}`)),
  post: (path: string, body: unknown): Promise<unknown> => Promise.reject(new Error(`no answer for ${path} ${String(body)}`)),
}));

vi.mock("./api", () => ({
  api: {
    get: (path: string) => {
      net.calls.push({ method: "get", path, token: net.token() });
      return net.get(path);
    },
    post: (path: string, body: unknown) => {
      net.calls.push({ method: "post", path, token: net.token() });
      return net.post(path, body);
    },
    patch: () => Promise.resolve({}),
  },
}));
vi.mock("../realtime/RealtimeProvider", () => ({ RealtimeProvider: ({ children }: { children: ReactNode }) => children }));
vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);

const at = "2026-09-01T00:00:00Z";
const user: UserView = { id: "u1", email: "admin@example.test", display_name: "Admin", created_at: at } as UserView;
const orgOf = (id: string, name: string, slug: string): OrgView =>
  ({ id, name, slug, kind: "team", created_at: at, deleted_at: null }) as OrgView;
const orgs = {
  ajax: orgOf("o_ajax", "Ajax", "ajax"),
  beta: orgOf("o_beta", "Beta", "beta"),
  gamma: orgOf("o_gamma", "Gamma", "gamma"),
};
type Slug = keyof typeof orgs;
const tokenOf = (slug: Slug) => `ses_${slug}`;
const slugOf = (token: string | null) => (Object.keys(orgs) as Slug[]).find((slug) => tokenOf(slug) === token);

function answer(path: string): unknown {
  const slug = slugOf(net.token());
  if (!slug) throw new Error(`read without a session: ${path}`);
  if (path === "/v1/me") {
    return { app: "portal", role: "owner", permissions: ["read", "write"], user, org: orgs[slug] } satisfies MeView;
  }
  if (path.startsWith("/v1/auth/memberships")) {
    const items: MembershipChoiceView[] = [orgs.ajax, orgs.beta].map((org) => ({ org, user, role: "owner" }));
    return { items, next_cursor: null };
  }
  if (path.startsWith("/v1/users")) return { items: [user], next_cursor: null };
  // Uploads are off for Ajax alone, so a snapshot names the org it was read in.
  if (path === "/v1/flags") return { flags: { "media-uploads": slug !== "ajax" } } satisfies FlagsView;
  if (path === "/v1/me/identity") return { id: "i1", email: user.email, operator_role: null, created_at: at, time_zone: null };
  throw new Error(`no read for ${path}`);
}

function exchange(orgId: string): IssuedSessionView {
  const slug = (Object.keys(orgs) as Slug[]).find((key) => orgs[key].id === orgId)!;
  return { token: tokenOf(slug), expires_at: "2099-01-01T00:00:00Z", org: orgs[slug], user, role: "owner" } as IssuedSessionView;
}

net.token = () => useSessionStore.getState().token;
net.get = (path) => {
  try {
    return Promise.resolve(answer(path));
  } catch (cause) {
    return Promise.reject(cause as Error);
  }
};
net.post = (path, body) => {
  if (path === "/v1/auth/sessions") return Promise.resolve(exchange((body as { org_id: string }).org_id));
  if (path === "/v1/orgs") return Promise.resolve({ org: orgs.gamma, user, role: "owner" } satisfies MembershipChoiceView);
  return Promise.reject(new Error(`no answer for ${path}`));
};

const container = document.createElement("div");
document.body.append(container);
let root: ReturnType<typeof createRoot>;
let router: ReturnType<typeof createMemoryRouter>;

const settle = async () => {
  for (let turn = 0; turn < 10; turn += 1) {
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  }
};
/** The reads sent after the exchange that switched the tab. */
const readsAfterSwitch = () => {
  const exchanged = net.calls.map((call) => call.path).lastIndexOf("/v1/auth/sessions");
  expect(exchanged).toBeGreaterThanOrEqual(0);
  return net.calls.slice(exchanged + 1).filter((call) => call.method === "get");
};
/** The org the home card names. */
const shownOrg = () => container.querySelector("#org h2")?.textContent ?? null;
const button = (text: string) =>
  [...container.querySelectorAll("button")].find((node) => node.textContent?.includes(text)) as HTMLButtonElement;

/** The org chip's caret, which opens the switcher; the org's name goes home. */
const switcher = () => container.querySelector("button[aria-label='Switch or create an organization']") as HTMLButtonElement;

async function open(path: string) {
  router = createMemoryRouter(routes, { initialEntries: [path] });
  await act(async () => {
    root.render(createElement(QueryClientProvider, { client: queryClient }, createElement(RouterProvider, { router })));
  });
  await settle();
}

beforeEach(() => {
  queryClient.clear();
  net.calls.length = 0;
  useSessionStore.getState().setSession(tokenOf("ajax"), "ajax");
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.render(null));
  useSessionStore.getState().clear();
});

it("shows the new org's home after a switch from the chip, read once under the new session", async () => {
  await open("/");
  expect(shownOrg()).toBe("Ajax");
  expect(container.querySelector("[data-home]")!.textContent).toContain("1 member");
  expect(queryClient.getQueryData(keys.flags)).toEqual({ flags: { "media-uploads": false } });

  await act(async () => switcher().click());
  await act(async () => button("Beta").click());
  await settle();

  expect(router.state.location.pathname).toBe("/");
  expect(shownOrg()).toBe("Beta");
  const after = readsAfterSwitch();
  expect(after.every((read) => read.token === tokenOf("beta"))).toBe(true);
  expect(after.filter((read) => read.path === "/v1/me")).toHaveLength(1);
  expect(after.filter((read) => read.path.startsWith("/v1/users"))).toHaveLength(1);
  expect(after.filter((read) => read.path === "/v1/flags")).toHaveLength(1);
  expect(queryClient.getQueryData(keys.flags)).toEqual({ flags: { "media-uploads": true } });
});

it("lands on the new org's home after creating one at /orgs/new", async () => {
  await open("/orgs/new");
  const name = container.querySelector("input") as HTMLInputElement;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(name, "Gamma");
    name.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => container.querySelector("form")!.requestSubmit());
  await settle();

  expect(router.state.location.pathname).toBe("/");
  expect(shownOrg()).toBe("Gamma");
  const after = readsAfterSwitch();
  expect(after.every((read) => read.token === tokenOf("gamma"))).toBe(true);
  expect(after.filter((read) => read.path === "/v1/me")).toHaveLength(1);
});
