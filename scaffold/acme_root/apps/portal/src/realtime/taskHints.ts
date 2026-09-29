// A push about a task is a hint: it names the task and the version its change
// wrote, never its fields. The page reads that one task through the
// authorized read and places the answer into the lists it holds; a 404
// (deleted, or no longer this org's to see) takes it out. A tab that already
// holds the task at that version reads nothing: the tab that made the write
// placed the write's answer, and the push is about that answer. Hints are
// gathered while they keep coming, so a task pushed twice is read once, and a
// burst reads the lists once instead of task by task. Everything it reaches
// for is handed in, so it runs in a test with fake timers and no query cache.
import type { TaskView } from "../api";

/** How long a window stays open after its last hint. The pushes of one
 * write that touches many tasks (an import step, a respace) arrive a few
 * milliseconds apart, and a person's own writes seconds apart. */
export const HINT_WINDOW_MS = 100;

/** How long a window stays open at most, so a steady stream of pushes is
 * still read while it lasts. */
export const HINT_WINDOW_MAX_MS = 500;

/** Past this many distinct tasks in one window, the lists are read once
 * instead of each task on its own. An import step lands up to a hundred
 * rows, and the sweep's respace up to two hundred and one; twenty single
 * reads are already more than the two list reads a refresh costs. */
export const HINT_BURST = 20;

/** A hint that was answered by reading the lists, not the task. */
export class ReadAsList extends Error {
  constructor() {
    super("the lists were read instead of the task");
  }
}

export interface TaskHintEffects {
  /** The authorized read of one task. */
  readTask(id: string): Promise<TaskView>;
  /** The task as this tab holds it, when that is at or past `version`;
   * otherwise null. */
  held(id: string, version: number): TaskView | null;
  /** Whether a failed read says the task is gone (a 404). */
  isGone(cause: unknown): boolean;
  /** The cache's clock when a read is issued; handed back with its answer. */
  stamp(): number;
  place(task: TaskView, since: number): void;
  remove(id: string, since: number): void;
  /** Reads every task list again, once. */
  refreshLists(): void;
}

export interface TaskHints {
  /** Notes a push about one task, with the version its change wrote when the
   * push names one. Resolves with the task read (or held), or null when it is
   * gone; rejects with ReadAsList when a burst was read as lists instead. */
  hint(id: string, version?: number): Promise<TaskView | null>;
  stop(): void;
}

interface Waiter {
  /** The newest version the window's pushes named; null once a push named
   * none, since then only a read can tell. */
  version: number | null;
  promise: Promise<TaskView | null>;
  resolve(task: TaskView | null): void;
  reject(cause: unknown): void;
}

function waiter(version: number | null): Waiter {
  let resolve!: (task: TaskView | null) => void;
  let reject!: (cause: unknown) => void;
  const promise = new Promise<TaskView | null>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  // A hint nobody awaits must not surface as an unhandled rejection.
  promise.catch(() => undefined);
  return { version, promise, resolve, reject };
}

export function createTaskHints(effects: TaskHintEffects): TaskHints {
  let gathered = new Map<string, Waiter>();
  let quiet: ReturnType<typeof setTimeout> | null = null;
  let longest: ReturnType<typeof setTimeout> | null = null;
  let stopped = false;

  const close = () => {
    if (quiet) clearTimeout(quiet);
    if (longest) clearTimeout(longest);
    quiet = longest = null;
  };

  const flush = async () => {
    close();
    const batch = gathered;
    gathered = new Map();
    if (stopped) return;
    // Checked when the window closes, not when the push arrives: the push of
    // this tab's own write can come before the write's answer does.
    for (const [id, w] of batch) {
      const held = w.version === null ? null : effects.held(id, w.version);
      if (!held) continue;
      batch.delete(id);
      w.resolve(held);
    }
    if (batch.size === 0) return;
    if (batch.size > HINT_BURST) {
      effects.refreshLists();
      for (const w of batch.values()) w.reject(new ReadAsList());
      return;
    }
    const since = effects.stamp();
    const ids = [...batch.keys()];
    const answers = await Promise.allSettled(ids.map((id) => effects.readTask(id)));
    if (stopped) return;
    // One window's answers are placed together, so a respace heard as a
    // handful of hints lands as one order, not as each read returns.
    let unknown = false;
    answers.forEach((answer, index) => {
      const id = ids[index]!;
      const w = batch.get(id)!;
      if (answer.status === "fulfilled") {
        effects.place(answer.value, since);
        w.resolve(answer.value);
      } else if (effects.isGone(answer.reason)) {
        effects.remove(id, since);
        w.resolve(null);
      } else {
        unknown = true;
        w.reject(answer.reason);
      }
    });
    // A read that failed for another reason leaves the task's place unknown.
    if (unknown) effects.refreshLists();
  };

  return {
    hint(id, version) {
      const named = version ?? null;
      const held = gathered.get(id);
      if (held) {
        held.version = held.version === null || named === null ? null : Math.max(held.version, named);
        return held.promise;
      }
      const w = waiter(named);
      gathered.set(id, w);
      if (quiet) clearTimeout(quiet);
      quiet = setTimeout(() => void flush(), HINT_WINDOW_MS);
      longest ??= setTimeout(() => void flush(), HINT_WINDOW_MAX_MS);
      return w.promise;
    },
    stop() {
      stopped = true;
      close();
      for (const w of gathered.values()) w.reject(new Error("the channel stopped"));
      gathered = new Map();
    },
  };
}
