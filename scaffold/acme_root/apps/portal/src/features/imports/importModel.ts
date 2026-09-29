// Pure: which import the tasks page shows, and how it is said.
import type { BillingView, ImportView, Plan, PlanLimit } from "../../api";

/** How long before the page opened an import may have ended and still show. */
export const ENDED_SHOWN_MS = 15 * 60 * 1000;

/** The import to show: the newest one, while it runs or waits, or once it
 * ended, until the person dismisses it. One that ended well before the page
 * opened (`openedAt`) is not news, and one older than the newest shows
 * nothing. */
export function shownImport(
  items: readonly ImportView[],
  dismissed: ReadonlySet<string>,
  openedAt: number,
): ImportView | null {
  const newest = items[0];
  if (!newest || dismissed.has(newest.id)) return null;
  if (newest.finished_at !== null && openedAt - Date.parse(newest.finished_at) > ENDED_SHOWN_MS) return null;
  return newest;
}

export function isRunning(view: ImportView): boolean {
  return view.status === "running";
}

export function isParkedOnThePlan(view: ImportView): boolean {
  return view.status === "parked" && view.park_reason === "plan_limit";
}

function rows(count: number): string {
  return count === 1 ? "1 row" : `${count} rows`;
}

/** How far an import is: created N of M, skipped K. */
export function progressLine(view: ImportView): string {
  const total = view.total === null ? "" : ` of ${view.total}`;
  const skipped = view.skipped > 0 ? `, skipped ${rows(view.skipped)}` : "";
  return `Created ${view.created}${total}${skipped}`;
}

const FAILURES: Readonly<Record<string, string>> = {
  file_too_large: "the file is larger than 1 MB.",
  too_many_rows: "the file has more than 5,000 rows.",
  not_csv: "the file is not a CSV file.",
  no_title_column: "the file has no title column.",
  file_gone: "the file was removed before it was read.",
  defect: "something went wrong on our side. Try again later.",
};

/** The one sentence at the head of the import's card. */
export function headline(view: ImportView): string {
  switch (view.status) {
    case "running":
      return view.total === null ? "Importing tasks…" : `Importing tasks: ${progressLine(view).toLowerCase()}.`;
    case "parked":
      return isParkedOnThePlan(view)
        ? `Import paused at your plan's limit of active tasks. ${progressLine(view)}.`
        : `Import paused. ${progressLine(view)}.`;
    case "succeeded":
      return `Imported ${view.created === 1 ? "1 task" : `${view.created} tasks`}${
        view.skipped > 0 ? `; skipped ${rows(view.skipped)}` : ""
      }.`;
    case "failed":
      return `Import failed: ${FAILURES[view.fail_reason ?? "defect"] ?? FAILURES.defect}`;
    default:
      return progressLine(view);
  }
}

/** The rows the import skipped, as lines: `Row 3: no title`. */
export function skippedLines(view: ImportView): string[] {
  const named = view.row_errors.map((e) => `Row ${e.row}: ${e.reason}`);
  const more = view.skipped - named.length;
  return more > 0 ? [...named, `and ${rows(more)} more`] : named;
}

const ORDER: readonly Plan[] = ["free", "pro", "team", "max"];

/** The bound an import parked on, in the shape the upgrade dialog reads: the
 * org's plan, its active tasks, and the first plan above it with more. */
export function activeTasksLimit(billing: BillingView): PlanLimit {
  const bound = billing.limits.active_tasks;
  const above = billing.plans
    .filter((offer) => ORDER.indexOf(offer.plan) > ORDER.indexOf(billing.plan))
    .find((offer) => offer.limits.active_tasks === null || (bound !== null && offer.limits.active_tasks > bound));
  return { lever: "active_tasks", plan: billing.plan, limit: bound, suggested_plan: above?.plan ?? null };
}
