// Pure: the rows the list renders, drag placements, and the order a drag
// shows before the server answers.
import type { InfiniteData } from "@tanstack/react-query";
import type { MeView, TaskPageView, TaskView, UserView } from "../../api";
import { dueBadge, type DueBadge } from "./dueModel";

/** How long a task takes to fade out of one group and into the other. */
export const MOTION_MS = 1000;

export type DropSide = "before" | "after";

export interface TaskRow {
  id: string;
  title: string;
  notes: string;
  done: boolean;
  assigneeId: string | null;
  createdBy: string;
  assignee: string | null;
  /** The due date's badge, or null when the task has none. */
  due: DueBadge | null;
}

export function canWrite(me: MeView | undefined): boolean {
  return me?.permissions.includes("write") ?? false;
}

export function canAdd(title: string): boolean {
  return title.trim().length > 0;
}

/** What a person no longer in the org is called: their user is gone, and
 * the id on what they made names nobody. The list of members is read whole
 * before any name is shown, so an id it lacks is one that left. */
export const FORMER_MEMBER = "Former member";

export function nameOf(userId: string, users: ReadonlyMap<string, UserView>, meId: string | null): string {
  if (userId === meId) return "you";
  return users.get(userId)?.display_name ?? FORMER_MEMBER;
}

export function taskRow(
  task: TaskView,
  users: ReadonlyMap<string, UserView>,
  meId: string | null,
  now: Date = new Date(),
): TaskRow {
  return {
    id: task.id,
    title: task.title,
    notes: task.notes,
    done: task.status === "done",
    assigneeId: task.assignee_id ?? null,
    createdBy: nameOf(task.created_by, users, meId),
    assignee: task.assignee_id ? nameOf(task.assignee_id, users, meId) : null,
    due: dueBadge(task, now),
  };
}

export function flattenPages(data: InfiniteData<TaskPageView> | undefined): TaskView[] {
  return data?.pages.flatMap((page) => page.items) ?? [];
}

/** Where a dragged open task lands: the new order, and the `after_id` the
 * move call takes (null for the top). Null when the drop changes nothing. */
export function placement(
  open: TaskView[],
  movedId: string,
  targetId: string,
  side: DropSide,
): { order: TaskView[]; afterId: string | null } | null {
  const moved = open.find((task) => task.id === movedId);
  if (!moved || movedId === targetId) return null;
  const without = open.filter((task) => task.id !== movedId);
  const targetIndex = without.findIndex((task) => task.id === targetId);
  if (targetIndex === -1) return null;
  const insertAt = side === "before" ? targetIndex : targetIndex + 1;
  const order = [...without.slice(0, insertAt), moved, ...without.slice(insertAt)];
  if (order.every((task, index) => task.id === open[index]?.id)) return null;
  return { order, afterId: insertAt === 0 ? null : (without[insertAt - 1]?.id ?? null) };
}

// The order a drag asks for, shown before the server answers. Every other
// cache edit is a placement (`queries/taskPlacement.ts`).

/** The whole order asked for, on the first page; the pages after it are
 * emptied, since the order spans every page that was loaded. */
export function pagesWithOrder(
  data: InfiniteData<TaskPageView> | undefined,
  order: TaskView[],
): InfiniteData<TaskPageView> | undefined {
  if (!data || data.pages.length === 0) return data;
  const [first, ...rest] = data.pages;
  return { ...data, pages: [{ ...first!, items: order }, ...rest.map((page) => ({ ...page, items: [] }))] };
}

export interface Leaving {
  task: TaskView;
  index: number;
}

/** The open list as rendered: a task fading out keeps its place for the whole
 * motion, whatever the server's list already says. */
export function withLeaving(open: TaskView[], leaving: Leaving[]): TaskView[] {
  const leavingIds = new Set(leaving.map((l) => l.task.id));
  const rows = open.filter((task) => !leavingIds.has(task.id));
  for (const { task, index } of [...leaving].sort((a, b) => a.index - b.index)) {
    rows.splice(Math.min(index, rows.length), 0, task);
  }
  return rows;
}
