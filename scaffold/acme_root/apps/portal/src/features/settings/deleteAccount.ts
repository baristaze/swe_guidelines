// The deletion as one flow with its effects handed in, so it runs in a test
// without React. The server deletes the account while the token is still
// held. Once it has, the session is gone here too, the tab notes that the
// account was deleted so the page it lands on says so, and the browser goes
// to the identity provider's logout when the server named one, so the
// provider's session in this browser ends as well. A refusal changes
// nothing: the person is still signed in, and it is said.
import type { AccountDeletedView, OwnedOrg } from "../../api";
import { ownedAlone, refusalText } from "./deleteAccountModel";

export interface DeleteAccountEffects {
  /** The deletion, made with the token the session still holds. */
  remove: () => Promise<Pick<AccountDeletedView, "provider_logout_url">>;
  /** Notes, for the page the browser lands on, that the account is gone. */
  note: () => void;
  /** Drops the token and everything fetched under it. */
  forget: () => void;
  /** Sends the browser on: to the provider's logout, or to the signed-out page. */
  leave: (url: string | null) => void;
}

export type DeleteAccountOutcome =
  | { deleted: true }
  | { deleted: false; refusal: string; stranded: OwnedOrg[] };

export async function deleteAccount(effects: DeleteAccountEffects): Promise<DeleteAccountOutcome> {
  let providerLogout: string | null;
  try {
    providerLogout = (await effects.remove()).provider_logout_url ?? null;
  } catch (caught) {
    return { deleted: false, refusal: refusalText(caught), stranded: ownedAlone(caught) };
  }
  effects.note();
  effects.forget();
  effects.leave(providerLogout);
  return { deleted: true };
}
