"""Payments: the processor that owns money, customers, and subscriptions.

Acme owns what a plan entitles an org to; the processor owns whether the
org has paid. This interface is the one door to it: a customer per org, a
checkout that starts a subscription, the processor's own page for payment
methods, invoices, and plan changes, the two changes Acme makes to a
subscription itself (cancel at the period's end, the seat count), the end
of both when an account is deleted (the subscription canceled at once, the
customer deleted), a read of a subscription as the processor holds it now,
and the check of an inbound delivery's signature. It has a real client and a deterministic
twin; the twin refuses to run outside a local environment.

Every call fails by whose problem it is. A refusal of the request itself
is `PaymentsRefused`: the same request gets the same answer. A refusal
of the process's own key (revoked, or without the permission) is
`PaymentsKeyRefused`, a `ProviderUnavailable`: nothing is wrong with the
call, and it goes through once a person fixes the key. A processor that
throttles, or does not answer, is unavailable too.

A call a request makes (the customer, the checkout, the portal, the cancel
and its undo) carries the request's `deadline`, which every call it makes
shares (ADR 0069), and ends by then, unavailable, as when the processor does
not answer. The rest are a worker's, bounded by the item's lease."""

from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from acme.integrations.payments.types import (
    ProviderDelivery,
    ProviderSubscription,
)

__all__ = ["PaymentsInterface", "ProviderDelivery", "ProviderSubscription"]


class PaymentsInterface(ABC):
    @property
    @abstractmethod
    def configured(self) -> bool:
        """False when this environment holds no credential for the processor:
        every call then raises `PaymentsUnconfigured`, and a delivery is
        refused, so the plans still apply and nobody can buy one."""
        ...

    @abstractmethod
    async def create_customer(
        self, org_id: UUID, name: str, *, deadline: datetime | None = None
    ) -> str:
        """The processor's customer for one org, created once: the call carries
        an idempotency key derived from the org, so two starts that race make
        one customer. The customer's metadata names the org."""
        ...

    @abstractmethod
    async def create_checkout(
        self,
        *,
        customer_id: str,
        org_id: UUID,
        lookup_key: str,
        quantity: int,
        success_url: str,
        cancel_url: str,
        deadline: datetime | None = None,
    ) -> str:
        """A hosted checkout for a subscription to the price the lookup key
        names, at `quantity`, for the org's customer; returns its URL. The
        subscription it starts carries the org in its metadata, and the
        session names the org as its client reference."""
        ...

    @abstractmethod
    async def create_portal_session(
        self,
        customer_id: str,
        return_url: str,
        update_payment_method: bool = False,
        *,
        deadline: datetime | None = None,
    ) -> str:
        """The processor's own page for the customer: payment methods,
        invoices, a change of plan, cancellation. Returns its URL. With
        `update_payment_method`, the page is the processor's flow for adding
        a payment method, which sends the person back to `return_url` once
        one is saved."""
        ...

    @abstractmethod
    async def read_subscription(self, subscription_id: str) -> ProviderSubscription | None:
        """The subscription as the processor holds it now, or None when it
        does not know the id. Deliveries arrive in any order, so this read,
        and never a delivery's payload, is what the mirror is written from."""
        ...

    @abstractmethod
    async def read_customer_org(self, customer_id: str) -> UUID | None:
        """The org the customer's metadata names, or None."""
        ...

    @abstractmethod
    async def set_cancel_at_period_end(
        self, subscription_id: str, cancel: bool, *, deadline: datetime | None = None
    ) -> ProviderSubscription:
        """Ends the subscription when its current period ends, or takes that
        back; the org keeps what it paid for until then."""
        ...

    @abstractmethod
    async def set_quantity(
        self, subscription: ProviderSubscription, quantity: int, idempotency_key: str
    ) -> ProviderSubscription:
        """The seat count of a per-seat subscription, with no proration: the
        change applies from the next invoice. The key makes a repeated call
        one change. `subscription` is the one just read, whose item it
        changes, so the change is one call."""
        ...

    @abstractmethod
    async def cancel_subscription(self, subscription_id: str) -> None:
        """Ends the subscription now, not at its period's end: the account it
        paid for is gone. No proration and no refund is made. A subscription
        the processor no longer knows, or one canceled already, is done
        already, and that is no error."""
        ...

    @abstractmethod
    async def delete_customer(self, customer_id: str) -> None:
        """Deletes the org's customer at the processor, which ends every
        subscription it still has and removes its payment methods. The
        processor keeps the invoices it issued, as the law asks of it. A
        customer it no longer knows is deleted already, and that is no
        error."""
        ...

    @abstractmethod
    def verify_delivery(self, payload: bytes, signature: str | None) -> ProviderDelivery:
        """The inbound delivery, once its signature over the body and its
        timestamp check out against the endpoint's secret within the replay
        window. Raises `DeliveryRefused` otherwise, naming neither the secret
        nor the signature."""
        ...

    @abstractmethod
    def describe(self) -> str: ...

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...
