import { useRef } from "react";
import { useNavigate } from "react-router-dom";
import type { MembershipChoiceView } from "@acme/client";
import { useMe, useMyMemberships, useSwitchOrg } from "../queries/tenancy";
import { useNoticesStore } from "../store/notices";
import { adoptSession } from "./adoptSession";
import { forgetSession, holdSession } from "./forgetSession";
import { inFlight, oneAtATime } from "./oneAtATime";
import { chipChoices } from "./orgChipModel";
import { switchOrg } from "./switchOrg";

export function useOrgChipVm() {
  const navigate = useNavigate();
  const me = useMe();
  const memberships = useMyMemberships();
  const exchange = useSwitchOrg();
  const notify = useNoticesStore((s) => s.notify);
  const switching = useRef(inFlight());
  const choices = chipChoices(memberships.isPending ? undefined : memberships.data, me.data?.org.id);

  // One pick, one exchange: a second click while the first is in flight is dropped.
  const pick = async (membership: MembershipChoiceView) => {
    const outcome = await oneAtATime(switching.current, () =>
      switchOrg({
        exchange: () => exchange.mutateAsync(membership.org.id),
        hold: holdSession,
        adopt: adoptSession,
        forget: forgetSession,
        report: notify,
      }),
    );
    // The new tenant starts on its home page; a page of the old one may not exist here.
    if (outcome === "switched") navigate("/", { replace: true });
  };

  return {
    orgName: me.data?.org.name ?? "Acme",
    personal: me.data?.org.kind === "personal",
    role: me.data?.role,
    canOpen: me.data !== undefined,
    canSwitch: choices.canSwitch,
    others: choices.others,
    switching: exchange.isPending,
    pick,
    newOrg: () => navigate("/orgs/new"),
  };
}
