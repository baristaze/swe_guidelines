// @vitest-environment jsdom
// Settings holds no signed-in line and no Sign out: those are the account
// menu's. The view models are stubbed; this case is about what the page draws.
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SettingsPage } from "./SettingsPage";

const vm = vi.hoisted(() => ({
  me: undefined,
  loading: false,
  error: null,
  members: [],
  keys: [],
  canManageKeys: false,
}));

vi.mock("./useSettingsVm", () => ({ useSettingsVm: () => vm }));
vi.mock("./useInvitationsVm", () => ({ useInvitationsVm: () => ({ mayManage: false, sso: false, rows: [] }) }));
vi.mock("./useDeleteAccountVm", () => ({ useDeleteAccountVm: () => ({}) }));
vi.mock("./DeleteAccountCard", () => ({ DeleteAccountCard: () => null }));
vi.mock("./useDeleteOrgVm", () => ({ useDeleteOrgVm: () => ({ shown: false }) }));
vi.mock("./StorageCard", () => ({ StorageCard: () => null }));
vi.mock("../../app/AppNav", () => ({ AppNav: () => null }));
vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);

const container = document.createElement("div");
document.body.append(container);
let root: ReturnType<typeof createRoot>;

beforeEach(async () => {
  const router = createMemoryRouter([{ path: "/settings", Component: SettingsPage }], { initialEntries: ["/settings"] });
  root = createRoot(container);
  await act(async () => root.render(createElement(RouterProvider, { router })));
});
afterEach(async () => { await act(async () => root.render(null)); });

it("is titled Settings and opens on the members", () => {
  expect(container.querySelector("h1")!.textContent).toBe("Settings");
  expect(container.querySelector("#members h2")!.textContent).toBe("Members");
});

it("has no signed-in line and no Sign out", () => {
  expect(container.textContent).not.toContain("Signed in");
  expect([...container.querySelectorAll("button")].some((b) => b.textContent === "Sign out")).toBe(false);
});
