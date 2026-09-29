// The task lists in the query cache, written from what the server said about
// one task: the answer to this tab's own write, or the single read a push
// leads to. Where the task goes is `placeTask` in `taskPlacement.ts`; this
// module reads every cached list, in every scope, and writes them back.
//
// Answers can arrive out of order, so a ledger per query client keeps, for
// each task heard of, the newest version seen and when a 404 last removed it.
// An older version never overwrites a newer one, even after the task left
// the lists; a read issued before a removal never brings the task back.
import type { InfiniteData, QueryClient, QueryKey } from "@tanstack/react-query";
import type { MeView, TaskPageView, TaskScope, TaskStatus, TaskView } from "../api";
import { keys } from "./keys";
import {
  DONE_PAGE_SIZE,
  OPEN_PAGE_SIZE,
  placeTask as placement,
  type PlacementOptions,
  type ScopeLists,
  type TaskChange,
} from "./taskPlacement";

type Pages = InfiniteData<TaskPageView>;

interface Seen {
  /** The newest version the server answered with; null when only a 404 is known. */
  version: number | null;
  /** The ledger's clock when this was recorded. */
  at: number;
  /** The task as last answered, or null when it is gone. */
  task: TaskView | null;
}

interface Ledger {
  clock: number;
  seen: Map<string, Seen>;
  /** Lists being read while a change was placed: their answer may predate
   * it, so the change is placed again once the read lands. */
  pending: Map<string, Set<string>>;
}

const ledgers = new WeakMap<QueryClient, Ledger>();

function ledgerOf(queryClient: QueryClient): Ledger {
  let ledger = ledgers.get(queryClient);
  if (ledger) return ledger;
  const created: Ledger = { clock: 0, seen: new Map(), pending: new Map() };
  ledger = created;
  ledgers.set(queryClient, created);
  queryClient.getQueryCache().subscribe((event) => {
    if (event.type !== "updated" || event.action.type !== "success" || event.action.manual) return;
    const ids = created.pending.get(event.query.queryHash);
    if (!ids) return;
    created.pending.delete(event.query.queryHash);
    const scope = event.query.queryKey[2] as TaskScope;
    for (const id of ids) {
      const seen = created.seen.get(id);
      if (!seen) continue;
      applyToScope(queryClient, created, scope, seen.task ? { task: seen.task } : { gone: id }, {});
    }
  });
  return ledger;
}

const LIMITS: Record<TaskStatus, number> = { open: OPEN_PAGE_SIZE, done: DONE_PAGE_SIZE };

const listKey = (status: TaskStatus, scope: TaskScope): QueryKey =>
  status === "open" ? keys.tasks.open(scope) : keys.tasks.done(scope);

/** Every scope some task list is cached under. */
function cachedScopes(queryClient: QueryClient): Set<TaskScope> {
  const scopes = new Set<TaskScope>();
  for (const status of ["open", "done"] as const) {
    for (const query of queryClient.getQueryCache().findAll({ queryKey: [keys.tasks.all[0], status] })) {
      scopes.add(query.queryKey[2] as TaskScope);
    }
  }
  return scopes;
}

function applyToScope(
  queryClient: QueryClient,
  ledger: Ledger,
  scope: TaskScope,
  change: TaskChange,
  options: PlacementOptions,
): void {
  const meId = queryClient.getQueryData<MeView>(keys.me)?.user.id ?? null;
  const lists: ScopeLists = {
    open: queryClient.getQueryData<Pages>(listKey("open", scope)),
    done: queryClient.getQueryData<Pages>(listKey("done", scope)),
  };
  const placed = placement(lists, change, { scope, meId, limits: LIMITS }, options);
  const id = "task" in change ? change.task.id : change.gone;
  for (const status of ["open", "done"] as const) {
    const key = listKey(status, scope);
    const { data, outcome } = placed[status];
    if (outcome === "invalidate") {
      void queryClient.invalidateQueries({ queryKey: key, exact: true });
      continue;
    }
    if (outcome !== "unchanged") queryClient.setQueryData<Pages>(key, data);
    const query = queryClient.getQueryCache().find({ queryKey: key, exact: true });
    if (query?.state.fetchStatus === "fetching" && !options.optimistic) {
      const ids = ledger.pending.get(query.queryHash) ?? new Set<string>();
      ids.add(id);
      ledger.pending.set(query.queryHash, ids);
    }
  }
}

