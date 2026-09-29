// The sign-out as one flow with its effects handed in, so it runs in a test
// without React: the server session is revoked while the token is still
// held, and the session is forgotten here whatever the server said. A 401
// means the session was gone already; any other refusal is said, since the
// server session may outlive the sign-out until it expires. When the server
// answers with the identity provider's logout, the browser goes there last,
// so the provider's own session ends too and the next sign-in on this
// browser asks who it is.
import { ApiError, type SignedOutView } from "../api";
import { errorMessage } from "./errorMessage";

export const NOT_REVOKED_MESSAGE = "Signed out here; the server did not revoke the session.";

export interface SignOutEffects {
  /** The logout call, made with the token the session still holds. */
  revoke: () => Promise<Pick<SignedOutView, "provider_logout_url">>;
  /** Drops the token and everything fetched under it. */
  forget: () => void;
  /** Says what went wrong in one line. */
  report: (message: string) => void;
  /** Sends the browser to the identity provider's logout, which ends its
   * session and sends the browser back to the portal's `/signed-out`. */
  leave: (url: string) => void;
}

export type SignOutOutcome = "revoked" | "already_gone" | "not_revoked";

/** The server no longer knows the session: there was nothing left to revoke. */
export function isAlreadyGone(cause: unknown): boolean {
  return cause instanceof ApiError && cause.status === 401;
}

export async function signOut(effects: SignOutEffects): Promise<SignOutOutcome> {
  let outcome: SignOutOutcome = "revoked";
  let providerLogout: string | null = null;
  try {
    providerLogout = (await effects.revoke()).provider_logout_url ?? null;
  } catch (cause) {
    outcome = isAlreadyGone(cause) ? "already_gone" : "not_revoked";
    if (outcome === "not_revoked") effects.report(errorMessage(cause, NOT_REVOKED_MESSAGE));
  } finally {
    effects.forget();
  }
  if (providerLogout) effects.leave(providerLogout);
  return outcome;
}
