"""A delivery from the payment processor that has been applied: the mark
that makes the next copy of it a no-op."""

from typing import ClassVar

from acme.om.base import Created, Identifiable


class BillingDelivery(Identifiable, Created):
    """Written in the same commit as the account the delivery changed. Its id
    is the delivery's key, a UUID v5 over the processor's name and the event
    id, so a second copy meets the primary key and changes nothing."""

    MANAGER_OWNED_FIELDS: ClassVar[tuple[str, ...]] = ()

    event_id: str
    event_type: str
