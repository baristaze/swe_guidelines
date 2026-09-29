// Pure: which list the tasks page shows and what its heading says. An org
// with more than one member, of either kind, offers the person's own tasks
// and the team's; a person alone in an org has one list.
import type { TaskScope } from "../../api";

/** The switch that stands as the page's heading when the org has company. */
export const SCOPE_CHOICES: readonly { value: TaskScope; label: string }[] = [
  { value: "mine", label: "My tasks" },
  { value: "team", label: "Team" },
];

/** The heading the page draws: the switch when the org has more than one
 * member, the plain "My tasks" when the person is alone in it, and, while
 * the member list is still on its way, the name of the list on screen. */
export type ScopeHeading = "switch" | "alone" | "pending";

/** `members` is the org's member count, undefined until the whole list is in. */
export function scopeHeadingKind(members: number | undefined): ScopeHeading {
  if (members === undefined) return "pending";
  return members > 1 ? "switch" : "alone";
}

/** The list a page shows: the one the person picked last, only where the
 * switch shows; a person alone in an org sees their own. The server's
 * `mine` is every task assigned to the person, or unassigned and created by
 * them, which in an org of one is every task in it. Until the count is
 * known, the pick stands. */
export function shownScope(picked: TaskScope, members: number | undefined): TaskScope {
  return scopeHeadingKind(members) === "alone" ? "mine" : picked;
}

/** The heading a screen reader hears for the list on screen. */
export function scopeHeading(scope: TaskScope): string {
  return scope === "team" ? "Team tasks" : "My tasks";
}
