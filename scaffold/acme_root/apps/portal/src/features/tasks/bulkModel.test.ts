import { describe, expect, it } from "vitest";
import type { BulkTasksView } from "../../api";
import { actionLabel, confirmTitle, doneMessage, requestFor, requestForAll, undoRequest } from "./bulkModel";
import { NOTHING, selectAll, toggle } from "./selectionModel";

const answer = (over: Partial<BulkTasksView> = {}): BulkTasksView => ({
  action: "complete",
  changed: ["a", "b", "c"],
  changed_count: 3,
  skipped: [],
  skipped_count: 0,
  plan_limit: null,
  ...over,
});

describe("a change of many tasks", () => {
  it("asks for what is selected: the rows named, or the whole section", () => {
    const shown = ["a", "b", "c"];
    const picked = toggle(toggle(NOTHING, "open", "c", shown), "open", "a", shown);
    expect(requestFor(picked, "team")).toEqual({ action: "complete", ids: ["a", "c"] });
    expect(requestFor(selectAll("open"), "mine")).toEqual({ action: "complete", all: { scope: "mine", status: "open" } });
    expect(requestFor(NOTHING, "team")).toBeNull();
    expect(requestForAll("done", "team")).toEqual({ action: "reopen", all: { scope: "team", status: "done" } });
  });

  it("names a reopen bottom first, so the tasks keep their order on top of Open", () => {
    const shown = ["x", "y", "z"];
    const picked = toggle(toggle(NOTHING, "done", "x", shown), "done", "z", shown);
    expect(requestFor(picked, "team")).toEqual({ action: "reopen", ids: ["z", "x"] });
  });

  it("undoes exactly what changed with the other action", () => {
    expect(undoRequest(answer())).toEqual({ action: "reopen", ids: ["c", "b", "a"] });
    expect(undoRequest(answer({ action: "reopen", changed: ["z", "x"], changed_count: 2 }))).toEqual({
      action: "complete",
      ids: ["z", "x"],
    });
  });

  it("offers no undo when nothing changed or the answer could not name every change", () => {
    expect(undoRequest(answer({ changed: [], changed_count: 0 }))).toBeNull();
    expect(undoRequest(answer({ changed_count: 1500 }))).toBeNull();
  });

  it("says how many changed, and how many were skipped apart from the plan's", () => {
    expect(doneMessage(answer())).toBe("3 tasks marked done");
    expect(doneMessage(answer({ changed: ["a"], changed_count: 1 }))).toBe("1 task marked done");
    expect(
      doneMessage(answer({ skipped: [{ id: "d", reason: "changed" }], skipped_count: 1 })),
    ).toBe("3 tasks marked done, 1 skipped");
    const plan = { lever: "active_tasks", plan: "free", limit: 10, suggested_plan: "pro" };
    expect(
      doneMessage(
        answer({
          action: "reopen",
          changed: ["a", "b"],
          changed_count: 2,
          skipped: [
            { id: "c", reason: "plan_limit" },
            { id: "d", reason: "plan_limit" },
          ],
          skipped_count: 2,
          plan_limit: plan,
        }),
      ),
    ).toBe("2 tasks reopened");
  });

  it("asks with the section's true count", () => {
    expect(confirmTitle("open", 40)).toBe("Mark 40 tasks as done?");
    expect(confirmTitle("open", 1)).toBe("Mark 1 task as done?");
    expect(confirmTitle("done", 12)).toBe("Reopen 12 tasks?");
    expect([actionLabel("open"), actionLabel("done")]).toEqual(["Mark done", "Reopen"]);
  });
});
