// Pure: where one task goes in the cached task lists of one scope, given what
// the server said about it. Both paths use it: the answer to this tab's own
// write, and the single read a push about a task leads to. Values in, values
// out; the cache module (`taskCache.ts`) reads the lists and writes them back.
//
// The cached lists mirror the server's order: open by (rank, id), top first;
// done by (updated_at, id), newest first. `mine` shows a task assigned
// to the person, or unassigned and created by them; `team` shows every task
// (the server's `is_visible`). An archived or a deleted task is in neither.
//
// A list is the window the page has loaded: a prefix of the server's order,
// whose last page names in `next_cursor` the last item it holds. So the
// window's last item is its edge. Past the edge nothing is known: a task that
// sorts there is left out. When the placement cannot say what the server
// would now show, it asks for that one list to be read again rather than
// guess.
import type { InfiniteData } from "@tanstack/react-query";
import type { TaskPageView, TaskScope, TaskStatus, TaskView } from "../api";

export type TaskPages = InfiniteData<TaskPageView>;

/** The page each list asks for: the open list is the one on screen whole,
 * the done list its most recent few. */
export const OPEN_PAGE_SIZE = 200;
export const DONE_PAGE_SIZE = 10;

/** What the server said: the task as it now is, or that it is gone (a 404). */
export type TaskChange = { task: TaskView } | { gone: string };

export interface PlacementRules {
  scope: TaskScope;
  /** The person the page is for; `mine` cannot be decided without it. */
  meId: string | null;
  /** The page size each list asks for. */
  limits: Record<TaskStatus, number>;
}

export interface PlacementOptions {
  /** An edit made before the server answers: no version check. */
  optimistic?: boolean;
  /** A task already in the list keeps its row (the answer to a drag, whose
   * order the page already shows). */
  inPlace?: boolean;
}

export type ListOutcome = "unchanged" | "inserted" | "replaced" | "reordered" | "removed" | "invalidate";

export interface ListPlacement {
  data: TaskPages | undefined;
  outcome: ListOutcome;
}

export type ScopePlacement = Record<TaskStatus, ListPlacement>;

export type ScopeLists = Partial<Record<TaskStatus, TaskPages>>;

/** The server's `is_visible`; null when the scope is `mine` and the person
 * is not known yet. */
export function isVisible(task: TaskView, scope: TaskScope, meId: string | null): boolean | null {
  if (scope === "team") return true;
  if (meId === null) return null;
  const assignee = task.assignee_id ?? null;
  return assignee !== null ? assignee === meId : task.created_by === meId;
}

/** Whether the task belongs in the list of `status`; null when it cannot be told. */
export function belongsIn(task: TaskView, status: TaskStatus, rules: PlacementRules): boolean | null {
  if (task.deleted_at || task.archived_at || task.status !== status) return false;
  return isVisible(task, rules.scope, rules.meId);
}

const byId = (a: TaskView, b: TaskView) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);

/** A rank as the exact decimal it is: its digits as one integer, and how
 * many of them are after the point. */
function decimalOf(rank: string): { units: bigint; scale: number } {
  const negative = rank.startsWith("-");
  const [whole = "0", fraction = ""] = rank.replace(/^[-+]/, "").split(".");
  const units = BigInt(`${whole}${fraction}` || "0");
  return { units: negative ? -units : units, scale: fraction.length };
}

/** Two ranks compared as numbers, exactly: never as floats, which tie past
 * sixteen digits, and never as text, which sorts "10" before "9". */
export function compareRank(a: string, b: string): number {
  if (a === b) return 0;
  const x = decimalOf(a);
  const y = decimalOf(b);
  const scale = Math.max(x.scale, y.scale);
  const left = x.units * 10n ** BigInt(scale - x.scale);
  const right = y.units * 10n ** BigInt(scale - y.scale);
  return left < right ? -1 : left > right ? 1 : 0;
}

/** Where a reopen sits before the server answers: on top, where the server
 * puts it. The server's top moves one whole number per task placed there,
 * so no rank it gives reaches this one. It is never sent. */
export const ON_TOP = `-1${"0".repeat(40)}`;

/** The open list's order: rank, then id, ascending. */
export function compareOpen(a: TaskView, b: TaskView): number {
  return compareRank(a.rank, b.rank) || byId(a, b);
}

/** An instant in microseconds, the precision the server orders by; a
 * millisecond reading would tie two writes the server tells apart. */
export function microsOf(stamp: string): number {
  const fraction = /\.(\d+)/.exec(stamp)?.[1] ?? "";
  const seconds = Date.parse(stamp.replace(/\.\d+/, ""));
  return seconds * 1000 + Number((fraction + "000000").slice(0, 6));
}

