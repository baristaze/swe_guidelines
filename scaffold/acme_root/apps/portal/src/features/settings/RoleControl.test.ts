// @vitest-environment jsdom
// The role in the member table: a plain word where the signed-in member may
// not change it, and a menu of the roles they may give where they may, which
// the keyboard opens and moves through, and which asks for the change.
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { RoleControl } from "./RoleControl";
import type { MemberRow } from "./settingsModel";

vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);

const bob: MemberRow = {
  id: "u2",
  name: "Bob",
  email: "bob@example.test",
  joined: "2026-09-02",
  role: "member",
  roles: ["viewer", "member", "admin", "owner"],
};

const container = document.createElement("div");
document.body.append(container);
let root: ReturnType<typeof createRoot>;

beforeEach(() => {
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.render(null));
});

async function draw(row: MemberRow, onChange = vi.fn()) {
  await act(async () => root.render(createElement(RoleControl, { row, onChange, busy: false })));
  return onChange;
}

it("reads the role where it may not be changed", async () => {
  await draw({ ...bob, roles: [] });
  expect(container.querySelector("button")).toBeNull();
  expect(container.textContent).toBe("member");
});

it("offers the roles the caller may give, the current one checked, and asks for the one picked", async () => {
  const onChange = await draw(bob);
  const trigger = container.querySelector("button")!;
  expect(trigger.getAttribute("aria-label")).toBe("Role of Bob: member. Change");
  await act(async () => trigger.click());
  const items = [...container.querySelectorAll('[role="menuitemradio"]')] as HTMLButtonElement[];
  expect(items.map((item) => item.textContent)).toEqual(["viewer", "member", "admin", "owner"]);
  expect(items.map((item) => item.getAttribute("aria-checked"))).toEqual(["false", "true", "false", "false"]);
  await act(async () => items[3]!.click());
  expect(onChange).toHaveBeenCalledWith("u2", "owner");
  await act(async () => items[1]!.click());
  expect(onChange).toHaveBeenCalledTimes(1);
});

it("marks each role with its own icon, hidden from assistive technology", async () => {
  await draw(bob);
  await act(async () => container.querySelector("button")!.click());
  const items = [...container.querySelectorAll('[role="menuitemradio"]')];
  const drawings = items.map((item) => item.querySelector('.acme-menu-icon[aria-hidden="true"] svg')?.innerHTML);
  expect(drawings.every(Boolean)).toBe(true);
  expect(new Set(drawings).size).toBe(4);
});

it("opens from the keyboard and closes on Escape, giving the keyboard back", async () => {
  await draw(bob);
  const trigger = container.querySelector("button")!;
  trigger.focus();
  await act(async () => trigger.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true })));
  const menu = container.querySelector('[role="menu"]')!;
  expect(menu.getAttribute("aria-label")).toBe("Role of Bob");
  expect(document.activeElement?.textContent).toBe("viewer");
  await act(async () => menu.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true })));
  expect(document.activeElement?.textContent).toBe("member");
  await act(async () => menu.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
  expect(container.querySelector('[role="menu"]')).toBeNull();
  expect(document.activeElement).toBe(trigger);
});
