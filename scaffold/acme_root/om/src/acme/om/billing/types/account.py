"""An org's billing account: the org's customer at the payment processor, a
mirror of its subscription, and a plan an operator granted, if any.

The processor owns whether the org has paid; this row is what Acme last
read of it, written only from a read of the processor, never from a
caller's say-so, so every field here is the manager's."""

from datetime import datetime
from enum import StrEnum
from typing import ClassVar

from acme.om.base import Identifiable, Trackable
from acme.om.billing.types.plan import Plan


class SubscriptionStatus(StrEnum):
    """The processor's statuses, as it spells them."""

    INCOMPLETE = "incomplete"
    INCOMPLETE_EXPIRED = "incomplete_expired"
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    UNPAID = "unpaid"
    PAUSED = "paused"


def status_of(value: str) -> SubscriptionStatus:
    """A status the processor names; one this build does not know carries no
    plan, which is what `incomplete` means."""
    try:
        return SubscriptionStatus(value)
    except ValueError:
        return SubscriptionStatus.INCOMPLETE


class BillingAccount(Identifiable, Trackable):
    MANAGER_OWNED_FIELDS: ClassVar[tuple[str, ...]] = (
        "customer_id",
        "subscription_id",
        "price_lookup_key",
        "status",
        "current_period_end",
        "cancel_at_period_end",
        "quantity",
        "comped_plan",
        "synced_at",
    )
    """All of it: the mirror is written from the processor's answer, the
    customer when the manager makes it, and the grant by an operator."""

    customer_id: str | None = None
    """Made the first time the org starts a checkout; None before."""
    subscription_id: str | None = None
    price_lookup_key: str | None = None
    status: SubscriptionStatus | None = None
    current_period_end: datetime | None = None
    cancel_at_period_end: bool = False
    quantity: int = 0
    comped_plan: Plan | None = None
    """A plan an operator granted without a payment: support, a partner, a
    load test's tenants. The org is on the higher of this and what it pays
    for."""
    synced_at: datetime | None = None
    """When the mirror last read the processor."""
