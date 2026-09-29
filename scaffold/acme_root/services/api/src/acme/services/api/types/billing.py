"""Wire types of billing: the org's plan and what it entitles the org to, the
plans on offer, where a person goes to pay or to manage what they pay for,
and the operator's grant."""

from datetime import datetime
from typing import Literal

from pydantic import Field

from acme.om.billing.types.account import SubscriptionStatus
from acme.om.billing.types.plan import Plan
from acme.services.api.types.common import RequestBody, View


class PlanLimitsView(View):
    """A plan's bounds; a null count is no bound."""

    members: int | None
    api_keys: bool
    active_tasks: int | None
    storage_bytes: int


class PlanOfferView(View):
    """One plan on offer: its bounds and its monthly price. A per-seat plan's
    `flat_cents` covers up to `included_seats` seats, and past them every
    seat is `per_seat_cents`."""

    plan: Plan
    limits: PlanLimitsView
    flat_cents: int
    included_seats: int | None
    per_seat_cents: int


class BillingView(View):
    """The org's plan now, where it comes from, and what the org uses of it.
    `ends_at` is set when a paid plan is set to end; the org is on
    `plan_after` from then, and keeps everything it has. `can_manage` says
    whether the caller may change the plan: an owner or an admin."""

    plan: Plan
    limits: PlanLimitsView
    paid_plan: Plan | None
    comped_plan: Plan | None
    status: SubscriptionStatus | None
    current_period_end: datetime | None
    cancel_at_period_end: bool
    ends_at: datetime | None
    plan_after: Plan | None
    seats: int
    """The org's active members, which a per-seat plan bills for."""
    active_tasks: int
    storage_bytes: int
    """What the org keeps in files, stored and still uploading. Shown beside
    the plan's storage figure; the plan does not refuse on it yet."""
    monthly_cents: int
    """What the paid plan costs a month at `seats`; zero with none."""
    payment_failed: bool
    """The processor could not collect the latest invoice: the subscription
    is past due, and the org keeps its plan while the processor retries, or
    unpaid, and the org is on what is left until it pays. It clears when a
    later payment succeeds or the subscription ends. The fix is another
    payment method: `POST /v1/billing/portal` with `flow` set to
    `payment_method_update`."""
    can_manage: bool
    plans: tuple[PlanOfferView, ...]


class OperatorBillingView(View):
    """One org's plan, as the operator plane reads it."""

    plan: Plan
    paid_plan: Plan | None
    comped_plan: Plan | None
    status: SubscriptionStatus | None
    ends_at: datetime | None


class StartCheckoutRequest(RequestBody):
    """`return_url` is the portal page the person comes back to, paid or not;
    it is one of the portal's own origins, or the request is refused."""

    plan: Plan
    return_url: str = Field(min_length=1, max_length=2000)


class OpenPortalRequest(RequestBody):
    """`flow` opens the processor's page on one task and comes back to
    `return_url` when it is done; without it, the page's home."""

    return_url: str = Field(min_length=1, max_length=2000)
    flow: Literal["payment_method_update"] | None = None


class RedirectView(View):
    """Where the person goes next: the processor's hosted page."""

    url: str


class CompPlanRequest(RequestBody):
    """The plan an operator grants without a payment; null, or free, takes
    the grant back."""

    plan: Plan | None


class DeliveryReceivedView(View):
    """The delivery checked out and is queued; the processor stops retrying."""

    received: bool