/** The archive is read only while it is open, and newest first by a field
 * the lists do not carry, so a task newly archived asks for it to be read
 * again (which reads nothing while it is closed); a task that left it is
 * taken out. */
function applyToArchive(queryClient: QueryClient, id: string, task: TaskView | null): void {
  for (const query of queryClient.getQueryCache().findAll({ queryKey: [keys.tasks.all[0], "archived"] })) {
    const data = query.state.data as TaskPageView | undefined;
    const held = data?.items.find((t) => t.id === id);
    if (task?.archived_at && !task.deleted_at) {
      if (!held || held.version < task.version) void queryClient.invalidateQueries({ queryKey: query.queryKey, exact: true });
    } else if (held && data) {
      queryClient.setQueryData<TaskPageView>(query.queryKey, { ...data, items: data.items.filter((t) => t.id !== id) });
    }
  }
}

/** The ledger's clock now: a single read notes it when it is issued, and
 * hands it back with a 404 (`removeTask`'s `since`). */
export function taskStamp(queryClient: QueryClient): number {
  return ledgerOf(queryClient).clock;
}

/** The task as this tab placed it, when that is at or past `version`: a
 * write's answer or a read's, never an optimistic edit, and never a task a
 * 404 took out. Placing puts a task into every cached list, and the ledger
 * places it again into a list read while it was placed, so every list this
 * tab holds already shows it; a push naming that version has nothing to read. */
export function heldTask(queryClient: QueryClient, id: string, version: number): TaskView | null {
  const seen = ledgerOf(queryClient).seen.get(id);
  if (!seen?.task || seen.version === null || seen.version < version) return null;
  return seen.task;
}

export interface PlaceOptions extends PlacementOptions {
  /** The stamp at which the read that answered was issued. */
  since?: number;
}

/** Puts what the server answered for one task into every cached list: a
 * write's answer, or a single read's. An answer older than one already
 * placed, or a read issued before a 404 that landed, is dropped. An
 * optimistic edit is placed as asked and leaves the ledger alone. */
export function placeTask(queryClient: QueryClient, task: TaskView, options: PlaceOptions = {}): void {
  const ledger = ledgerOf(queryClient);
  if (!options.optimistic) {
    const seen = ledger.seen.get(task.id);
    if (seen && seen.version !== null && seen.version > task.version) return;
    if (seen && seen.task === null && options.since !== undefined && seen.at > options.since) return;
    ledger.clock += 1;
    ledger.seen.set(task.id, { version: Math.max(task.version, seen?.version ?? 0), at: ledger.clock, task });
  }
  for (const scope of cachedScopes(queryClient)) applyToScope(queryClient, ledger, scope, { task }, options);
  if (!options.optimistic) applyToArchive(queryClient, task.id, task);
}

export interface RemoveOptions {
  /** The stamp at which the read that answered 404 was issued: a newer
   * answer placed since then wins. */
  since?: number;
  /** A removal made before the server answers. */
  optimistic?: boolean;
}

/** Takes one task out of every cached list: a 404 on its read (deleted, or
 * no longer this org's to see), or a delete before its answer. */
export function removeTask(queryClient: QueryClient, id: string, options: RemoveOptions = {}): void {
  const ledger = ledgerOf(queryClient);
  if (!options.optimistic) {
    const seen = ledger.seen.get(id);
    if (seen && options.since !== undefined && seen.at > options.since) return;
    ledger.clock += 1;
    ledger.seen.set(id, { version: seen?.version ?? null, at: ledger.clock, task: null });
  }
  for (const scope of cachedScopes(queryClient)) applyToScope(queryClient, ledger, scope, { gone: id }, options);
  if (!options.optimistic) applyToArchive(queryClient, id, null);
}

/** Every task list read again, once: for a burst of pushes too large to read
 * task by task, or a write the server refused. */
export function refreshTaskLists(queryClient: QueryClient): void {
  void queryClient.invalidateQueries({ queryKey: keys.tasks.all });
}
