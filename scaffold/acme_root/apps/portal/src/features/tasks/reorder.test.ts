import type { TaskView } from "../../api";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api";
import { isStale, reorder, STALE_MESSAGE, type ReorderEffects } from "./reorder";

const task = (id: string, version = 1): TaskView => ({
  id,
  title: `task ${id}`,
  notes: "",
  status: "open",
  assignee_id: null,
  rank: "0",
  created_at: "2026-09-17T10:00:00Z",
  updated_at: "2026-09-17T10:00:00Z",
  created_by: "ann",
  deleted_at: null,
  due_on: null,
  reminded_at: null,
  version,
});

function effects(move: ReorderEffects["move"]) {
  return {
    move: vi.fn(move),
    showOrder: vi.fn<ReorderEffects["showOrder"]>(),
    showMoved: vi.fn<ReorderEffects["showMoved"]>(),
    refetch: vi.fn<ReorderEffects["refetch"]>(),
    report: vi.fn<ReorderEffects["report"]>(),
  };
}

const describeCause = (cause: unknown) => (cause instanceof Error ? cause.message : "unknown");

describe("reorder", () => {
  it("shows the new order at once and moves with the version the task was read at", async () => {
    const open = [task("a", 3), task("b"), task("c")];
    const fx = effects(async () => task("a", 4));
    expect(await reorder(open, "a", "c", "after", fx, describeCause)).toBe("moved");
    expect(fx.showOrder.mock.calls[0]![0]!.map((t) => t.id)).toEqual(["b", "c", "a"]);
    expect(fx.move).toHaveBeenCalledWith("a", "c", 3);
    expect(fx.report).not.toHaveBeenCalled();
    // The answer is the list's news: no list is read again.
    expect(fx.refetch).not.toHaveBeenCalled();
    // The row the server wrote goes back into the list: it carries version 4,
    // so a second drag names that and is not refused as someone else's
    // change.
    expect(fx.showMoved).toHaveBeenCalledWith(task("a", 4));
  });

  it("refuses a stale reorder, says so, and reloads the list", async () => {
    // Another window moved or edited task a after this one listed it: the
    // server answers 412 precondition_failed, and the order shown was never
    // the server's, so the list is fetched again.
    const open = [task("a", 1), task("b"), task("c")];
    const fx = effects(async () => {
      throw new ApiError(412, "precondition_failed", "task a is at version 2, not 1", "req_1");
    });
    expect(await reorder(open, "a", "c", "after", fx, describeCause)).toBe("refused");
    expect(fx.move).toHaveBeenCalledWith("a", "c", 1);
    expect(fx.report).toHaveBeenCalledWith(STALE_MESSAGE);
    expect(fx.refetch).toHaveBeenCalledTimes(1);
    expect(fx.showMoved).not.toHaveBeenCalled();
  });

  it("says any other refusal in the caller's words and still reloads", async () => {
    const open = [task("a"), task("b")];
    const fx = effects(async () => {
      throw new ApiError(422, "validation_failed", "only an open task can be moved", null);
    });
    expect(await reorder(open, "a", "b", "after", fx, describeCause)).toBe("refused");
    expect(fx.report).toHaveBeenCalledWith("only an open task can be moved");
    expect(fx.refetch).toHaveBeenCalledTimes(1);
  });

  it("does nothing for a drop that changes nothing", async () => {
    const open = [task("a"), task("b")];
    const fx = effects(async () => task("a"));
    expect(await reorder(open, "b", "a", "after", fx, describeCause)).toBe("unchanged");
    expect(fx.move).not.toHaveBeenCalled();
    expect(fx.refetch).not.toHaveBeenCalled();
  });

  it("knows a stale write from any other error", () => {
    expect(isStale(new ApiError(412, "precondition_failed", "moved", null))).toBe(true);
    expect(isStale(new ApiError(409, "version_mismatch", "moved", null))).toBe(false);
    expect(isStale(new ApiError(409, "conflict", "other", null))).toBe(false);
    expect(isStale(new Error("offline"))).toBe(false);
  });
});
