// Pure: what a change of many tasks sends, what it says, and what undoes it.
import type { BulkAction, BulkTasksRequest, BulkTasksView, TaskScope } from "../../api";
import type { TaskSection } from "../../store/preferences";
import type { Selection } from "./selectionModel";

/** What a section's tasks can become: an open task done, a done one open. */
export function actionOf(section: TaskSection): BulkAction {
  return section === "open" ? "complete" : "reopen";
}

export function actionLabel(section: TaskSection): string {
  return section === "open" ? "Mark done" : "Reopen";
}

const tasks = (n: number) => (n === 1 ? "1 task" : `${n} tasks`);

/** The question "Mark all" asks, with the section's count on the server. */
export function confirmTitle(section: TaskSection, count: number): string {
  return section === "open" ? `Mark ${tasks(count)} as done?` : `Reopen ${tasks(count)}?`;
}

export function confirmBody(section: TaskSection, scope: TaskScope): string {
  const which = scope === "mine" ? "your" : "the team's";
  return section === "open"
    ? `Every open task on ${which} list moves to Done, not only the ones on screen. You can undo it for a few seconds.`
    : `Every done task on ${which} list goes back to the top of Open. You can undo it for a few seconds.`;
}

/** The request for what is selected. Picked rows are named in the order the
 * section shows them; a reopen names them bottom first, since the server
 * puts the last one named on top, so they keep the order they had. */
export function requestFor(selection: Selection, scope: TaskScope): BulkTasksRequest | null {
  const section = selection.section;
  if (section === null) return null;
  const action = actionOf(section);
  if (selection.all) return { action, all: { scope, status: section } };
  const ids = action === "reopen" ? [...selection.ids].reverse() : selection.ids;
  return ids.length === 0 ? null : { action, ids };
}

/** "Mark all": the whole section in the scope, as the server reads it. */
export function requestForAll(section: TaskSection, scope: TaskScope): BulkTasksRequest {
  return { action: actionOf(section), all: { scope, status: section } };
}

/** The other action over exactly the tasks the change wrote, not the section
 * as it is now. The server answered them in the order it wrote them; an
 * undone completion reopens them bottom first, so the open list reads as it
 * did. Null when nothing changed, or when the answer could not name every
 * task it changed (more than the answer's cap), since an undo of some of
 * them is not an undo. */
export function undoRequest(answer: BulkTasksView): BulkTasksRequest | null {
  if (answer.changed_count === 0 || answer.changed.length < answer.changed_count) return null;
  if (answer.action === "complete") return { action: "reopen", ids: [...answer.changed].reverse() };
  return { action: "complete", ids: answer.changed };
}

/** What the toast says about a change: how many changed, and how many were
 * skipped because they had changed or gone meanwhile. The ones a plan's
 * bound held back are the upgrade dialog's to say. */
export function doneMessage(answer: BulkTasksView): string {
  const verb = answer.action === "complete" ? "marked done" : "reopened";
  const heldByPlan = answer.skipped.filter((s) => s.reason === "plan_limit").length;
  const skipped = answer.skipped_count - heldByPlan;
  const said = `${tasks(answer.changed_count)} ${verb}`;
  return skipped > 0 ? `${said}, ${skipped} skipped` : said;
}
