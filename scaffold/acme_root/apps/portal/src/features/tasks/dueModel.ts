// Pure: a task's due date, between the wire and the screen. The wire carries
// a calendar date, "YYYY-MM-DD", with no time and no zone. The screen shows it
// as that same date and takes it from a `date` input, whose value is the same
// string. A date is never read through `new Date(iso)`, which would take it as
// UTC midnight and shift it a day for anyone west of UTC.
import type { TaskView } from "../../api";

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const LONG_DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const LONG_MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];
const DAY_MS = 86_400_000;

/** A due date is upcoming (today or later), overdue (before today, its
 * reminder not out), or reminded. */
export type DueState = "upcoming" | "overdue" | "reminded";

export interface DueBadge {
  state: DueState;
  /** The short text on the row, e.g. "due Tomorrow". */
  text: string;
  /** The whole date, for the tooltip. */
  title: string;
}

/** The local calendar date a "YYYY-MM-DD" names, or null for anything else. */
export function parseDate(value: string | null | undefined): Date | null {
  const match = value ? /^(\d{4})-(\d{2})-(\d{2})$/.exec(value.trim()) : null;
  if (!match) return null;
  const [, y, m, d] = match.map(Number) as [number, number, number, number];
  const date = new Date(y, m - 1, d);
  if (date.getFullYear() !== y || date.getMonth() !== m - 1 || date.getDate() !== d) return null;
  return date;
}

/** Days from `now`'s calendar day to `date`'s: 0 today, 1 tomorrow, -1 yesterday. */
function daysFrom(date: Date, now: Date): number {
  const day = (d: Date) => Date.UTC(d.getFullYear(), d.getMonth(), d.getDate());
  return Math.round((day(date) - day(now)) / DAY_MS);
}

/** Short: Today, Tomorrow, the weekday within the week ahead, the month and
 * day further out or behind, and the year too when it is not this one. */
export function formatDue(date: Date, now: Date): string {
  const days = daysFrom(date, now);
  if (days === 0) return "Today";
  if (days === 1) return "Tomorrow";
  if (days > 1 && days <= 6) return DAYS[date.getDay()]!;
  const day = `${MONTHS[date.getMonth()]} ${date.getDate()}`;
  return date.getFullYear() === now.getFullYear() ? day : `${day} ${date.getFullYear()}`;
}

export function formatDueLong(date: Date): string {
  return `${LONG_DAYS[date.getDay()]} ${date.getDate()} ${LONG_MONTHS[date.getMonth()]} ${date.getFullYear()}`;
}

export function dueState(task: Pick<TaskView, "due_on" | "reminded_at">, now: Date): DueState | null {
  const due = parseDate(task.due_on);
  if (!due) return null;
  if (task.reminded_at) return "reminded";
  return daysFrom(due, now) < 0 ? "overdue" : "upcoming";
}

export function dueBadge(task: Pick<TaskView, "due_on" | "reminded_at">, now: Date): DueBadge | null {
  const state = dueState(task, now);
  const due = parseDate(task.due_on);
  if (!state || !due) return null;
  const short = formatDue(due, now);
  const long = formatDueLong(due);
  if (state === "reminded") return { state, text: `reminded · due ${short}`, title: `Due ${long}. The reminder went out.` };
  if (state === "overdue") return { state, text: `Overdue · ${short}`, title: `Due ${long}. The date has passed.` };
  return { state, text: `due ${short}`, title: `Due ${long}` };
}

/** The value a `date` input shows for a due date: the date itself, or empty. */
export function toInputValue(dueOn: string | null | undefined): string {
  return dueOn && parseDate(dueOn) ? dueOn.trim() : "";
}

/** What an edit sends as `due_on`: undefined when the input did not change,
 * so the field is left out and the date kept; null when it was emptied,
 * which clears it; the new date otherwise, which reschedules. */
export function dueOnChange(initial: string, current: string): string | null | undefined {
  if (current.trim() === initial.trim()) return undefined;
  return parseDate(current) ? current.trim() : null;
}
