import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TaskView } from "../api";
import {
  createTaskHints,
  HINT_BURST,
  HINT_WINDOW_MAX_MS,
  HINT_WINDOW_MS,
  ReadAsList,
  type TaskHintEffects,
} from "./taskHints";

const task = (id: string, version = 1) => ({ id, version }) as TaskView;

class Gone extends Error {}

function effects(
  answer: (id: string) => Promise<TaskView> = async (id) => task(id),
  holding: Record<string, TaskView> = {},
) {
  let clock = 7;
  const fx = {
    readTask: vi.fn<TaskHintEffects["readTask"]>(answer),
    // The tab holds each task in `holding` at its version, and nothing else.
    held: vi.fn<TaskHintEffects["held"]>((id, version) => {
      const held = holding[id];
      return held && held.version >= version ? held : null;
    }),
    isGone: (cause: unknown) => cause instanceof Gone,
    stamp: vi.fn(() => clock++),
    place: vi.fn<TaskHintEffects["place"]>(),
    remove: vi.fn<TaskHintEffects["remove"]>(),
    refreshLists: vi.fn<TaskHintEffects["refreshLists"]>(),
  };
  return fx;
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

const settle = () => vi.advanceTimersByTimeAsync(HINT_WINDOW_MS);

describe("createTaskHints", () => {
  it("reads one task per hint and places the answer with the stamp of its issue", async () => {
    const fx = effects();
    const hints = createTaskHints(fx);
    const read = hints.hint("t1");
    expect(fx.readTask).not.toHaveBeenCalled(); // gathered first
    await settle();
    expect(fx.readTask.mock.calls).toEqual([["t1"]]);
    expect(fx.place).toHaveBeenCalledWith(task("t1"), 7);
    expect(fx.refreshLists).not.toHaveBeenCalled();
    await expect(read).resolves.toEqual(task("t1"));
  });

  it("reads a task pushed twice in one window once", async () => {
    const fx = effects();
    const hints = createTaskHints(fx);
    const first = hints.hint("t1");
    const second = hints.hint("t1");
    hints.hint("t2");
    await settle();
    expect(fx.readTask.mock.calls).toEqual([["t1"], ["t2"]]);
    expect(first).toBe(second);
  });

  it("reads a task again when it is pushed in a later window", async () => {
    const fx = effects();
    const hints = createTaskHints(fx);
    hints.hint("t1");
    await settle();
    hints.hint("t1");
    await settle();
    expect(fx.readTask).toHaveBeenCalledTimes(2);
  });

  it("takes a task out when its read answers 404", async () => {
    const fx = effects(async () => {
      throw new Gone();
    });
    const hints = createTaskHints(fx);
    const read = hints.hint("t1");
    await settle();
    expect(fx.remove).toHaveBeenCalledWith("t1", 7);
    expect(fx.place).not.toHaveBeenCalled();
    expect(fx.refreshLists).not.toHaveBeenCalled();
    await expect(read).resolves.toBeNull();
  });

  it("reads the lists once when a read fails for another reason", async () => {
    const fx = effects(async (id) => {
      if (id === "t2") throw new Error("offline");
      return task(id);
    });
    const hints = createTaskHints(fx);
    hints.hint("t1");
    hints.hint("t2");
    hints.hint("t3");
    await settle();
    expect(fx.place).toHaveBeenCalledTimes(2);
    expect(fx.refreshLists).toHaveBeenCalledTimes(1);
  });

  it(`reads single tasks up to ${HINT_BURST} in a window`, async () => {
    const fx = effects();
    const hints = createTaskHints(fx);
    for (let i = 0; i < HINT_BURST; i += 1) hints.hint(`t${i}`);
    await settle();
    expect(fx.readTask).toHaveBeenCalledTimes(HINT_BURST);
    expect(fx.refreshLists).not.toHaveBeenCalled();
  });

  it(`reads the lists once, and no task, past ${HINT_BURST} tasks in a window`, async () => {
    // An import step, or the sweep's respace of a run of ranks.
    const fx = effects();
    const hints = createTaskHints(fx);
    const reads = Array.from({ length: 50 }, (_, i) => hints.hint(`t${i}`));
    await settle();
    expect(fx.readTask).not.toHaveBeenCalled();
    expect(fx.refreshLists).toHaveBeenCalledTimes(1);
    await expect(reads[0]).rejects.toBeInstanceOf(ReadAsList);
  });

  it("keeps the window open while pushes keep coming, so a burst spread over time is one burst", async () => {
    // An import's pushes arrive a few milliseconds apart, over longer than
    // one quiet window.
    const fx = effects();
    const hints = createTaskHints(fx);
    for (let i = 0; i < 50; i += 1) {
      hints.hint(`t${i}`);
      await vi.advanceTimersByTimeAsync(6);
    }
    await settle();
    expect(fx.readTask).not.toHaveBeenCalled();
    expect(fx.refreshLists).toHaveBeenCalledTimes(1);
  });

  it("closes a window at its longest, so a steady stream is still read", async () => {
    const fx = effects();
    const hints = createTaskHints(fx);
    for (let i = 0; i < 10; i += 1) {
      hints.hint(`t${i}`);
      await vi.advanceTimersByTimeAsync(HINT_WINDOW_MAX_MS / 5);
    }
    expect(fx.readTask.mock.calls.length).toBeGreaterThan(0);
    await settle();
    expect(fx.readTask).toHaveBeenCalledTimes(10);
    expect(fx.refreshLists).not.toHaveBeenCalled();
  });

  it("places one window's answers together, after the last read lands", async () => {
    const answers = new Map<string, (task: TaskView) => void>();
    const fx = effects((id) => new Promise((resolve) => answers.set(id, resolve)));
    const hints = createTaskHints(fx);
    hints.hint("t1");
    hints.hint("t2");
    await settle();
    answers.get("t1")!(task("t1"));
    await vi.advanceTimersByTimeAsync(0);
    expect(fx.place).not.toHaveBeenCalled();
    answers.get("t2")!(task("t2"));
    await vi.advanceTimersByTimeAsync(0);
    expect(fx.place.mock.calls.map(([t]) => t.id)).toEqual(["t1", "t2"]);
  });

  it("reads nothing for a push naming a version this tab already holds, and resolves with the held task", async () => {
    // The tab that made the write placed its answer; the push is about it.
    const fx = effects(undefined, { t1: task("t1", 3) });
    const hints = createTaskHints(fx);
    const read = hints.hint("t1", 3);
    await settle();
    expect(fx.readTask).not.toHaveBeenCalled();
    expect(fx.place).not.toHaveBeenCalled();
    await expect(read).resolves.toEqual(task("t1", 3));
  });

  it("reads nothing for a version older than the one held", async () => {
    const fx = effects(undefined, { t1: task("t1", 4) });
    const hints = createTaskHints(fx);
    hints.hint("t1", 3);
    await settle();
    expect(fx.readTask).not.toHaveBeenCalled();
  });

  it("reads a task held at an older version than the push names", async () => {
    // Another tab, or another person, wrote it: this tab has not seen it.
    const fx = effects(async (id) => task(id, 4), { t1: task("t1", 3) });
    const hints = createTaskHints(fx);
    hints.hint("t1", 4);
    await settle();
    expect(fx.readTask.mock.calls).toEqual([["t1"]]);
    expect(fx.place).toHaveBeenCalledWith(task("t1", 4), 7);
  });

  it("reads a task whose push names no version, held or not", async () => {
    const fx = effects(undefined, { t1: task("t1", 3) });
    const hints = createTaskHints(fx);
    hints.hint("t1");
    await settle();
    expect(fx.held).not.toHaveBeenCalled();
    expect(fx.readTask.mock.calls).toEqual([["t1"]]);
  });

  it("asks about the newest version a window named, and reads when any push in it named none", async () => {
    const fx = effects(undefined, { t1: task("t1", 3), t2: task("t2", 3) });
    const hints = createTaskHints(fx);
    hints.hint("t1", 2);
    hints.hint("t1", 4);
    hints.hint("t2", 3);
    hints.hint("t2");
    await settle();
    expect(fx.held.mock.calls).toEqual([["t1", 4]]);
    expect(fx.readTask.mock.calls).toEqual([["t1"], ["t2"]]);
  });

  it("asks when the window closes, so an answer that lands after its push still counts", async () => {
    const holding: Record<string, TaskView> = {};
    const fx = effects(undefined, holding);
    const hints = createTaskHints(fx);
    hints.hint("t1", 2);
    holding.t1 = task("t1", 2); // the write's answer, placed inside the window
    await settle();
    expect(fx.readTask).not.toHaveBeenCalled();
  });

  it(`counts only the tasks it must read toward the burst of ${HINT_BURST}`, async () => {
    const holding = Object.fromEntries(Array.from({ length: 30 }, (_, i) => [`t${i}`, task(`t${i}`, 2)]));
    const fx = effects(undefined, holding);
    const hints = createTaskHints(fx);
    for (let i = 0; i < 30; i += 1) hints.hint(`t${i}`, 2);
    for (let i = 30; i < 35; i += 1) hints.hint(`t${i}`, 2);
    await settle();
    expect(fx.refreshLists).not.toHaveBeenCalled();
    expect(fx.readTask).toHaveBeenCalledTimes(5);
  });

  it("reads nothing once stopped", async () => {
    const fx = effects();
    const hints = createTaskHints(fx);
    const read = hints.hint("t1");
    hints.stop();
    await settle();
    expect(fx.readTask).not.toHaveBeenCalled();
    await expect(read).rejects.toThrow();
  });
});
