// @vitest-environment jsdom
// Settings opens with a way back to the task list above its title, and holds
// no signed-in line and no Sign out: those are the account menu's. The view
// models are stubbed; this case is about what the page draws.
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
  slack: { loading: true, error: null, summary: {}, installed: false, canManage: false },
}));

vi.mock("./useSettingsVm", () => ({ useSettingsVm: () => vm }));
vi.mock("./useInvitationsVm", () => ({ useInvitationsVm: () => ({ mayManage: false, sso: false, rows: [] }) }));
vi.mock("./useDeleteAccountVm", () => ({ useDeleteAccountVm: () => ({}) }));
vi.mock("./DeleteAccountCard", () => ({ DeleteAccountCard: () => null }));
vi.mock("./useDeleteOrgVm", () => ({ useDeleteOrgVm: () => ({ shown: false }) }));
vi.mock("./StorageCard", () => ({ StorageCard: () => null }));
vi.mock("../../app/AppNav", () => ({ AppNav: () => null }));
vi.mock("../billing/PaymentNotice", () => ({ PaymentNotice: () => null }));
vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);

const container = document.createElement("div");
document.body.append(container);
let root: ReturnType<typeof createRoot>;
let router: ReturnType<typeof createMemoryRouter>;

beforeEach(async () => {
  router = createMemoryRouter(
    [
      { path: "/", Component: () => createElement("main", null, "tasks") },
      { path: "/settings", Component: SettingsPage },
    ],
    { initialEntries: ["/settings"] },
  );
  root = createRoot(container);
  await act(async () => root.render(createElement(RouterProvider, { router })));
});
afterEach(async () => { await act(async () => root.render(null)); });

it("puts a way back to Tasks above the title", async () => {
  const back = container.querySelector(".acme-back a") as HTMLAnchorElement;
  expect(back.textContent).toBe("← Tasks");
  expect(back.getAttribute("href")).toBe("/");
  const title = container.querySelector("h1")!;
  expect(title.textContent).toBe("Settings");
  expect(back.compareDocumentPosition(title) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  await act(async () => back.click());
  expect(router.state.location.pathname).toBe("/");
});

it("has no signed-in line and no Sign out", () => {
  expect(container.textContent).not.toContain("Signed in");
  expect([...container.querySelectorAll("button")].some((b) => b.textContent === "Sign out")).toBe(false);
});