/** The done list's order: updated_at, then id, descending. */
export function compareDone(a: TaskView, b: TaskView): number {
  const at = microsOf(b.updated_at) - microsOf(a.updated_at);
  return at || byId(b, a);
}

const ORDER: Record<TaskStatus, (a: TaskView, b: TaskView) => number> = { open: compareOpen, done: compareDone };

export function flatten(data: TaskPages): TaskView[] {
  return data.pages.flatMap((page) => page.items);
}

/** The whole window on the first page; the pages after it are emptied and
 * keep their cursors, so the last page still names the window's edge. */
function withItems(data: TaskPages, items: TaskView[]): TaskPages {
  const [first, ...rest] = data.pages;
  if (!first) return data;
  return { ...data, pages: [{ ...first, items }, ...rest.map((page) => ({ ...page, items: [] }))] };
}

const hasMore = (data: TaskPages) => (data.pages.at(-1)?.next_cursor ?? null) !== null;

const idOf = (change: TaskChange) => ("task" in change ? change.task.id : change.gone);

const unchanged = (data: TaskPages | undefined): ListPlacement => ({ data, outcome: "unchanged" });
const invalidate = (data: TaskPages | undefined): ListPlacement => ({ data, outcome: "invalidate" });

/** Whether the lists hold this task at a version as new as the one given:
 * then the change is not news, whatever order it arrived in. */
export function heldAtOrAbove(lists: ScopeLists, task: TaskView): boolean {
  return [lists.open, lists.done].some(
    (data) => data !== undefined && flatten(data).some((t) => t.id === task.id && t.version >= task.version),
  );
}

function placeIn(
  data: TaskPages | undefined,
  status: TaskStatus,
  change: TaskChange,
  rules: PlacementRules,
  options: PlacementOptions,
): ListPlacement {
  if (!data || data.pages.length === 0) return unchanged(data);
  const id = idOf(change);
  const items = flatten(data);
  const at = items.findIndex((t) => t.id === id);
  const more = hasMore(data);
  const edge = more ? items.at(-1) : undefined;
  const without = at === -1 ? items : items.filter((t) => t.id !== id);
  const order = ORDER[status];

  let wanted = false;
  if ("task" in change) {
    const belongs = belongsIn(change.task, status, rules);
    if (belongs === null) return invalidate(data);
    wanted = belongs;
  }
  const task = "task" in change ? change.task : null;

  // The window's edge moved: the cursor still names where it was, and only
  // the server knows what now sits between.
  if (edge && edge.id === id && !(task && wanted && order(task, edge) === 0)) return invalidate(data);

  // Past the edge of a window with more behind it: not this window's to show.
  if (task && wanted && edge && order(task, edge) > 0) wanted = false;

  if (!task || !wanted) {
    if (at === -1) return unchanged(data);
    // A window with more behind it shows one row fewer than the server
    // would, and the row that would take the place is not held.
    if (more && without.length < data.pages.length * rules.limits[status]) return invalidate(data);
    return { data: withItems(data, without), outcome: "removed" };
  }

  if (at !== -1 && options.inPlace) {
    return { data: withItems(data, items.map((t) => (t.id === id ? task : t))), outcome: "replaced" };
  }
  const placed = [...without, task].sort(order);
  if (at === -1) return { data: withItems(data, placed), outcome: "inserted" };
  const kept = placed.every((t, index) => t.id === items[index]?.id);
  return { data: withItems(data, placed), outcome: kept ? "replaced" : "reordered" };
}

/** Where the change goes in the open and the done list of one scope: an
 * insert, a move between the two, a reorder, a replace, or a removal, or,
 * where the window cannot tell, a read of that one list. A task these lists
 * already hold at the same or a newer version is left as held. */
export function placeTask(
  lists: ScopeLists,
  change: TaskChange,
  rules: PlacementRules,
  options: PlacementOptions = {},
): ScopePlacement {
  if ("task" in change && !options.optimistic && heldAtOrAbove(lists, change.task)) {
    return { open: unchanged(lists.open), done: unchanged(lists.done) };
  }
  return {
    open: placeIn(lists.open, "open", change, rules, options),
    done: placeIn(lists.done, "done", change, rules, options),
  };
}

/** How many rows of a list the page shows: a page's worth per page loaded.
 * A task placed into a full window stays held past it, since the last page's
 * cursor names the row it ended on and a dropped row would never come back
 * on "Show more"; it shows once the person asks for more. */
export function windowOf<T>(items: T[], pagesLoaded: number, limit: number, extraPages = 0): T[] {
  return items.slice(0, Math.max(1, pagesLoaded + extraPages) * limit);
}
