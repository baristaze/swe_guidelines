// @vitest-environment jsdom
// Quick creation is one text box: the title, then Enter or Add. The import
// is an action of its own, outside that form. The sections fold under their
// titles and carry a ⋯ menu; a selection shows the bar; "Mark all" asks
// first. The view models are stubbed; this case is about what the page draws.
import { act, createElement, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TasksPage } from "./TasksPage";

const vm = vi.hoisted(() => ({
  scope: "mine",
  heading: "switch" as "switch" | "alone" | "pending",
  setScope: vi.fn(),
  title: "Call the bank",
  setTitle: () => undefined,
  add: vi.fn(() => Promise.resolve()),
  adding: false,
  canWrite: true,
  loading: true,
  error: null,
  open: [] as { task: { id: string }; row: unknown; leaving: boolean }[],
  done: [] as { task: { id: string }; row: unknown; leaving: boolean }[],
  folded: { open: false, done: true },
  toggleFolded: vi.fn(),
  hasMoreOpen: false,
  hasMoreDone: false,
  editingId: null,
  assigneeOptions: [],
}));

const bulk = vi.hoisted(() => ({
  mounts: 0,
  count: 5 as number | null,
  action: null as string | null,
  busy: false,
  asking: null as null | { section: string; title: string; body: string; confirmLabel: string; waiting: boolean },
  selectingSection: null as string | null,
  isSelected: () => false,
  selecting: (section: string) => bulk.selectingSection === section,
  select: vi.fn(),
  selectAll: vi.fn(),
  clear: vi.fn(),
  apply: vi.fn(),
  askAll: vi.fn(),
  confirmAll: vi.fn(),
  cancelAll: vi.fn(),
}));

const imports = vi.hoisted(() => ({
  canImport: true,
  dialogOpen: false,
  openDialog: vi.fn(),
  closeDialog: () => undefined,
  picked: null,
  pick: () => undefined,
  starting: false,
  failure: null,
  start: () => Promise.resolve(),
  shown: null,
}));

vi.mock("./useTasksVm", () => ({ useTasksVm: () => vm }));
vi.mock("../imports/useImportVm", () => ({ useImportVm: () => imports }));
vi.mock("../../app/AppNav", () => ({ AppNav: () => null }));
vi.mock("../billing/PaymentNotice", () => ({ PaymentNotice: () => null }));
vi.mock("./TaskItem", () => ({ TaskItem: () => null }));
vi.mock("./ArchivedTasks", () => ({ ArchivedTasks: () => null }));
vi.mock("./useBulkVm", () => ({
  useBulkVm: () => {
    // The bulk view model lives with the list of one scope: a remount is a
    // fresh selection.
    useEffect(() => {
      bulk.mounts += 1;
    }, []);
    return bulk;
  },
}));
vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);

let root: ReturnType<typeof createRoot>;
const container = document.createElement("div");
document.body.append(container);

beforeEach(async () => {
  vm.add.mockClear();
  vm.setScope.mockClear();
  vm.toggleFolded.mockClear();
  vm.heading = "switch";
  vm.loading = true;
  vm.scope = "mine";
  vm.folded = { open: false, done: true };
  vm.open = [{ task: { id: "a" }, row: {}, leaving: false }];
  vm.done = [];
  Object.assign(bulk, { mounts: 0, count: 5, action: null, asking: null, selectingSection: null });
  for (const fn of [bulk.selectAll, bulk.clear, bulk.apply, bulk.askAll, bulk.confirmAll, bulk.cancelAll]) fn.mockClear();
  root = createRoot(container);
  await act(async () => root.render(createElement(TasksPage)));
});
afterEach(async () => { await act(async () => root.render(null)); });

it("draws one text box and the Add button, and no date field", () => {
  const form = container.querySelector("form")!;
  const inputs = [...form.querySelectorAll("input")];
  expect(inputs.map((input) => input.getAttribute("aria-label"))).toEqual(["New task"]);
  expect(form.querySelector("input[type=date], input[type=datetime-local]")).toBeNull();
  expect(form.querySelector("button[type=submit]")!.textContent).toBe("Add");
});

it("creates on Enter in the text box", async () => {
  const input = container.querySelector("input[aria-label='New task']") as HTMLInputElement;
  // Enter in a form's only text box submits the form: the browser's implicit
  // submission, which jsdom leaves to the page, so the test does what it does.
  await act(async () => {
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    input.form!.requestSubmit();
  });
  expect(vm.add).toHaveBeenCalledOnce();
});

it("creates on a click of Add", async () => {
  const add = container.querySelector("button[type=submit]") as HTMLButtonElement;
  await act(async () => add.click());
  expect(vm.add).toHaveBeenCalledOnce();
});

it("offers the import beside the lists, never inside the quick-add box", async () => {
  const form = container.querySelector("form")!;
  const importButton = [...container.querySelectorAll("button")].find((b) => b.textContent === "Import")!;
  expect(importButton).toBeDefined();
  expect(form.contains(importButton)).toBe(false);
  await act(async () => importButton.click());
  expect(imports.openDialog).toHaveBeenCalledOnce();
});

