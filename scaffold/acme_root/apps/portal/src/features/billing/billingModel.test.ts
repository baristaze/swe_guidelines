import { describe, expect, it } from "vitest";
import { ApiError, type BillingView, type PlanOfferView } from "../../api";
import {
  billingActions,
  boundSentence,
  checkoutFailure,
  checkoutReturn,
  endsOnSentence,
  limitLines,
  monthlyCents,
  monthlyText,
  paymentFailedSentence,
  planName,
  priceText,
  renewsText,
  sourceText,
  storageText,
  usageText,
} from "./billingModel";

const GIB = 1024 ** 3;

const PLANS: PlanOfferView[] = [
  { plan: "free", flat_cents: 0, included_seats: null, per_seat_cents: 0, limits: { members: 1, api_keys: false, active_tasks: 10, storage_bytes: GIB } },
  { plan: "pro", flat_cents: 500, included_seats: null, per_seat_cents: 0, limits: { members: 1, api_keys: true, active_tasks: null, storage_bytes: 10 * GIB } },
  { plan: "team", flat_cents: 1000, included_seats: null, per_seat_cents: 0, limits: { members: 5, api_keys: true, active_tasks: null, storage_bytes: 50 * GIB } },
  { plan: "max", flat_cents: 3000, included_seats: 10, per_seat_cents: 300, limits: { members: null, api_keys: true, active_tasks: null, storage_bytes: 200 * GIB } },
];
const [FREE, PRO, TEAM, MAX] = PLANS as [PlanOfferView, PlanOfferView, PlanOfferView, PlanOfferView];

function billing(overrides: Partial<BillingView> = {}): BillingView {
  return {
    plan: "free",
    limits: FREE.limits,
    paid_plan: null,
    comped_plan: null,
    status: null,
    current_period_end: null,
    cancel_at_period_end: false,
    ends_at: null,
    plan_after: null,
    seats: 1,
    active_tasks: 3,
    storage_bytes: 0,
    monthly_cents: 0,
    payment_failed: false,
    can_manage: true,
    plans: PLANS,
    ...overrides,
  };
}

const PAYING = {
  plan: "pro",
  paid_plan: "pro",
  status: "active",
  current_period_end: "2026-10-22T10:00:00Z",
  monthly_cents: 500,
} as const;

describe("the plans' prices", () => {
  it("words each plan's monthly price", () => {
    expect(PLANS.map(priceText)).toEqual([
      "Free",
      "$5 a month",
      "$10 a month",
      "$30 a month up to 10 members, then $3 a member",
    ]);
  });

  it("prices Max at its volume tiers: ten seats are $30 and eleven $33", () => {
    expect(monthlyCents(MAX, 10)).toBe(3000);
    expect(monthlyCents(MAX, 11)).toBe(3300);
    expect(monthlyCents(MAX, 1)).toBe(3000);
    expect(monthlyCents(PRO, 3)).toBe(500);
    expect(monthlyCents(TEAM, 5)).toBe(1000);
    expect(monthlyText(3300)).toBe("$33 a month");
    expect(monthlyText(0)).toBe("Nothing");
  });

  it("says each plan's bounds in a line each", () => {
    expect(limitLines(FREE.limits)).toEqual(["1 member", "10 active tasks", "No API keys", "1 GB of files"]);
    expect(limitLines(MAX.limits)).toEqual(["Unlimited members", "Unlimited active tasks", "API keys", "200 GB of files"]);
  });
});

describe("the bound a refusal met", () => {
  it("is one plain sentence per lever", () => {
    expect(boundSentence({ lever: "active_tasks", plan: "free", limit: 10, suggested_plan: "pro" })).toBe("Free allows 10 active tasks.");
    expect(boundSentence({ lever: "members", plan: "free", limit: 1, suggested_plan: "team" })).toBe("Free allows 1 member.");
    expect(boundSentence({ lever: "members", plan: "team", limit: 5, suggested_plan: "max" })).toBe("Team allows 5 members.");
    expect(boundSentence({ lever: "api_keys", plan: "free", limit: 0, suggested_plan: "pro" })).toBe("Free includes no API keys.");
  });
});

