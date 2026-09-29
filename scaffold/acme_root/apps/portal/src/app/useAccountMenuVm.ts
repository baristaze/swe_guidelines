import { useNavigate } from "react-router-dom";
import { useLogout, useMe } from "../queries/tenancy";
import { useNoticesStore } from "../store/notices";
import { usePreferencesStore } from "../store/preferences";
import { noteSignedOut } from "../store/signInState";
import { shortEmail } from "./accountModel";
import { forgetSession } from "./forgetSession";
import { signOut } from "./signOut";
import { THEME_CHOICES } from "./themeModel";

export function useAccountMenuVm() {
  const navigate = useNavigate();
  const me = useMe();
  const logout = useLogout();
  const notify = useNoticesStore((s) => s.notify);
  const theme = usePreferencesStore((s) => s.theme);
  const setTheme = usePreferencesStore((s) => s.setTheme);

  // The server session is revoked, then the token and the cache go; the
  // realtime channel closes with the token. A sign-out finishes here even
  // when the server cannot be reached. A session signed in through the
  // identity provider then sends the browser to its logout, which ends the
  // provider's session and comes back to `/signed-out`. Until the browser
  // leaves, and for a session with nothing to end there, `/login` waits for
  // the person to ask rather than starting a sign-in.
  const forget = () => {
    noteSignedOut();
    forgetSession();
  };
  const leave = () =>
    void signOut({
      revoke: () => logout.mutateAsync({ return_to: `${window.location.origin}/signed-out` }),
      forget,
      report: notify,
      leave: (url) => window.location.assign(url),
    });

  const email = me.data?.user.email ?? "";
  return {
    email,
    shortEmail: shortEmail(email),
    orgName: me.data?.org.name ?? "",
    ready: me.data !== undefined,
    theme,
    themes: THEME_CHOICES,
    setTheme,
    openSettings: () => navigate("/settings"),
    signOut: leave,
    signingOut: logout.isPending,
  };
}

export type AccountMenuVm = ReturnType<typeof useAccountMenuVm>;
