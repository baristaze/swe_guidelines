import type { IssuedLoginView, MembershipChoiceView } from "../../api";
import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { adoptSession } from "../../app/adoptSession";
import { errorMessage } from "../../app/errorMessage";
import { inFlight, oneAtATime } from "../../app/oneAtATime";
import { takeUpSignIn } from "../../app/takeUpSignIn";
import { useEndReplacedSession, useExchangeSession } from "../../queries/tenancy";
import { useNoticesStore } from "../../store/notices";
import { useSessionStore } from "../../store/session";
import { chooseOrg, landingPath, type OrgChoice } from "./signInModel";

/** The step every sign-in ends in: the person's places, one goes straight
 * in, several show the picker, and the exchange turns the choice into the
 * one session the tab holds. */
export function useEnterOrg() {
  const navigate = useNavigate();
  const notify = useNoticesStore((s) => s.notify);
  const exchange = useExchangeSession();
  const endReplaced = useEndReplacedSession();
  const [choice, setChoice] = useState<OrgChoice>({ kind: "none" });
  const [held, setHeld] = useState<{ token: string; returnTo: string } | null>(null);
  const choosing = useRef(inFlight());

  const enter = async (token: string, membership: MembershipChoiceView, returnTo: string) => {
    const issued = await exchange.mutateAsync({ loginToken: token, body: { org_id: membership.org.id } });
    // A tab already signed in (another org, another person) drops that
    // session here, so nothing fetched under it is shown under this one,
    // and the server ends it after, so it does not outlive the tab's hold.
    void takeUpSignIn(issued, {
      held: () => useSessionStore.getState().token,
      adopt: adoptSession,
      end: (held) => endReplaced.mutateAsync(held),
    });
    navigate(landingPath(returnTo), { replace: true });
  };

  /** Goes on with a sign-in; the answer is what to say, or null. */
  const signedIn = async (issued: IssuedLoginView, returnTo: string): Promise<string | null> => {
    const next = chooseOrg(issued.memberships);
    setHeld({ token: issued.token, returnTo });
    setChoice(next);
    if (next.kind === "none") return "This account belongs to no organization yet.";
    if (next.kind === "single") await enter(issued.token, next.membership, returnTo);
    return null;
  };

  // Choosing an org can be refused too (the sign-in expired, the API is
  // away); the refusal is said the way a failed write is. One choice sends
  // one exchange: a click while one is in flight is dropped.
  const pick = async (membership: MembershipChoiceView) => {
    if (!held) return;
    await oneAtATime(choosing.current, async () => {
      try {
        await enter(held.token, membership, held.returnTo);
      } catch (caught) {
        notify(errorMessage(caught, "Sign-in failed."));
      }
    });
  };

  return { choice, signedIn, pick, entering: exchange.isPending };
}
