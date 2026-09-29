import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { errorMessage } from "../../app/errorMessage";
import { useFinishSignIn } from "../../queries/tenancy";
import { consumeSignIn } from "../../store/signInState";
import { callbackStep, type CallbackStep } from "./signInModel";
import { useEnterOrg } from "./useEnterOrg";

/** `/auth/callback`: the provider sends the browser back here with a code
 * and the state. Only a state this tab stored goes on, and the code is
 * handed to the API once, which exchanges it server-side. */
/** Each callback address is decided once per page load, so a render React
 * runs twice consumes the stored state once and both see the same step. */
const decided = new Map<string, CallbackStep>();

function decide(search: string): CallbackStep {
  let step = decided.get(search);
  if (step === undefined) {
    step = callbackStep(search, (state) => consumeSignIn(state));
    decided.set(search, step);
  }
  return step;
}

export function useCallbackVm() {
  const location = useLocation();
  const navigate = useNavigate();
  const finish = useFinishSignIn();
  const org = useEnterOrg();
  const [failure, setFailure] = useState<string | null>(null);
  const step = decide(location.search);
  const ran = useRef(false);

  useEffect(() => {
    // The code is good once: StrictMode's second effect must not spend it again.
    if (ran.current) return;
    ran.current = true;
    if (step.kind === "refused") return;
    void (async () => {
      try {
        const issued = await finish.mutateAsync({
          code: step.code,
          code_verifier: step.pending.codeVerifier,
          invitation_token: step.pending.invitationToken,
        });
        const said = await org.signedIn(issued, step.pending.returnTo);
        if (said) setFailure(said);
      } catch (caught) {
        setFailure(errorMessage(caught, "Sign-in failed."));
      }
    })();
    // Run once per visit; the address is read at the start.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return {
    error: step.kind === "refused" ? step.message : failure,
    choice: org.choice,
    pick: org.pick,
    busy: finish.isPending || org.entering,
    again: () => navigate("/login", { replace: true }),
  };
}
