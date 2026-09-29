import { useState } from "react";
import { useLocation } from "react-router-dom";
import { errorMessage } from "../../app/errorMessage";
import { useDevSignIn } from "../../queries/tenancy";
import { checkEmail, landingPath } from "./signInModel";
import { useEnterOrg } from "./useEnterOrg";

/** `/login/dev`: the local stack's sign-in by address alone, for the seeded
 * people and anyone a developer names. A deployed API does not serve it. */
export function useDevSignInVm() {
  const location = useLocation();
  const signIn = useDevSignIn();
  const org = useEnterOrg();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setError(null);
    const refused = checkEmail(email);
    if (refused) {
      setError(refused);
      return;
    }
    try {
      const issued = await signIn.mutateAsync({ email: email.trim(), display_name: name.trim() });
      const said = await org.signedIn(issued, landingPath((location.state as { from?: unknown } | null)?.from));
      if (said) setError(said);
    } catch (caught) {
      setError(errorMessage(caught, "Sign-in failed."));
    }
  };

  return {
    email,
    name,
    error,
    choice: org.choice,
    busy: signIn.isPending || org.entering,
    setEmail,
    setName,
    submit,
    pick: org.pick,
  };
}
