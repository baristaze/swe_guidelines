import { useState } from "react";
import { useNavigate } from "react-router-dom";
import type { MeView } from "../../api";
import { adoptSession } from "../../app/adoptSession";
import { forgetSession, holdSession } from "../../app/forgetSession";
import { useDeleteOrg } from "../../queries/tenancy";
import { useNoticesStore } from "../../store/notices";
import { deleteOrg } from "./deleteOrg";
import { confirmsName, mayDeleteOrg } from "./deleteOrgModel";

/** The "Delete this organization" card: an owner of a team org opens it,
 * types the org's name, and confirms. Everyone in the org loses it at once;
 * the owner lands in their personal org, told what went. */
export function useDeleteOrgVm(me: MeView | undefined) {
  const navigate = useNavigate();
  const remove = useDeleteOrg();
  const notify = useNoticesStore((s) => s.notify);
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [refusal, setRefusal] = useState<string | null>(null);
  const name = me?.org.name;

  const confirm = async () => {
    if (!confirmsName(typed, name)) return;
    setRefusal(null);
    const outcome = await deleteOrg({
      remove: () => remove.mutateAsync({ name: typed }),
      hold: holdSession,
      adopt: adoptSession,
      forget: forgetSession,
      land: (where) => {
        if (where === "home") {
          if (name) notify(`${name} is deleted.`);
          navigate("/", { replace: true });
        } else {
          navigate("/login", { replace: true });
        }
      },
    });
    if (!outcome.deleted && outcome.refusal) setRefusal(outcome.refusal);
  };

  return {
    shown: mayDeleteOrg(me),
    name,
    open,
    start: () => setOpen(true),
    cancel: () => {
      setOpen(false);
      setTyped("");
      setRefusal(null);
    },
    typed,
    setTyped,
    mayConfirm: confirmsName(typed, name) && !remove.isPending,
    deleting: remove.isPending,
    confirm,
    refusal,
  };
}

export type DeleteOrgVm = ReturnType<typeof useDeleteOrgVm>;
