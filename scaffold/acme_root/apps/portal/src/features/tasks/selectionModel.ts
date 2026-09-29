// Pure: which tasks are selected. A selection lives in one section, the open
// tasks or the done ones, of the list on screen; a pick in the other section
// starts a new one there. It is a set of rows the person picked, or, after
// "Select all", every task of the section in the scope, loaded or not.
import type { TaskSection } from "../../store/preferences";

export interface Selection {
  section: TaskSection | null;
  /** The rows picked, in the order the section shows them. */
  ids: string[];
  /** Where a Shift-click reaches from: the row picked last on its own. */
  anchor: string | null;
  /** Every task of the section in the scope, as the server reads it. */
  all: boolean;
}

export const NOTHING: Selection = { section: null, ids: [], anchor: null, all: false };

/** How a pick reaches: one row on and off, or every row from the anchor. */
export type Pick = "toggle" | "range";

/** The ids in the order `shown` lists them; one it does not list goes. */
function inOrder(ids: Iterable<string>, shown: readonly string[]): string[] {
  const picked = new Set(ids);
  return shown.filter((id) => picked.has(id));
}

/** One row on or off. A row of the other section starts a new selection; a
 * row picked after "Select all" narrows it to the rows on screen. */
export function toggle(selection: Selection, section: TaskSection, id: string, shown: readonly string[]): Selection {
  if (selection.section !== section) return { section, ids: [id], anchor: id, all: false };
  const current = new Set(selection.all ? shown : selection.ids);
  if (current.has(id)) current.delete(id);
  else current.add(id);
  const ids = inOrder(current, shown);
  return ids.length === 0 ? NOTHING : { section, ids, anchor: id, all: false };
}

/** Every row from the anchor to this one, as the section shows them, added
 * to what is picked. With no anchor in this section it picks the one row,
 * which becomes the anchor. */
export function range(selection: Selection, section: TaskSection, id: string, shown: readonly string[]): Selection {
  const anchor = selection.section === section ? selection.anchor : null;
  const from = anchor === null ? -1 : shown.indexOf(anchor);
  const to = shown.indexOf(id);
  if (from === -1 || to === -1) return toggle(selection.section === section ? selection : NOTHING, section, id, shown);
  if (selection.all) return selection;
  const [low, high] = from < to ? [from, to] : [to, from];
  const ids = inOrder([...selection.ids, ...shown.slice(low, high + 1)], shown);
  return { section, ids, anchor, all: false };
}

export function pick(selection: Selection, section: TaskSection, id: string, shown: readonly string[], how: Pick): Selection {
  return how === "range" ? range(selection, section, id, shown) : toggle(selection, section, id, shown);
}

/** Every task of the section in the scope, not only the page loaded. */
export function selectAll(section: TaskSection): Selection {
  return { section, ids: [], anchor: null, all: true };
}

/** The selection as the section now shows it: a picked row that left it
 * (done elsewhere, deleted, moved to the other section) is no longer
 * picked, and a selection with none left is none. */
export function prune(selection: Selection, shown: readonly string[]): Selection {
  if (selection.section === null || selection.all) return selection;
  const ids = inOrder(selection.ids, shown);
  if (ids.length === selection.ids.length) return selection;
  return ids.length === 0 ? NOTHING : { ...selection, ids, anchor: ids.includes(selection.anchor ?? "") ? selection.anchor : null };
}

export function isSelected(selection: Selection, section: TaskSection, id: string): boolean {
  return selection.section === section && (selection.all || selection.ids.includes(id));
}

export function isEmpty(selection: Selection): boolean {
  return selection.section === null;
}

/** How many tasks are selected: the picked rows, or the section's count on
 * the server after "Select all" (null until it is read). */
export function selectedCount(selection: Selection, allCount: number | null): number | null {
  return selection.all ? allCount : selection.ids.length;
}
