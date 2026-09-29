// Pure: who sees the "Delete this organization" card, what it says, and when
// it lets the owner go on. The owner types the org's name to say they mean
// it; the server checks the same, forgiving surrounding space and nothing else.
import type { MeView } from "../../api";

/** What happens to an org's data, said before it is deleted. */
export const ORG_GONE_LINE = "Its data is kept for 30 days, then purged, and gone from backups 7 days after that.";

/** Only an owner deletes an org, and only a team org: a personal org goes
 * with its person's account. */
export function mayDeleteOrg(me: MeView | undefined): boolean {
  return me?.role === "owner" && me.org.kind === "team";
}

/** Whether what was typed is the org's name, spelled as it is. */
export function confirmsName(typed: string, name: string | undefined): boolean {
  if (!name) return false;
  return typed.trim() === name.trim();
}
