// Pure: what the billing page and the upgrade dialog say. The numbers come
// from the server's one table of plans; this file only words them.
import { ApiError, type BillingView, type Plan, type PlanLimit, type PlanLimitsView, type PlanOfferView } from "../../api";
import { humanSize } from "../attachments/attachmentsModel";

export const PLAN_NAMES: Readonly<Record<Plan, string>> = {
  free: "Free",
  pro: "Pro",
  team: "Team",
  max: "Max",
};

export function planName(plan: string | null | undefined): string {
  if (!plan) return "Free";
  return PLAN_NAMES[plan as Plan] ?? plan.charAt(0).toUpperCase() + plan.slice(1);
}

function dollars(cents: number): string {
  const whole = cents / 100;
  return Number.isInteger(whole) ? `$${whole}` : `$${whole.toFixed(2)}`;
}

/** What a plan costs a month, as its price is shaped. */
export function priceText(offer: PlanOfferView): string {
  if (offer.flat_cents === 0 && offer.per_seat_cents === 0) return "Free";
  if (offer.included_seats !== null) {
    return `${dollars(offer.flat_cents)} a month up to ${offer.included_seats} members, then ${dollars(offer.per_seat_cents)} a member`;
  }
  return `${dollars(offer.flat_cents)} a month`;
}

/** What a per-seat plan bills at a seat count; a flat plan its flat amount. */
export function monthlyCents(offer: PlanOfferView, seats: number): number {
  if (offer.included_seats === null || seats <= offer.included_seats) return offer.flat_cents;
  return offer.per_seat_cents * seats;
}

export function monthlyText(cents: number): string {
  return cents === 0 ? "Nothing" : `${dollars(cents)} a month`;
}

function members(count: number): string {
  return count === 1 ? "1 member" : `${count} members`;
}

function gib(bytes: number): string {
  return `${Math.round(bytes / 1024 ** 3)} GB`;
}

/** A plan's bounds, one short line each. */
export function limitLines(limits: PlanLimitsView): string[] {
  return [
    limits.members === null ? "Unlimited members" : members(limits.members),
    limits.active_tasks === null ? "Unlimited active tasks" : `${limits.active_tasks} active tasks`,
    limits.api_keys ? "API keys" : "No API keys",
    `${gib(limits.storage_bytes)} of files`,
  ];
}

/** The bound a refusal met, said as a sentence. */
export function boundSentence(limit: PlanLimit): string {
  const plan = planName(limit.plan);
  switch (limit.lever) {
    case "active_tasks":
      return `${plan} allows ${limit.limit ?? 0} active tasks.`;
    case "members":
      return `${plan} allows ${members(limit.limit ?? 0)}.`;
    case "api_keys":
      return `${plan} includes no API keys.`;
    default:
      return `${plan} does not allow more ${limit.lever.replace(/_/g, " ")}.`;
  }
}

/** `n of limit`, or `n` with no bound. */
export function usageText(used: number, bound: number | null): string {
  return bound === null ? `${used}` : `${used} of ${bound}`;
}

/** The files kept against the plan's figure, `1.2 MB of 1 GB`. */
export function storageText(used: number, bound: number): string {
  return `${humanSize(used)} of ${Math.round(bound / 1024 ** 3)} GB`;
}

export function dateText(iso: string): string {
  return iso.slice(0, 10);
}

/** What happens when a paid plan set to end ends, or null. */
export function endsOnSentence(billing: BillingView): string | null {
  if (!billing.ends_at || !billing.paid_plan) return null;
  const after = planName(billing.plan_after);
  return (
    `Your ${planName(billing.paid_plan)} plan ends on ${dateText(billing.ends_at)}. ` +
    `After that the org is on ${after}: nothing is deleted, creating past ${after}'s bounds is refused until you upgrade` +
    (billing.plan_after === "free" || billing.plan_after === null ? ", and API keys are refused while on Free." : ".")
  );
}

/** Where the org's plan comes from. */
export function sourceText(billing: BillingView): string {
  if (billing.paid_plan && billing.comped_plan) return "Paid, and granted by Acme";
  if (billing.paid_plan) return "Paid";
  if (billing.comped_plan) return "Granted by Acme";
  return "Every org starts here";
}

export function renewsText(billing: BillingView): string | null {
  if (!billing.paid_plan || billing.ends_at || !billing.current_period_end) return null;
  return `Renews on ${dateText(billing.current_period_end)}.`;
}

export const ASK_AN_OWNER = "Ask an owner or an admin of this org to upgrade.";

export interface BillingActions {
  /** The plans list with a Choose button on each paid plan the org is not on. */
  choose: boolean;
  /** The processor's page: once the org has a paid plan. */
  manage: boolean;
  /** Only a paid plan not already set to end. */
  cancel: boolean;
  /** Only a paid plan set to end. */
  keep: boolean;
}

export function billingActions(billing: BillingView): BillingActions {
  const paid = billing.paid_plan !== null;
  if (!billing.can_manage) return { choose: false, manage: false, cancel: false, keep: false };
  return {
    // An org that pays changes plan on the processor's page, not by a second checkout.
    choose: !paid,
    manage: paid || billing.status !== null,
    cancel: paid && !billing.ends_at,
    keep: paid && billing.ends_at !== null,
  };
}

/** What a failed checkout says: billing not set up here, billing out of
 * reach until the processor's key is fixed, the processor's refusal with its
 * code and reference, or the server's own words. */
export function checkoutFailure(caught: unknown): string {
  if (caught instanceof ApiError) {
    if (caught.code === "billing_unavailable") return "Billing is not set up in this environment.";
    const reference = caught.requestId ? ` Reference: ${caught.requestId}` : "";
    if (caught.code === "payments_key_refused") {
      return `Billing is unavailable right now. Try again later.${reference}`;
    }
    if (caught.code === "payments_refused") {
      return `The payment processor refused (payments_refused).${reference}`;
    }
    return caught.message;
  }
  return "The checkout did not start.";
}

/** What the page says when the processor sends the person back. */
export function checkoutReturn(search: string): "done" | "cancelled" | null {
  const value = new URLSearchParams(search).get("checkout");
  return value === "done" || value === "cancelled" ? value : null;
}

export const CHECKOUT_DONE = "Payment received. Your plan updates in a moment.";
export const CHECKOUT_CANCELLED = "Checkout cancelled; nothing changed.";

/** What an owner or an admin is told when the processor could not collect
 * the latest invoice, or null when there is nothing to tell them. A member
 * cannot fix it, so a member is not told. */
export function paymentFailedSentence(billing: BillingView): string | null {
  if (!billing.payment_failed || !billing.can_manage) return null;
  if (billing.status === "unpaid") {
    return (
      "The last payment did not go through and the retries have stopped, so the org is on " +
      `${planName(billing.plan)} until it is paid. Update the payment method to pay it.`
    );
  }
  const plan = billing.paid_plan ? `your ${planName(billing.paid_plan)} plan` : "your plan";
  return (
    `The last payment for ${plan} did not go through. It is being tried again, and the org ` +
    "keeps the plan while it is. Update the payment method to keep it."
  );
}

export const UPDATE_PAYMENT_METHOD = "Update payment method";
