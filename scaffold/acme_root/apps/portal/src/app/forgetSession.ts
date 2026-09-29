// Forgetting a session is one act: the token goes, and with it everything
// fetched under it, so the next person to sign in on this tab never sees the
// last one's answers. Every path that drops a session goes through here: the
// sign-out, a 401 from the API, and a 4401 close of the realtime socket. A
// sign-in over a tab that still holds one replaces it instead (takeUpSignIn).
import { useSessionStore } from "../store/session";
import { queryClient } from "./queryClient";

export function forgetSession(): void {
  useSessionStore.getState().clear();
  queryClient.clear();
}

/** A session the tab is handing over: a switch presents it, and the server
 * ends it in the same write that answers. */
interface Handover {
  token: string | null;
  refused: boolean;
}

let handover: Handover | null = null;

/** A refusal that belongs to a token the tab no longer holds says nothing
 * about the session it holds now: a switch ends the old session, and the
 * old socket's close can arrive after the new session is set. A refusal of
 * the session being handed over is the switch's own doing: the server
 * closes that session's socket with 4401 as it ends it, and the close can
 * arrive before the answer does. It is noted, and the switch decides. */
export function forgetSessionIfHeld(token: string): void {
  if (handover !== null && handover.token === token) {
    handover.refused = true;
    return;
  }
  if (useSessionStore.getState().token === token) forgetSession();
}

export interface SessionHold {
  /** Ends the hold; true when the held session was refused while it lasted. */
  release(): boolean;
}

/** Holds the session this tab has while a switch hands it over, so a
 * refusal of it does not sign the tab out before the switch has its answer. */
export function holdSession(): SessionHold {
  const held: Handover = { token: useSessionStore.getState().token, refused: false };
  handover = held;
  return {
    release: () => {
      if (handover === held) handover = null;
      return held.refused;
    },
  };
}
