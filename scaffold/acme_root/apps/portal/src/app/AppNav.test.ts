// @vitest-environment jsdom
// The bar above every signed-in page: the org chip goes home, the gear goes
// to Settings, and the account menu holds who is signed in, the theme, and
// Sign out. The real bar, chip, and menu run over a fake transport; the
// pages behind the routes are stand-ins.
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { MeView, OrgView, UserView } from "../api";
import { PREFERENCES_STORAGE_KEY, usePreferencesStore } from "../store/preferences";
import { useSessionStore } from "../store/session";
import { AppNav } from "./AppNav";
import { queryClient } from "./queryClient";

const net = vi.hoisted(() => ({
  posts: [] as { path: string; body: unknown }[],
  logout: { provider_logout_url: null as string | null },
}));

const at = "2026-09-01T00:00:00Z";
const user = { id: "u1", email: "owner@example.test", display_name: "Owner", created_at: at } as UserView;
const org = { id: "o1", name: "Ajax", slug: "ajax", kind: "team", created_at: at, deleted_at: null } as OrgView;

vi.mock("./api", () => ({
  api: {
    get: (path: string) => {
      if (path === "/v1/me") return Promise.resolve({ app: "portal", role: "owner", permissions: ["read"], user, org } as MeView);
      if (path.startsWith("/v1/auth/memberships")) return Promise.resolve({ items: [{ org, user, role: "owner" }], next_cursor: null });
      return Promise.reject(new Error(`no read for ${path}`));
    },
    post: (path: string, body: unknown) => {
      net.posts.push({ path, body });
      if (path === "/v1/auth/logout") return Promise.resolve(net.logout);
      return Promise.reject(new Error(`no answer for ${path}`));
    },
  },
}));
vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);

const container = document.createElement("div");
document.body.append(container);
let root: ReturnType<typeof createRoot>;
let router: ReturnType<typeof createMemoryRouter>;

const page = (name: string) => () => createElement("div", null, createElement(AppNav), createElement("main", null, name));

const settle = async () => {
  for (let turn = 0; turn < 5; turn += 1) {
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  }
};
const q = <T extends Element = HTMLElement>(selector: string) => container.querySelector(selector) as T | null;
const trigger = () => q<HTMLButtonElement>("button[aria-label^='Account']")!;
const menu = () => q("[role='menu'][aria-label='Account']");
const item = (text: string) =>
  [...container.querySelectorAll<HTMLElement>("[role='menuitem'], [role='menuitemradio']")].find((node) => node.textContent === text)!;
const key = (target: Element, name: string) =>
  act(async () => { target.dispatchEvent(new KeyboardEvent("keydown", { key: name, bubbles: true })); });

beforeEach(async () => {
  queryClient.clear();
  net.posts.length = 0;
  net.logout = { provider_logout_url: null };
  localStorage.clear();
  usePreferencesStore.setState({ theme: "system" });
  useSessionStore.getState().setSession("ses_ajax", "ajax");
  router = createMemoryRouter(
    [
      { path: "/", Component: page("home") },
      { path: "/settings", Component: page("settings") },
    ],
    { initialEntries: ["/settings"] },
  );
  root = createRoot(container);
  await act(async () => {
    root.render(createElement(QueryClientProvider, { client: queryClient }, createElement(RouterProvider, { router })));
  });
  await settle();
});
afterEach(async () => {
  await act(async () => root.render(null));
  useSessionStore.getState().clear();
  vi.unstubAllGlobals();
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
});

it("has no tab menu and no theme button, only the chip, the gear, and the account", () => {
  const nav = q("nav")!;
  const links = [...nav.querySelectorAll("a")].map((a) => a.textContent?.trim() || a.getAttribute("aria-label"));
  expect(links).toEqual(["AAjax", "Settings"]);
  expect(nav.querySelector("button[aria-label*='theme']")).toBeNull();
  expect(trigger().textContent).toContain("owner@example.test");
});

it("goes home from the org's name, and to Settings from the gear", async () => {
  const home = [...container.querySelectorAll("a")].find((a) => a.textContent?.includes("Ajax"))!;
  expect(home.getAttribute("href")).toBe("/");
  await act(async () => home.click());
  expect(router.state.location.pathname).toBe("/");

  const gear = q<HTMLAnchorElement>("a[aria-label='Settings']")!;
  expect(gear.getAttribute("title")).toBe("Settings");
  await act(async () => gear.click());
  expect(router.state.location.pathname).toBe("/settings");
  expect(q("a[aria-label='Settings']")!.getAttribute("aria-current")).toBe("page");
});

it("keeps the switcher on the chip's caret", async () => {
  const caret = q<HTMLButtonElement>("button[aria-label='Create an organization']")!;
  await act(async () => caret.click());
  expect(q("[role='menu'][aria-label='Organizations']")).not.toBeNull();
  expect(item("New organization…")).toBeDefined();
});

