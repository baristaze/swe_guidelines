import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import type { Plan } from "../../api";
import {
  useBilling,
  useCancelPlan,
  useOpenBillingPortal,
  useResumePlan,
  useStartCheckout,
} from "../../queries/billing";
import { useNoticesStore } from "../../store/notices";
import { errorMessage } from "../../app/errorMessage";
import {
  billingActions,
  CHECKOUT_CANCELLED,
  CHECKOUT_DONE,
  checkoutFailure,
  checkoutReturn,
} from "./billingModel";

/** How often, and how many times, the page reads the plan again after a
 * checkout, until the processor's word has reached it; the push usually
 * lands first. */
const AFTER_CHECKOUT_MS = 2_000;
const AFTER_CHECKOUT_READS = 5;

export function useBillingVm() {
  const billing = useBilling();
  const checkout = useStartCheckout();
  const portal = useOpenBillingPortal();
  const cancelPlan = useCancelPlan();
  const resumePlan = useResumePlan();
  const notify = useNoticesStore((s) => s.notify);
  const location = useLocation();
  const navigate = useNavigate();
  const [confirmingCancel, setConfirmingCancel] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const { refetch } = billing;

  // Back from the processor's page: say so once, read the plan again a few
  // times, and take the query string off the address.
  const returned = checkoutReturn(location.search);
  useEffect(() => {
    if (returned === null) return;
    notify(returned === "done" ? CHECKOUT_DONE : CHECKOUT_CANCELLED);
    navigate(location.pathname, { replace: true });
    if (returned !== "done") return;
    let reads = 0;
    const timer = setInterval(() => {
      reads += 1;
      void refetch();
      if (reads >= AFTER_CHECKOUT_READS) clearInterval(timer);
    }, AFTER_CHECKOUT_MS);
    return () => clearInterval(timer);
  }, [returned, notify, navigate, location.pathname, refetch]);

  const run = async (write: () => Promise<unknown>, fallback: string, forCheckout = false) => {
    setFailure(null);
    try {
      await write();
    } catch (caught) {
      setFailure(forCheckout ? checkoutFailure(caught) : errorMessage(caught, fallback));
    }
  };

  return {
    billing: billing.data,
    loading: billing.isPending,
    error: billing.error,
    actions: billing.data ? billingActions(billing.data) : null,
    failure,
    busy: checkout.isPending || portal.isPending || cancelPlan.isPending || resumePlan.isPending,
    choose: (plan: Plan) => void run(() => checkout.mutateAsync(plan), "The checkout did not start.", true),
    manage: () => void run(() => portal.mutateAsync(), "The billing page did not open.", true),
    confirmingCancel,
    askCancel: () => setConfirmingCancel(true),
    keepCancel: () => setConfirmingCancel(false),
    cancel: () => {
      setConfirmingCancel(false);
      void run(() => cancelPlan.mutateAsync(), "The plan was not cancelled.");
    },
    resume: () => void run(() => resumePlan.mutateAsync(), "The plan was not kept."),
  };
}
