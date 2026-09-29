// A sign-in over a tab that still holds a session, as one flow with its
// effects handed in, so it runs in a test without React. The new session is
// taken up first, so the tab is never without one and a failed sign-in never
// costs it the session it had. Then the session it replaced is ended at the
// server with its own token: the logout a sign-out makes. That is best
// effort. The new session stands whatever the server says, and a session the
// server could not end lapses when it expires. The logout's answer may name
// the identity provider's logout; it is not followed, since the person is
// signing in, not out, and the provider's session is the new sign-in's.
import type { IssuedSessionView } from "../api";
import { isAlreadyGone } from "./signOut";

export interface TakeUpEffects {
  /** The token the tab holds before the new session is taken up, if any. */
  held: () => string | null;
  /** Drops the old tenant and takes up the new session. */
  adopt: (issued: IssuedSessionView) => void;
  /** The logout, made with the replaced session's own token. */
  end: (token: string) => Promise<unknown>;
}

export type TakeUpOutcome = "nothing_held" | "ended" | "already_gone" | "not_ended";

/** Takes up the new session at once; the promise settles when the replaced
 * one is ended, or could not be. It never rejects. */
export function takeUpSignIn(issued: IssuedSessionView, effects: TakeUpEffects): Promise<TakeUpOutcome> {
  const held = effects.held();
  effects.adopt(issued);
  if (held === null || held === issued.token) return Promise.resolve("nothing_held");
  return effects.end(held).then(
    () => "ended" as const,
    (cause: unknown) => (isAlreadyGone(cause) ? "already_gone" : "not_ended"),
  );
}
