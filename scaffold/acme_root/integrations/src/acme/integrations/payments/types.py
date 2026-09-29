"""The values the payments interface hands out: the processor's own shapes,
reduced to the fields Acme reads. Frozen, like every infra value."""

from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from acme.infra.base import InfraModel

PROVIDER = "stripe"
"""The processor's name, as a delivery's idempotency key spells it."""

ORG_METADATA_KEY = "acme_org_id"
"""The metadata key every customer and subscription Acme creates carries."""


class ProviderSubscription(InfraModel):
    """A subscription as the processor holds it at the moment of the read."""

    id: str
    customer_id: str
    status: str
    price_lookup_key: str | None
    quantity: int
    current_period_end: datetime | None
    cancel_at_period_end: bool
    org_id: UUID | None
    """The org its metadata names, when it names one."""
    item_id: str | None
    """Its first item, the one a seat count changes; None when it has none."""


class ProviderDelivery(InfraModel):
    """An inbound delivery whose signature checked out: which event it is and
    the ids that say whose it is. The payload's state is deliberately not
    here: deliveries arrive in any order, so the consumer reads the
    subscription again rather than trusting a snapshot."""

    event_id: str
    event_type: str
    created: datetime
    livemode: bool
    customer_id: str | None = None
    subscription_id: str | None = None
    org_hint: UUID | None = None
    """The org the event names itself: a checkout's client reference, or the
    metadata of the object it is about. A hint and never a proof: the
    consumer holds it to the customer the org's own record names."""

    @property
    def idempotency_key(self) -> UUID:
        """A UUID v5 over the processor's name and the event id: the same on
        every retry of one delivery, so the consumer dedupes on it."""
        return delivery_key(self.event_id)


def delivery_key(event_id: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"{PROVIDER}:{event_id}")
