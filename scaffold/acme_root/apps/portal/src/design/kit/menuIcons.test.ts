// @vitest-environment jsdom
// A menu item's icon: a fixed slot before the words, hidden from assistive
// technology, so the item's name and role are its words alone. An item with
// no icon has no slot; a radio item keeps its check at the far end.
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Menu, MenuItem, MenuItemRadio, SunIcon, UserIcon } from "./index";

vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);

const container = document.createElement("div");
document.body.append(container);
let root: ReturnType<typeof createRoot>;

beforeEach(async () => {
  root = createRoot(container);
  await act(async () =>
    root.render(
      createElement(Menu, {
        label: "Things",
        trigger: "Open",
        children: [
          createElement(MenuItem, { key: "with", onSelect: () => undefined, icon: createElement(UserIcon), children: "With" }),
          createElement(MenuItem, { key: "without", onSelect: () => undefined, children: "Without" }),
          createElement(MenuItemRadio, {
            key: "light",
            checked: true,
            onSelect: () => undefined,
            icon: createElement(SunIcon),
            children: "Light",
          }),
        ],
      }),
    ),
  );
  await act(async () => container.querySelector("button")!.click());
});
afterEach(async () => {
  await act(async () => root.render(null));
});

const item = (text: string) =>
  [...container.querySelectorAll<HTMLElement>("[role='menuitem'], [role='menuitemradio']")].find((node) => node.textContent === text)!;

it("puts the icon in its slot before the words, hidden from assistive technology", () => {
  const withIcon = item("With");
  const slot = withIcon.firstElementChild!;
  expect(slot.className).toBe("acme-menu-icon");
  expect(slot.getAttribute("aria-hidden")).toBe("true");
  const svg = slot.querySelector("svg")!;
  expect(svg.getAttribute("aria-hidden")).toBe("true");
  expect(svg.getAttribute("width")).toBe("16");
  expect(svg.getAttribute("stroke")).toBe("currentColor");
  expect(withIcon.querySelector(".acme-menu-label")!.textContent).toBe("With");
  expect(withIcon.getAttribute("role")).toBe("menuitem");
  expect(withIcon.getAttribute("aria-label")).toBeNull();
});

it("leaves the slot out when an item has no icon", () => {
  expect(item("Without").querySelector(".acme-menu-icon")).toBeNull();
  expect(item("Without").textContent).toBe("Without");
});

it("keeps a radio item's check at the far end, after the icon and the words", () => {
  const radio = item("Light");
  expect(radio.getAttribute("role")).toBe("menuitemradio");
  expect(radio.getAttribute("aria-checked")).toBe("true");
  const parts = [...radio.children];
  expect(parts.map((part) => part.getAttribute("class") ?? part.tagName.toLowerCase())).toEqual([
    "acme-menu-icon",
    "acme-menu-label",
    "svg",
  ]);
});
