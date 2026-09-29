// Pure: what the "Delete my account" card says and when it lets the person
// go on. The person types their account's email to say they mean it; the
// server checks the same, forgiving space and letter case as it does.
import { ApiError, type OwnedOrg } from "../../api";
import { errorMessage } from "../../app/errorMessage";

/** What happens to an account's data, said before and after. */
export const GONE_LINE = "Gone now, and gone from backups within 7 days.";

/** Whether what was typed is the account's email. */
export function confirms(typed: string, email: string | undefined): boolean {
  if (!email) return false;
  return typed.trim().toLowerCase() === email.trim().toLowerCase();
}

/** The names of a list, as a sentence reads them: "A", "A and B", "A, B, and C". */
export function listed(names: string[]): string {
  if (names.length <= 1) return names.join("");
  if (names.length === 2) return `${names[0]} and ${names[1]}`;
  return `${names.slice(0, -1).join(", ")}, and ${names[names.length - 1]}`;
}

/** The orgs a refusal says the person is the last owner of; empty otherwise. */
export function ownedAlone(caught: unknown): OwnedOrg[] {
  return caught instanceof ApiError && caught.code === "last_owner" ? caught.lastOwnerOf : [];
}

/** The one line a refusal is said in. The last owner of a team org is told
 * which ones, and the two ways out of each: hand it on, or delete it. Both
 * are done in that org's Settings; anything else is the server's words. */
export function refusalText(caught: unknown): string {
  const orgs = ownedAlone(caught);
  if (orgs.length > 0) {
    const where = orgs.length === 1 ? "" : "In each, ";
    const make = orgs.length === 1 ? "Make" : "make";
    return (
      `You are the last owner of ${listed(orgs.map((org) => org.name))}. ` +
      `${where}${make} someone else an owner, or delete the organization, first. ` +
      `Both are done in that organization's Settings.`
    );
  }
  return errorMessage(caught, "Your account was not deleted.");
}

/** Whether the org this tab is in is one the refusal names: its Settings is
 * this page, so the card links to the members and to the org's deletion. */
export function strandedHere(orgs: readonly OwnedOrg[], currentOrgId: string | undefined): boolean {
  return currentOrgId !== undefined && orgs.some((org) => org.id === currentOrgId);
}
