// Pure: what the org chip offers. The current org comes from `me`; the
// person's places come from the memberships list. The chip always opens:
// it lists the other places to switch to, when there are any, and offers a
// new team org, which a person with one place needs most.
import type { MembershipChoiceView } from "../api";

export interface ChipChoices {
  canSwitch: boolean;
  /** The other orgs, the personal one first, then by name; the current one is not offered. */
  others: MembershipChoiceView[];
}

export function chipChoices(memberships: MembershipChoiceView[] | undefined, currentOrgId: string | undefined): ChipChoices {
  if (!memberships || !currentOrgId) return { canSwitch: false, others: [] };
  const others = memberships.filter((membership) => membership.org.id !== currentOrgId).sort(byPlace);
  return { canSwitch: others.length > 0, others };
}

/** The personal org first, then by name. */
export function byPlace(a: MembershipChoiceView, b: MembershipChoiceView): number {
  const personal = Number(b.org.kind === "personal") - Number(a.org.kind === "personal");
  return personal || a.org.name.localeCompare(b.org.name);
}

/** What a place is called beside its name: the role, and "personal" for the person's own. */
export function placeNote(membership: MembershipChoiceView): string {
  return membership.org.kind === "personal" ? `${membership.role} · personal` : membership.role;
}
