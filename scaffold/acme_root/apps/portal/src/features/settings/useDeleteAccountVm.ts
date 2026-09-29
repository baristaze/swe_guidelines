import { useState } from "react";
import type { MeView, OwnedOrg } from "../../api";
import { forgetSession } from "../../app/forgetSession";
import { useDeleteAccount } from "../../queries/tenancy";
import { noteAccountDeleted, noteSignedOut } from "../../store/signInState";
import { deleteAccount } from "./deleteAccount";
import { confirms, strandedHere } from "./deleteAccountModel";

/** The "Delete my account" card: the person opens it, types their email,
 * and confirms. The deletion leaves the portal: the browser goes to the
 * provider's logout, or straight to `/signed-out`, which says the account is
 * gone. A refusal stays on the card, said in one line. */
export function useDeleteAccountVm(me: MeView | undefined) {
  const remove = useDeleteAccount();
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [refusal, setRefusal] = useState<string | null>(null);
  const [stranded, setStranded] = useState<OwnedOrg[]>([]);
  const email = me?.user.email;

  const confirm = async () => {
    if (!confirms(typed, email)) return;
    setRefusal(null);
    setStranded([]);
    const outcome = await deleteAccount({
      remove: () =>
        remove.mutateAsync({ email: typed, return_to: `${window.location.origin}/signed-out` }),
      note: () => {
        noteAccountDeleted();
        noteSignedOut();
      },
      forget: forgetSession,
      leave: (url) => window.location.assign(url ?? "/signed-out"),
    });
    if (!outcome.deleted) {
      setRefusal(outcome.refusal);
      setStranded(outcome.stranded);
    }
  };

  return {
    email,
    open,
    start: () => setOpen(true),
    cancel: () => {
      setOpen(false);
      setTyped("");
      setRefusal(null);
      setStranded([]);
    },
    typed,
    setTyped,
    mayConfirm: confirms(typed, email) && !remove.isPending,
    deleting: remove.isPending,
    confirm,
    refusal,
    /** The refusal names the org this tab is in: its ways out are on this page. */
    strandedHere: strandedHere(stranded, me?.org.id),
  };
}

export type DeleteAccountVm = ReturnType<typeof useDeleteAccountVm>;
