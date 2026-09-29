// The org's deletion as one flow with its effects handed in, so it runs in a
// test without React. The server deletes the org while the token is still
// held, and it ends that session in the same act: its socket closes with
// 4401 and can close before the answer comes, so the session is held while
// the deletion runs, as a switch holds it. With the answer in hand the tab
// takes up the session the server made in the owner's personal org, as a
// switch does, and lands there. When no session came back, the tab forgets
// the old one and the owner signs in again. A refusal changes nothing, and
// it is said; one that says the held session is gone signs the tab out.
import { ApiError, type IssuedSessionView, type OrgDeletedView } from "../../api";
import { errorMessage } from "../../app/errorMessage";
import type { SessionHold } from "../../app/forgetSession";

export interface DeleteOrgEffects {
  /** The deletion, made with the token the session still holds. */
  remove: () => Promise<Pick<OrgDeletedView, "session">>;
  /** Holds back a refusal of the current session while the deletion runs. */
  hold: () => SessionHold;
  /** Drops the old tenant and takes up the session in the personal org. */
  adopt: (issued: IssuedSessionView) => void;
  /** Drops the token and everything fetched under it. */
  forget: () => void;
  /** Sends the tab on: home to the personal org, or to sign in again. */
  land: (where: "home" | "sign_in") => void;
}

export type DeleteOrgOutcome = { deleted: true } | { deleted: false; refusal: string | null };

export async function deleteOrg(effects: DeleteOrgEffects): Promise<DeleteOrgOutcome> {
  const hold = effects.hold();
  let deleted: Pick<OrgDeletedView, "session">;
  try {
    deleted = await effects.remove();
  } catch (caught) {
    const refused = hold.release();
    if (refused || (caught instanceof ApiError && caught.status === 401)) {
      effects.forget();
      effects.land("sign_in");
      return { deleted: false, refusal: null };
    }
    return { deleted: false, refusal: errorMessage(caught, "The organization was not deleted.") };
  }
  if (deleted.session) {
    // Taken up before the hold ends: a late refusal of the old session then
    // names a token the tab no longer holds, and is dropped.
    effects.adopt(deleted.session);
    hold.release();
    effects.land("home");
  } else {
    hold.release();
    effects.forget();
    effects.land("sign_in");
  }
  return { deleted: true };
}
