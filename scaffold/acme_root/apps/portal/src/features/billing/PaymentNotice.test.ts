// @vitest-environment jsdom
// The notice of a payment that failed: an owner or an admin sees the
// sentence and the way to a new payment method; a member, and an org that
// paid, see nothing. The queries are stubbed; this case is about the notice.
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { BillingView } from "../../api";
import { PaymentNotice } from "./PaymentNotice";

const state = vi.hoisted(() => ({
  billing: null as Partial<BillingView> | null,
  mutateAsync: vi.fn<(flow?: string) => Promise<{ url: string }>>(() => Promise.resolve({ url: "https://billing.test/p" })),
}));

vi.mock("../../queries/billing", () => ({
  useBilling: () => ({ data: state.billing }),
  useOpenBillingPortal: () => ({ mutateAsync: state.mutateAsync, isPending: false }),
}));
vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);

const PAST_DUE: Partial<BillingView> = {
  plan: "team",
  paid_plan: "team",
  status: "past_due",
  payment_failed: true,
  can_manage: true,
};

let root: ReturnType<typeof createRoot>;
const container = document.createElement("div");
document.body.append(container);

async function draw(billing: Partial<BillingView> | null) {
  state.billing = billing;
  await act(async () => root.render(createElement(PaymentNotice)));
}

beforeEach(() => {
  state.mutateAsync.mockClear();
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.render(null));
});

it("tells an owner or an admin, and opens the processor's page on a new payment method", async () => {
  await draw(PAST_DUE);
  const banner = container.querySelector("[role=status]")!;
  expect(banner.textContent).toContain("A payment failed.");
  expect(banner.textContent).toContain("your Team plan did not go through");
  const button = banner.querySelector("button")!;
  expect(button.textContent).toBe("Update payment method");
  await act(async () => button.click());
  expect(state.mutateAsync).toHaveBeenCalledWith("payment_method_update");
});

it("says why when the processor's page did not open", async () => {
  state.mutateAsync.mockRejectedValueOnce(new Error("offline"));
  await draw(PAST_DUE);
  await act(async () => container.querySelector("button")!.click());
  expect(container.querySelector("[role=alert]")!.textContent).toBe("The payment page did not open.");
});

it("tells a member nothing, since a member cannot fix it", async () => {
  await draw({ ...PAST_DUE, can_manage: false });
  expect(container.innerHTML).toBe("");
});

it("is gone once a payment went through, and before the plan is read", async () => {
  await draw({ ...PAST_DUE, status: "active", payment_failed: false });
  expect(container.innerHTML).toBe("");
  await draw(null);
  expect(container.innerHTML).toBe("");
});
