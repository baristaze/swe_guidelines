import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { adoptSession } from "../../app/adoptSession";
import { forgetSession, holdSession } from "../../app/forgetSession";
import { switchOrg } from "../../app/switchOrg";
import { useCreateOrg, useSwitchOrg } from "../../queries/tenancy";
import { useNoticesStore } from "../../store/notices";
import { checkNewOrg, newOrgBody, newOrgRefusal, suggestSlug } from "./newOrgModel";

export function useNewOrgVm() {
  const navigate = useNavigate();
  const create = useCreateOrg();
  const exchange = useSwitchOrg();
  const notify = useNoticesStore((s) => s.notify);
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setError(null);
    const form = { name, slug };
    const check = checkNewOrg(form);
    if (!check.ok) {
      setError(check.message);
      return;
    }
    let orgId: string;
    try {
      orgId = (await create.mutateAsync(newOrgBody(form))).org.id;
    } catch (caught) {
      setError(newOrgRefusal(caught));
      return;
    }
    // The org is made and the person owns it; the switch there is the
    // exchange, the same one the org chip runs.
    const outcome = await switchOrg({
      exchange: () => exchange.mutateAsync(orgId),
      hold: holdSession,
      adopt: adoptSession,
      forget: forgetSession,
      report: notify,
    });
    if (outcome === "switched") navigate("/", { replace: true });
  };

  return {
    name,
    slug,
    slugPlaceholder: suggestSlug(name) || "made from the name",
    error,
    busy: create.isPending || exchange.isPending,
    setName,
    setSlug,
    submit,
    cancel: () => navigate(-1),
  };
}
