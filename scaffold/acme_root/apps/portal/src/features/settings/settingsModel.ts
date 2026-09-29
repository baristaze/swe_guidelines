// Pure: rows, formatting, and gating predicates for the settings screen.
import type { ApiKeyView, InvitationView, MembershipView, MeView, Role, UserView } from "../../api";

export interface MemberRow {
  id: string;
  name: string;
  email: string;
  joined: string;
  /** The member's role; null while the memberships are on the way. */
  role: Role | null;
  /** The roles the signed-in member may give this one; empty when they may
   * not change this member's role at all. */
  roles: Role[];
}

export type KeyState = "active" | "expired" | "revoked";

export interface ApiKeyRow {
  id: string;
  name: string;
  role: string;
  state: KeyState;
  expires: string;
}

export function formatDate(iso: string): string {
  return iso.slice(0, 10);
}

export function memberRows(users: UserView[], memberships: MembershipView[] = [], me?: MeView): MemberRow[] {
  const roleOf = new Map(memberships.map((membership) => [membership.user_id, membership.role]));
  return users.map((user) => {
    const role = roleOf.get(user.id) ?? null;
    return {
      id: user.id,
      name: user.display_name,
      email: user.email,
      joined: formatDate(user.created_at),
      role,
      roles: grantableRoles(me, user.id, role),
    };
  });
}

export function keyState(key: ApiKeyView, now: Date): KeyState {
  if (key.deleted_at) return "revoked";
  if (new Date(key.expires_at).getTime() <= now.getTime()) return "expired";
  return "active";
}

export function apiKeyRows(keys: ApiKeyView[], now: Date): ApiKeyRow[] {
  return keys.map((key) => ({
    id: key.id,
    name: key.name,
    role: key.role,
    state: keyState(key, now),
    expires: formatDate(key.expires_at),
  }));
}

export function canManageKeys(me: MeView | undefined): boolean {
  return me?.permissions.includes("manage_keys") ?? false;
}

export function canManageMembers(me: MeView | undefined): boolean {
  return me?.permissions.includes("manage_members") ?? false;
}

/** Single sign-on is a team org's, set up by a member who manages members. */
export function ssoAvailable(me: MeView | undefined): boolean {
  return canManageMembers(me) && me?.org.kind === "team";
}

const INVITABLE: readonly Role[] = ["viewer", "member", "admin"];
const LADDER: readonly Role[] = ["viewer", "member", "admin", "owner"];
const RANK: Readonly<Record<string, number>> = { viewer: 0, member: 1, admin: 2, owner: 3 };

/** The roles the signed-in member may give another, as the server's rules
 * have it: only a member who manages members; never their own role; never a
 * member above their own role; and never a role above it. So an owner may
 * make someone else an owner, and an admin may not. The owner of a personal
 * org is kept by the server, which says so if asked. Empty when the member's
 * role cannot be changed from here. */
export function grantableRoles(me: MeView | undefined, userId: string, role: Role | null): Role[] {
  if (!me || role === null || !canManageMembers(me) || userId === me.user.id) return [];
  const mine = RANK[me.role] ?? -1;
  if ((RANK[role] ?? 99) > mine) return [];
  return LADDER.filter((choice) => (RANK[choice] ?? 99) <= mine);
}

/** The roles an invitation may carry: never above the caller's own, and
 * never owner, which an org has one of. */
export function invitableRoles(me: MeView | undefined): Role[] {
  if (!me) return [];
  const mine = RANK[me.role] ?? -1;
  return INVITABLE.filter((role) => (RANK[role] ?? 99) <= mine);
}

export interface InvitationRow {
  id: string;
  email: string;
  role: string;
  expires: string;
  expired: boolean;
}

export function invitationRows(invitations: InvitationView[], now: Date): InvitationRow[] {
  return invitations.map((invitation) => ({
    id: invitation.id,
    email: invitation.email,
    role: invitation.role,
    expires: formatDate(invitation.expires_at),
    expired: new Date(invitation.expires_at).getTime() <= now.getTime(),
  }));
}

/** The invite form asks for an address; the server checks the rest. */
export function checkInvite(email: string): string | null {
  return email.trim().includes("@") ? null : "Enter the email address to invite.";
}
