"""What the billing manager answers: the plan an org is on and what it
entitles the org to, with the account behind it."""

from datetime import datetime

from acme.om.base import Platform
from acme.om.billing.types.account import BillingAccount
from acme.om.billing.types.plan import Plan, PlanLimits


class Entitlements(Platform):
    """The plan an org is on now and its bounds: what every lever reads."""

    plan: Plan
    limits: PlanLimits


class Billing(Entitlements):
    """The plan, and where it comes from. `paid_plan` is what the subscription
    carries, `comped_plan` what an operator granted; `ends_at` is set when a
    paid plan is set to end, and the org is on `plan_after` from then."""

    paid_plan: Plan | None
    comped_plan: Plan | None
    ends_at: datetime | None
    plan_after: Plan | None
    payment_failed: bool
    """The processor could not collect the subscription's latest invoice
    (past due or unpaid); it clears when a later payment succeeds or the
    subscription ends."""
    account: BillingAccount | None


class CheckoutStart(Platform):
    """Where the person goes to pay."""

    url: str
