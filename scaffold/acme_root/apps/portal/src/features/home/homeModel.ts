// Pure: what the home card says. The org, the person, and their role come
// from `me`; the count comes from the org's member list. No React, no fetch.
import type { MeView, Role } from "@acme/client";

export interface HomeCard {
  orgName: string;
  personName: string;
  role: Role;
  members: string;
}

/** "1 member", "3 members". */
export function membersLine(count: number): string {
  return `${count} ${count === 1 ? "member" : "members"}`;
}

/** A person with no display name is shown by their email. */
export function homeCard(me: MeView, memberCount: number): HomeCard {
  return {
    orgName: me.org.name,
    personName: me.user.display_name.trim() || me.user.email,
    role: me.role,
    members: membersLine(memberCount),
  };
}
