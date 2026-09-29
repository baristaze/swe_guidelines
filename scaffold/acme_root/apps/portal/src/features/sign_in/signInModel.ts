// Pure: what the sign-in pages decide. No React, no fetch, no storage.
import type { MembershipChoiceView } from "../../api";
import { byPlace } from "../../app/orgChipModel";
import type { PendingSignIn } from "../../store/signInState";

export type OrgChoice =
  | { kind: "none" }
  | { kind: "single"; membership: MembershipChoiceView }
  | { kind: "several"; memberships: MembershipChoiceView[] };

export function chooseOrg(memberships: MembershipChoiceView[]): OrgChoice {
  const [first] = memberships;
  if (first === undefined) return { kind: "none" };
  if (memberships.length === 1) return { kind: "single", membership: first };
  return { kind: "several", memberships: [...memberships].sort(byPlace) };
}

/** The pages a sign-in passes through, never a place to land on. */
const SIGN_IN_PATHS = new Set(["/login", "/login/dev", "/auth/callback", "/sign-in", "/sign-up"]);

/** Where to land after signing in: the page RequireAuth turned away, when it
 * named one of ours, and the task list otherwise. What the history state
 * carries is not trusted as a destination; only an in-app absolute path is. */
export function landingPath(from: unknown): string {
  if (typeof from !== "string") return "/";
  if (!from.startsWith("/") || from.startsWith("//") || from.startsWith("/\\")) return "/";
  if (SIGN_IN_PATHS.has(from.split(/[?#]/)[0] ?? "")) return "/";
  return from;
}

export interface SignInStart {
  invitationToken: string | null;
  signUp: boolean;
  /** `?dev=1` on a stack that offers the local sign-in: show the link and wait. */
  offerDev: boolean;
}

/** What `/login` was asked for. An invitation's link carries its token; the
 * sign-up screen is asked for with `screen_hint=sign-up`. */
export function readStart(search: string, devSignIn: boolean): SignInStart {
  const query = new URLSearchParams(search);
  return {
    invitationToken: query.get("invitation_token") || null,
    signUp: query.get("screen_hint") === "sign-up",
    offerDev: devSignIn && query.get("dev") === "1",
  };
}

export type CallbackStep =
  | { kind: "exchange"; code: string; pending: PendingSignIn }
  | { kind: "refused"; message: string };

/** What a callback may do. It goes on only with a code and a state this tab
 * stored when it started the sign-in; anything else is someone else's round
 * trip or a sign-in the provider refused, and is said, never exchanged. */
export function callbackStep(
  search: string,
  consume: (state: string) => PendingSignIn | null,
): CallbackStep {
  const query = new URLSearchParams(search);
  const error = query.get("error");
  const state = query.get("state");
  // The state is consumed whatever else the callback says, so it is never
  // used twice.
  const pending = state ? consume(state) : null;
  if (error) {
    const description = query.get("error_description");
    return { kind: "refused", message: description ? `Sign-in was refused: ${description}` : "Sign-in was refused." };
  }
  if (!state || pending === null) {
    return { kind: "refused", message: "This sign-in was not started in this tab. Start it again." };
  }
  const code = query.get("code");
  if (!code) return { kind: "refused", message: "The sign-in came back without a code. Start it again." };
  return { kind: "exchange", code, pending };
}

/** The local sign-in asks for an address and nothing else. */
export function checkEmail(email: string): string | null {
  return email.includes("@") ? null : "Enter an email address.";
}
