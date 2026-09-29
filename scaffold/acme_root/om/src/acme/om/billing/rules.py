"""Pure rules of the billing namespace: the one table of what each plan
entitles an org to and costs, the plan an org's mirror of its subscription
derives, and the refusal a lever makes. Values in, values out; no clock, no
storage, no settings. The numbers are illustrative: the shape is what
holds."""

from datetime import datetime

from acme.om.billing.types.account import BillingAccount, SubscriptionStatus
from acme.om.billing.types.plan import Lever, Plan, PlanLimits, PlanPrice
from acme.om.exceptions import PlanLimitReached

GIB = 1024**3

PLAN_LIMITS: dict[Plan, PlanLimits] = {
    Plan.FREE: PlanLimits(members=1, api_keys=False, active_tasks=10, storage_bytes=1 * GIB),
    Plan.PRO: PlanLimits(members=1, api_keys=True, active_tasks=None, storage_bytes=10 * GIB),
    Plan.TEAM: PlanLimits(members=5, api_keys=True, active_tasks=None, storage_bytes=50 * GIB),
    Plan.MAX: PlanLimits(members=None, api_keys=True, active_tasks=None, storage_bytes=200 * GIB),
}

PLAN_PRICES: dict[Plan, PlanPrice] = {
    Plan.FREE: PlanPrice(lookup_key=None, flat_cents=0),
    Plan.PRO: PlanPrice(lookup_key="acme.pro.monthly", flat_cents=500),
    Plan.TEAM: PlanPrice(lookup_key="acme.team.monthly", flat_cents=1000),
    # One price, two volume tiers: up to ten seats is the flat $30, and from
    # the eleventh on every seat is $3, so eleven seats are $33.
    Plan.MAX: PlanPrice(
        lookup_key="acme.max.monthly", flat_cents=3000, included_seats=10, per_seat_cents=300
    ),
}

PLAN_ORDER: tuple[Plan, ...] = (Plan.FREE, Plan.PRO, Plan.TEAM, Plan.MAX)
"""Lowest first: a later plan entitles to at least what an earlier one does."""

LIVE_STATUSES = frozenset(
    {SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING, SubscriptionStatus.PAST_DUE}
)
"""The statuses under which a subscription still carries its plan. Past due
is the processor retrying a failed payment; the org keeps its plan while it
does, and loses it when the processor gives up (unpaid or canceled)."""


PAYMENT_FAILED_STATUSES = frozenset({SubscriptionStatus.PAST_DUE, SubscriptionStatus.UNPAID})
"""The statuses the processor gives a subscription whose latest invoice it
could not collect: past due while it retries, unpaid once it has stopped.
A later payment makes it active again; an end makes it canceled."""


def payment_failed(account: BillingAccount | None) -> bool:
    """Whether the org's subscription has an invoice the processor could not
    collect, which the owner fixes with another payment method."""
    return (
        account is not None
        and account.subscription_id is not None
        and account.status in PAYMENT_FAILED_STATUSES
    )


def limits_of(plan: Plan) -> PlanLimits:
    return PLAN_LIMITS[plan]


def plan_of_lookup_key(lookup_key: str | None) -> Plan | None:
    for plan, price in PLAN_PRICES.items():
        if price.lookup_key is not None and price.lookup_key == lookup_key:
            return plan
    return None


def monthly_price_cents(plan: Plan, seats: int) -> int:
    """What the processor bills a month for `seats` active members, mirroring
    the price's tiers: the flat amount up to the included seats, and the
    per-seat amount for every seat once the count is past them."""
    price = PLAN_PRICES[plan]
    if price.included_seats is None or seats <= price.included_seats:
        return price.flat_cents
    return price.per_seat_cents * seats


def seats_metered(plan: Plan) -> bool:
    """Whether the subscription's quantity follows the active member count."""
    return PLAN_PRICES[plan].included_seats is not None


def paid_plan(account: BillingAccount | None, now: datetime) -> Plan | None:
    """The plan the org's subscription carries now, or None. A subscription
    set to end at its period's end carries its plan until that instant,
    whether or not the processor has said it ended yet."""
    if account is None or account.subscription_id is None or account.status is None:
        return None
    if account.status not in LIVE_STATUSES:
        return None
    if (
        account.cancel_at_period_end
        and account.current_period_end is not None
        and now >= account.current_period_end
    ):
        return None
    return plan_of_lookup_key(account.price_lookup_key)


def effective_plan(account: BillingAccount | None, now: datetime) -> Plan:
    """The higher of the paid plan and a plan the operator granted, else Free."""
    candidates = [Plan.FREE, paid_plan(account, now), account.comped_plan if account else None]
    return max((p for p in candidates if p is not None), key=PLAN_ORDER.index)


def plan_rose(before: Plan, after: Plan) -> bool:
    """Whether a change of plan lifted the org's bounds: the event that wakes
    a record parked on one (an import stopped at the plan's active tasks)."""
    return PLAN_ORDER.index(after) > PLAN_ORDER.index(before)


def ends_at(account: BillingAccount | None, now: datetime) -> datetime | None:
    """When a paid plan set to end drops the org to what is left, or None."""
    if paid_plan(account, now) is None or account is None or not account.cancel_at_period_end:
        return None
    return account.current_period_end


def bound_of(limits: PlanLimits, lever: Lever) -> int | None:
    """The lever's bound as a count; None is no bound. A plan without api
    keys allows none."""
    if lever is Lever.MEMBERS:
        return limits.members
    if lever is Lever.ACTIVE_TASKS:
        return limits.active_tasks
    if lever is Lever.API_KEYS:
        return None if limits.api_keys else 0
    return limits.storage_bytes


def suggested_plan(lever: Lever, plan: Plan, needed: int) -> Plan | None:
    """The first plan above `plan` whose bound admits `needed`, or None."""
    for candidate in PLAN_ORDER[PLAN_ORDER.index(plan) + 1 :]:
        bound = bound_of(PLAN_LIMITS[candidate], lever)
        if bound is None or needed <= bound:
            return candidate
    return None


def refuse_past(plan: Plan, lever: Lever, current: int, adding: int = 1) -> None:
    """Refuses a change that would take the lever from `current` to
    `current + adding` past the plan's bound. Nothing already there is
    touched: an org over its bound after a downgrade keeps what it has and
    is refused only what would add to it."""
    bound = bound_of(PLAN_LIMITS[plan], lever)
    needed = current + adding
    if bound is None or needed <= bound:
        return
    suggested = suggested_plan(lever, plan, needed)
    raise PlanLimitReached(
        plan=plan.value,
        lever=lever.value,
        limit=bound,
        suggested_plan=None if suggested is None else suggested.value,
    )


def should_mirror(account: BillingAccount | None, subscription_id: str, live: bool) -> bool:
    """Whether a subscription read now is the one the mirror follows. An org
    that canceled and later subscribed again has two; a late delivery about
    the old one must not overwrite the new one with its end. So the mirror
    takes a subscription it already follows, or any live one, or any at all
    when it follows none."""
    if account is None or account.subscription_id is None:
        return True
    return account.subscription_id == subscription_id or live
