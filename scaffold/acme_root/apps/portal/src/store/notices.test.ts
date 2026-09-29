import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NOTICE_TTL_MS, notify, UNDO_TTL_MS, useNoticesStore } from "./notices";

beforeEach(() => {
  vi.useFakeTimers();
  useNoticesStore.setState({ notices: [] });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("notices store", () => {
  it("keeps what it is told, in order, until each is dismissed", () => {
    const first = notify("The key was not created.");
    const second = notify("The key was not revoked.");
    expect(useNoticesStore.getState().notices.map((n) => n.message)).toEqual([
      "The key was not created.",
      "The key was not revoked.",
    ]);
    useNoticesStore.getState().dismiss(first);
    expect(useNoticesStore.getState().notices.map((n) => n.id)).toEqual([second]);
  });

  it("lets a notice go on its own after a while", () => {
    notify("gone soon");
    vi.advanceTimersByTime(NOTICE_TTL_MS - 1);
    expect(useNoticesStore.getState().notices).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(useNoticesStore.getState().notices).toHaveLength(0);
  });

  it("gives every notice its own id", () => {
    expect(notify("a")).not.toBe(notify("a"));
  });

  it("says a problem unless told it is a change done", () => {
    notify("The key was not created.");
    notify("3 tasks marked done", { tone: "done" });
    expect(useNoticesStore.getState().notices.map((n) => n.tone)).toEqual(["problem", "done"]);
  });

  it("runs its action once and lets the notice go, and stays as long as it is told", () => {
    const run = vi.fn();
    const id = notify("3 tasks marked done", { tone: "done", action: { label: "Undo", run }, ttlMs: UNDO_TTL_MS });
    vi.advanceTimersByTime(NOTICE_TTL_MS);
    expect(useNoticesStore.getState().notices).toHaveLength(1);
    useNoticesStore.getState().act(id);
    useNoticesStore.getState().act(id);
    expect(run).toHaveBeenCalledOnce();
    expect(useNoticesStore.getState().notices).toHaveLength(0);
  });

  it("lets an undo go on its own after its while, never having run it", () => {
    const run = vi.fn();
    notify("3 tasks reopened", { tone: "done", action: { label: "Undo", run }, ttlMs: UNDO_TTL_MS });
    vi.advanceTimersByTime(UNDO_TTL_MS);
    expect(useNoticesStore.getState().notices).toHaveLength(0);
    expect(run).not.toHaveBeenCalled();
  });
});
