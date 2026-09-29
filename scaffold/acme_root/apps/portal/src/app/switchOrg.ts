// The switch as one flow with its effects handed in, so it runs in a test
// without React. The exchange is presented with the session this tab holds,
// so it goes first, while that token is still held; the server ends that
// session in the same write. The server also closes that session's socket
// with 4401 as it ends it, and the close can arrive before the answer, so
// the session is held while the exchange runs: a refusal of it waits for the
// answer instead of signing the tab out. Only with the answer in hand is the
// old tenant dropped (the token, every cached answer) and the new session
// taken up. A refusal leaves the tab where it was, and one that says the
// held session is gone signs the tab out; anything else is said.
import { ApiError, type IssuedSessionView } from "../api";
import { errorMessage } from "./errorMessage";
import type { SessionHold } from "./forgetSession";

export interface SwitchEffects {
  /** The exchange, presented with the current session and the chosen org. */
  exchange: () => Promise<IssuedSessionView>;
  /** Holds back a refusal of the current session while the exchange runs. */
  hold: () => SessionHold;
  /** Drops the old tenant and takes up the new session. */
  adopt: (issued: IssuedSessionView) => void;
  /** Signs the tab out: the session it held is gone. */
  forget: () => void;
  /** Says what went wrong in one line. */
  report: (message: string) => void;
}

export type SwitchOutcome = "switched" | "signed_out" | "refused";

export async function switchOrg(effects: SwitchEffects): Promise<SwitchOutcome> {
  const hold = effects.hold();
  let issued: IssuedSessionView;
  try {
    issued = await effects.exchange();
  } catch (cause) {
    const refused = hold.release();
    if (refused || (cause instanceof ApiError && cause.status === 401)) {
      effects.forget();
      return "signed_out";
    }
    effects.report(errorMessage(cause, "Switching organizations failed."));
    return "refused";
  }
  // Taken up before the hold ends: a late refusal of the old session then
  // names a token the tab no longer holds, and is dropped.
  effects.adopt(issued);
  hold.release();
  return "switched";
}