it("has no Tasks title: in an org with company the switch is the heading", async () => {
  expect([...container.querySelectorAll("h1")].map((h) => h.textContent)).toEqual(["My tasks"]);
  expect(container.querySelector("h1")!.className).toBe("acme-sr-only");
  const choices = [...container.querySelectorAll("[role='radiogroup'] [role='radio']")];
  expect(choices.map((c) => [c.textContent, c.getAttribute("aria-checked")])).toEqual([
    ["My tasks", "true"],
    ["Team", "false"],
  ]);
  await act(async () => (choices[1] as HTMLButtonElement).click());
  expect(vm.setScope).toHaveBeenCalledWith("team");
});

it("says My tasks, with no switch, when the person is alone in the org", async () => {
  vm.heading = "alone";
  await act(async () => root.render(createElement(TasksPage)));
  const heading = container.querySelector("h1")!;
  expect(heading.textContent).toBe("My tasks");
  expect(heading.className).toBe("acme-title");
  expect(container.querySelector("[role='radiogroup']")).toBeNull();
  expect([...container.querySelectorAll("button")].some((b) => b.textContent === "Import")).toBe(true);
});

describe("the sections", () => {
  const show = async () => {
    vm.loading = false;
    await act(async () => root.render(createElement(TasksPage)));
  };
  const toggles = () => [...container.querySelectorAll<HTMLButtonElement>(".acme-fold-toggle")];
  const button = (text: string) => [...container.querySelectorAll("button")].find((b) => b.textContent === text)!;

  it("fold under their titles: Open shown, Done folded, each title a button that says so", async () => {
    await show();
    expect(toggles().map((t) => [t.textContent, t.getAttribute("aria-expanded")])).toEqual([
      ["Open (1)", "true"],
      ["Done", "false"],
    ]);
    const doneBody = document.getElementById(toggles()[1]!.getAttribute("aria-controls")!)!;
    expect(doneBody.hidden).toBe(true);
    await act(async () => toggles()[1]!.click());
    expect(vm.toggleFolded).toHaveBeenCalledWith("done");
    vm.folded = { open: true, done: false };
    await show();
    expect(toggles().map((t) => t.getAttribute("aria-expanded"))).toEqual(["false", "true"]);
  });

  it("let go of a selection in a section that folds", async () => {
    bulk.selectingSection = "open";
    await show();
    await act(async () => toggles()[0]!.click());
    expect(bulk.clear).toHaveBeenCalledOnce();
    expect(vm.toggleFolded).toHaveBeenCalledWith("open");
  });

  it("carry a ⋯ menu: Select all, and Mark all as done… in red behind a question", async () => {
    await show();
    const more = container.querySelector<HTMLButtonElement>("button[aria-label='More actions for open tasks']")!;
    await act(async () => more.click());
    const items = [...container.querySelectorAll<HTMLButtonElement>("[role='menuitem']")];
    expect(items.map((i) => [i.textContent, i.dataset.tone ?? null])).toEqual([
      ["Select all", null],
      ["Mark all as done…", "danger"],
    ]);
    await act(async () => items[1]!.click());
    expect(bulk.askAll).toHaveBeenCalledWith("open");
    const doneMore = container.querySelector<HTMLButtonElement>("button[aria-label='More actions for done tasks']")!;
    await act(async () => doneMore.click());
    const doneItems = [...container.querySelectorAll<HTMLButtonElement>("[role='menuitem']")];
    expect(doneItems.map((i) => [i.textContent, i.disabled])).toEqual([
      ["Select all", true],
      ["Reopen all…", true],
    ]);
  });

  it("show the bar while anything is selected, with the one action that applies", async () => {
    await show();
    expect(container.querySelector("[role='toolbar']")).toBeNull();
    bulk.action = "Mark done";
    await show();
    const bar = container.querySelector("[role='toolbar']")!;
    expect(bar.getAttribute("aria-label")).toBe("Selected tasks");
    expect(bar.textContent).toContain("5 selected");
    expect([...bar.querySelectorAll("button")].map((b) => b.textContent || b.getAttribute("aria-label"))).toEqual([
      "Mark done",
      "Clear the selection",
    ]);
    await act(async () => button("Mark done").click());
    expect(bulk.apply).toHaveBeenCalledOnce();
    await act(async () => bar.querySelector<HTMLButtonElement>("button[aria-label='Clear the selection']")!.click());
    expect(bulk.clear).toHaveBeenCalledOnce();
    bulk.count = null;
    await show();
    expect(container.querySelector("[role='toolbar']")!.textContent).toContain("Counting…");
    expect(button("Mark done").disabled).toBe(true);
  });

  it("ask before Mark all, with the true count, and let Escape cancel", async () => {
    bulk.asking = { section: "open", title: "Mark 40 tasks as done?", body: "Every open task…", confirmLabel: "Mark all done", waiting: false };
    await show();
    const dialog = container.querySelector("[role='alertdialog']")!;
    expect(dialog.querySelector("h2")!.textContent).toBe("Mark 40 tasks as done?");
    expect(document.activeElement!.textContent).toBe("Cancel");
    await act(async () => button("Mark all done").click());
    expect(bulk.confirmAll).toHaveBeenCalledOnce();
    await act(async () => {
      dialog.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
    expect(bulk.cancelAll).toHaveBeenCalledOnce();
  });

  it("start a fresh selection when the scope switches", async () => {
    await show();
    expect(bulk.mounts).toBe(1);
    vm.scope = "team";
    await show();
    expect(bulk.mounts).toBe(2);
  });
});
