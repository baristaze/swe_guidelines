import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { BillingView, OpenPortalRequest, Plan, RedirectView } from "../api";
import { api } from "../app/api";
import { keys } from "./keys";

/** The page a checkout or the processor's portal comes back to. The API
 * refuses any origin but the portal's own, so it is always this one. */
export function billingReturnUrl(origin: string = window.location.origin): string {
  return `${origin}/settings/billing`;
}

/** A task the processor's page opens on. */
export type PortalFlow = NonNullable<OpenPortalRequest["flow"]>;

/** Where a hosted page is opened; a test hands in its own. */
export type Go = (url: string) => void;

const leave: Go = (url) => window.location.assign(url);

export function useBilling() {
  return useQuery({
    queryKey: keys.billing,
    queryFn: ({ signal }) => api.get<BillingView>("/v1/billing", { signal }),
  });
}

/** A checkout for `plan`, under an idempotency key, then off to the processor's page. */
export function useStartCheckout(go: Go = leave) {
  return useMutation({
    mutationFn: (plan: Plan) =>
      api.post<RedirectView>(
        "/v1/billing/checkout",
        { plan, return_url: billingReturnUrl() },
        { idempotencyKey: crypto.randomUUID() },
      ),
    onSuccess: (redirect) => go(redirect.url),
  });
}

/** The processor's page for payment methods, invoices, and a change of plan;
 * with a flow, the page opens on that one task and comes back when it is done. */
export function useOpenBillingPortal(go: Go = leave) {
  return useMutation({
    mutationFn: (flow?: PortalFlow) =>
      api.post<RedirectView>("/v1/billing/portal", { return_url: billingReturnUrl(), flow: flow ?? null }),
    onSuccess: (redirect) => go(redirect.url),
  });
}

function useBillingChange(path: "/v1/billing/cancel" | "/v1/billing/resume") {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<BillingView>(path),
    onSuccess: (billing) => queryClient.setQueryData(keys.billing, billing),
  });
}

/** The paid plan ends at its period's end; the answer is the plan as it now stands. */
export const useCancelPlan = () => useBillingChange("/v1/billing/cancel");
/** Takes a cancellation back before the period ends. */
export const useResumePlan = () => useBillingChange("/v1/billing/resume");
