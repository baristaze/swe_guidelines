import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { errorMessage } from "../../app/errorMessage";
import { runtimeConfig } from "../../app/config";
import { useStartSignIn } from "../../queries/tenancy";
import { newState, rememberSignIn, takeAccountDeleted, takeSignedOut } from "../../store/signInState";
import { landingPath, readStart } from "./signInModel";

/** `/login`, the identity provider's "initiate login" address: it starts a
 * sign-in at once. The tab keeps a fresh state with where to land and the
 * invitation's token, and the browser goes to the provider. `/signed-out`,
 * where the provider's logout sends the browser back, is the same page
 * waiting for the person to ask. */
export function useLoginVm({ arrivedSignedOut = false }: { arrivedSignedOut?: boolean } = {}) {
  const location = useLocation();
  const start = useStartSignIn();
  const [error, setError] = useState<string | null>(null);
  const devSignIn = runtimeConfig().devSignIn;
  const asked = readStart(location.search, devSignIn);
  const returnTo = landingPath((location.state as { from?: unknown } | null)?.from);
  const started = useRef(false);
  // Read once per visit: a tab that just signed out waits to be asked.
  const [justSignedOut] = useState(() => takeSignedOut() || arrivedSignedOut);
  // Read once too: a tab whose account was just deleted says so, and waits.
  const [accountDeleted] = useState(() => takeAccountDeleted());
  const wait = asked.offerDev || justSignedOut || accountDeleted;

  const go = useCallback(async () => {
    setError(null);
    const state = newState();
    try {
      const answer = await start.mutateAsync({
        redirect_uri: `${window.location.origin}/auth/callback`,
        state,
        invitation_token: asked.invitationToken,
        sign_up: asked.signUp,
      });
      // Kept before the browser leaves: the callback needs the state to know
      // the round trip is this tab's, and the verifier to redeem the code.
      rememberSignIn(state, {
        returnTo,
        invitationToken: asked.invitationToken,
        codeVerifier: answer.code_verifier,
      });
      window.location.assign(answer.authorization_url);
    } catch (caught) {
      setError(errorMessage(caught, "Sign-in could not start."));
    }
    // The inputs are read from the address once; a retry starts the same sign-in again.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [returnTo, asked.invitationToken, asked.signUp]);

  useEffect(() => {
    // Once per visit, StrictMode's second effect included.
    if (wait || started.current) return;
    started.current = true;
    void go();
  }, [wait, go]);

  return {
    error,
    wait,
    signedOut: justSignedOut,
    accountDeleted,
    devSignIn,
    retry: () => void go(),
  };
}