describe("the billing page", () => {
  it("shows usage against the bound, or alone with none", () => {
    expect(usageText(3, 10)).toBe("3 of 10");
    expect(usageText(12, null)).toBe("12");
    expect(storageText(1536 * 1024, GIB)).toBe("1.5 MB of 1 GB");
    expect(planName("max")).toBe("Max");
    expect(planName(null)).toBe("Free");
  });

  it("says where the plan comes from", () => {
    expect(sourceText(billing())).toBe("Every org starts here");
    expect(sourceText(billing({ ...PAYING }))).toBe("Paid");
    expect(sourceText(billing({ plan: "max", comped_plan: "max" }))).toBe("Granted by Acme");
  });

  it("says when a paid plan renews, or when it ends and what follows", () => {
    expect(renewsText(billing({ ...PAYING }))).toBe("Renews on 2026-10-22.");
    const ending = billing({ ...PAYING, cancel_at_period_end: true, ends_at: "2026-10-22T10:00:00Z", plan_after: "free" });
    expect(renewsText(ending)).toBeNull();
    expect(endsOnSentence(ending)).toBe(
      "Your Pro plan ends on 2026-10-22. After that the org is on Free: nothing is deleted, creating past Free's bounds is refused until you upgrade, and API keys are refused while on Free.",
    );
    expect(endsOnSentence(billing({ ...PAYING }))).toBeNull();
  });

  it("offers a manager of a Free org the plans, and nothing to manage or cancel", () => {
    expect(billingActions(billing())).toEqual({ choose: true, manage: false, cancel: false, keep: false });
  });

  it("offers a manager of a paying org the processor's page and a cancel, not a second checkout", () => {
    expect(billingActions(billing({ ...PAYING }))).toEqual({ choose: false, manage: true, cancel: true, keep: false });
  });

  it("offers a manager of an ending plan to keep it, and no second cancel", () => {
    const ending = billing({ ...PAYING, ends_at: "2026-10-22T10:00:00Z", cancel_at_period_end: true });
    expect(billingActions(ending)).toEqual({ choose: false, manage: true, cancel: false, keep: true });
  });

  it("offers a member nothing to change", () => {
    expect(billingActions(billing({ ...PAYING, can_manage: false }))).toEqual({
      choose: false,
      manage: false,
      cancel: false,
      keep: false,
    });
  });

  it("reads the processor's return from the query string", () => {
    expect(checkoutReturn("?checkout=done")).toBe("done");
    expect(checkoutReturn("?checkout=cancelled")).toBe("cancelled");
    expect(checkoutReturn("?checkout=other")).toBeNull();
    expect(checkoutReturn("")).toBeNull();
  });

  it("says why a checkout did not start", () => {
    expect(checkoutFailure(new ApiError(503, "billing_unavailable", "internal error", "req_1"))).toBe(
      "Billing is not set up in this environment.",
    );
    expect(checkoutFailure(new ApiError(503, "payments_key_refused", "internal error", "req_3"))).toBe(
      "Billing is unavailable right now. Try again later. Reference: req_3",
    );
    expect(checkoutFailure(new ApiError(502, "payments_refused", "internal error", "req_2"))).toBe(
      "The payment processor refused (payments_refused). Reference: req_2",
    );
    expect(checkoutFailure(new Error("offline"))).toBe("The checkout did not start.");
  });
});

describe("a payment that failed", () => {
  it("tells an owner or an admin while the processor retries, naming the plan kept", () => {
    const sentence = paymentFailedSentence(billing({ ...PAYING, status: "past_due", payment_failed: true }));
    expect(sentence).toBe(
      "The last payment for your Pro plan did not go through. It is being tried again, and the org " +
        "keeps the plan while it is. Update the payment method to keep it.",
    );
  });

  it("tells them the org is on what is left once the retries stop", () => {
    const sentence = paymentFailedSentence(billing({ status: "unpaid", payment_failed: true }));
    expect(sentence).toBe(
      "The last payment did not go through and the retries have stopped, so the org is on " +
        "Free until it is paid. Update the payment method to pay it.",
    );
  });

  it("tells a member nothing, and nobody anything once it is paid", () => {
    expect(paymentFailedSentence(billing({ ...PAYING, status: "past_due", payment_failed: true, can_manage: false }))).toBeNull();
    expect(paymentFailedSentence(billing({ ...PAYING }))).toBeNull();
  });
});