it("opens the account menu on a click, not on hover, with the email, the org, Settings, the theme, and Sign out", async () => {
  await act(async () => { trigger().dispatchEvent(new MouseEvent("mouseover", { bubbles: true })); });
  expect(menu()).toBeNull();
  await act(async () => trigger().click());
  expect(trigger().getAttribute("aria-expanded")).toBe("true");
  expect(menu()!.textContent).toContain("owner@example.test");
  expect(menu()!.textContent).toContain("Ajax");
  const items = [...menu()!.querySelectorAll("[role^='menuitem']")].map((node) => node.textContent);
  expect(items).toEqual(["Settings", "System", "Light", "Dark", "Sign out"]);
  expect(document.activeElement).toBe(item("Settings"));
});

it("gives each account menu item its icon, and keeps the words as the items' names", async () => {
  await act(async () => trigger().click());
  const items = [...menu()!.querySelectorAll<HTMLElement>("[role^='menuitem']")];
  expect(items.map((node) => node.getAttribute("role"))).toEqual(["menuitem", "menuitemradio", "menuitemradio", "menuitemradio", "menuitem"]);
  expect(items.map((node) => node.textContent)).toEqual(["Settings", "System", "Light", "Dark", "Sign out"]);
  const drawings = items.map((node) => node.querySelector(".acme-menu-icon[aria-hidden='true'] svg[aria-hidden='true']")?.innerHTML);
  expect(drawings.every(Boolean)).toBe(true);
  expect(new Set(drawings).size).toBe(5);
  // The gear in the menu is the gear in the bar.
  expect(drawings[0]).toBe(q("a[aria-label='Settings'] svg")!.innerHTML);
});

it("moves by the arrows, closes on Escape, and gives the keyboard back to its button", async () => {
  trigger().focus();
  await key(trigger(), "ArrowDown");
  expect(menu()).not.toBeNull();
  expect(document.activeElement).toBe(item("Settings"));
  await key(document.activeElement!, "ArrowDown");
  expect(document.activeElement).toBe(item("System"));
  await key(document.activeElement!, "ArrowUp");
  await key(document.activeElement!, "ArrowUp");
  expect(document.activeElement).toBe(item("Sign out"));
  await key(document.activeElement!, "Home");
  expect(document.activeElement).toBe(item("Settings"));
  await key(document.activeElement!, "Escape");
  expect(menu()).toBeNull();
  expect(document.activeElement).toBe(trigger());

  await key(trigger(), "ArrowUp");
  expect(document.activeElement).toBe(item("Sign out"));
});

it("closes on a click anywhere else", async () => {
  await act(async () => trigger().click());
  expect(menu()).not.toBeNull();
  await act(async () => { q("main")!.dispatchEvent(new Event("pointerdown", { bubbles: true })); });
  expect(menu()).toBeNull();
});

it("goes to Settings from the menu", async () => {
  await act(async () => router.navigate("/"));
  await act(async () => trigger().click());
  await act(async () => item("Settings").click());
  expect(router.state.location.pathname).toBe("/settings");
  expect(menu()).toBeNull();
});

it("marks the theme in use, applies a pick, and keeps it across visits", async () => {
  await act(async () => trigger().click());
  expect(item("System").getAttribute("aria-checked")).toBe("true");
  await act(async () => item("Dark").click());
  expect(usePreferencesStore.getState().theme).toBe("dark");
  expect(item("Dark").getAttribute("aria-checked")).toBe("true");
  expect(item("System").getAttribute("aria-checked")).toBe("false");
  expect(JSON.parse(localStorage.getItem(PREFERENCES_STORAGE_KEY)!).state.theme).toBe("dark");
});

it("signs out: the server session first, then the token here", async () => {
  await act(async () => trigger().click());
  await act(async () => item("Sign out").click());
  await settle();
  expect(net.posts.map((post) => post.path)).toEqual(["/v1/auth/logout"]);
  expect(useSessionStore.getState().token).toBeNull();
});

it("goes to the identity provider's logout when the server names one", async () => {
  const assign = vi.fn();
  vi.stubGlobal("location", { origin: "http://localhost:5173", assign });
  net.logout = { provider_logout_url: "https://api.workos.com/user_management/sessions/logout?session_id=s1" };
  await act(async () => trigger().click());
  await act(async () => item("Sign out").click());
  await settle();
  expect(net.posts[0]!.body).toEqual({ return_to: "http://localhost:5173/signed-out" });
  expect(useSessionStore.getState().token).toBeNull();
  expect(assign).toHaveBeenCalledWith("https://api.workos.com/user_management/sessions/logout?session_id=s1");
});
