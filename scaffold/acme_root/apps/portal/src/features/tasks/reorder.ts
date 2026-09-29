// The drag reorder as one flow with its effects handed in, so the stale case
// runs in a test without React: the list shows the new order at once, the
// move names the version of the task as held, the server's answer takes the
// optimistic row's place, and a refusal (someone else changed the list first)
// is said and the list reloaded from the server.
import { ApiError, type TaskView } from "../../api";
import { placement, type DropSide } from "./tasksModel";

export const STALE_MESSAGE = "Someone changed this list first; it was reloaded.";

export interface ReorderEffects {
  /** The move call: the task, what it lands after, and the version it was read at. */
  move: (id: string, afterId: string | null, version: number) => Promise<TaskView>;
  /** Shows the order the drop asked for while the server answers. */
  showOrder: (order: TaskView[]) => void;
  /** Puts the row the server wrote in place of the optimistic one. */
  showMoved: (moved: TaskView) => void;
  /** Reloads the list from the server, after a refusal. */
  refetch: () => void;
  /** Says what went wrong in one line. */
  report: (message: string) => void;
}

export type ReorderOutcome = "unchanged" | "moved" | "refused";

/** A write refused because the task changed since it was read. */
export function isStale(cause: unknown): boolean {
  return cause instanceof ApiError && cause.status === 412 && cause.code === "precondition_failed";
}

export async function reorder(
  open: TaskView[],
  movedId: string,
  targetId: string,
  side: DropSide,
  effects: ReorderEffects,
  describe: (cause: unknown) => string,
): Promise<ReorderOutcome> {
  const result = placement(open, movedId, targetId, side);
  const moved = open.find((task) => task.id === movedId);
  if (!result || !moved) return "unchanged";
  effects.showOrder(result.order);
  try {
    // The row the server wrote replaces the optimistic one: it carries the
    // version the move bumped, and without it a second drag sends the
    // version as read and is told, wrongly, that someone else changed the
    // list (`complete`, `reopen` and `add` do the same with what their
    // writes answer). No list is read again.
    effects.showMoved(await effects.move(movedId, result.afterId, moved.version));
    return "moved";
  } catch (cause) {
    effects.report(isStale(cause) ? STALE_MESSAGE : describe(cause));
    effects.refetch();
    return "refused";
  }
}
