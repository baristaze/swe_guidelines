"""The twin: a deterministic payment processor in memory.

It speaks the shapes the real client hands out, keeps customers, checkout
sessions, and subscriptions, and signs its own deliveries with the scheme
the processor uses, so a delivery it makes passes the same check a real one
does. Tests, and a single process on a laptop, run against it; it refuses
to be built outside a local environment. Everything it makes names its
provenance: every id carries `twin`.

Beyond the interface it offers what a test drives the processor with: a
checkout completed by the customer, a subscription moved to another state
or past its period, and the signed delivery of any event about one."""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from acme.integrations.exceptions import PaymentsRefused, UnsafeIntegration
from acme.integrations.payments import PaymentsInterface
from acme.integrations.payments.deliveries import sign, verified
from acme.integrations.payments.types import (
    ORG_METADATA_KEY,
    ProviderDelivery,
    ProviderSubscription,
)

TWIN_ENVIRONMENTS = frozenset({"local", "test"})
TWIN_WEBHOOK_SECRET = "whsec_twin_local_only"
"""The twin's signing secret: a value for a laptop and a test, never a
credential."""
PERIOD = timedelta(days=30)


class PaymentsTwinImpl(PaymentsInterface):
    def __init__(
        self,
        *,
        environment: str,
        webhook_secret: str = TWIN_WEBHOOK_SECRET,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if environment not in TWIN_ENVIRONMENTS:
            raise UnsafeIntegration(
                f"ACME_BILLING_BACKEND=twin is refused when ACME_ENVIRONMENT={environment}"
            )
        self._secret = webhook_secret
        self._now = clock or (lambda: datetime.now(UTC))
        self._counter = 0
        self.customers: dict[str, dict[str, Any]] = {}
        self.sessions: dict[str, dict[str, Any]] = {}
        self.subscriptions: dict[str, dict[str, Any]] = {}
        self.portal_sessions: list[str] = []
        self.quantity_keys: dict[str, int] = {}
        """Every idempotency key a quantity change was made under, and the
        quantity it set: a repeated key changes nothing."""
        self.quantity_changes: list[tuple[str, int]] = []
        self._customer_by_org: dict[UUID, str] = {}

    @property
    def configured(self) -> bool:
        return True

    def describe(self) -> str:
        return "payments=twin"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def _id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}_twin_{self._counter:06d}"

    async def create_customer(
        self, org_id: UUID, name: str, *, deadline: datetime | None = None
    ) -> str:
        existing = self._customer_by_org.get(org_id)
        if existing is not None:
            return existing
        customer_id = self._id("cus")
        self.customers[customer_id] = {"name": name, "metadata": {ORG_METADATA_KEY: str(org_id)}}
        self._customer_by_org[org_id] = customer_id
        return customer_id

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
        if customer_id not in self.customers:
            raise PaymentsRefused("create checkout session", "resource_missing")
        session_id = self._id("cs")
        self.sessions[session_id] = {
            "customer": customer_id,
            "org_id": org_id,
            "lookup_key": lookup_key,
            "quantity": quantity,
            "success_url": success_url,
            "cancel_url": cancel_url,
            "subscription": None,
        }
        return f"https://checkout.twin.invalid/c/{session_id}"

    async def create_portal_session(
        self,
        customer_id: str,
        return_url: str,
        update_payment_method: bool = False,
        *,
        deadline: datetime | None = None,
    ) -> str:
        if customer_id not in self.customers:
            raise PaymentsRefused("create billing portal session", "resource_missing")
        self.portal_sessions.append(customer_id)
        if update_payment_method:
            return f"https://billing.twin.invalid/p/{customer_id}/payment-method"
        return f"https://billing.twin.invalid/p/{customer_id}"

    async def read_subscription(self, subscription_id: str) -> ProviderSubscription | None:
        found = self.subscriptions.get(subscription_id)
        return None if found is None else self._view(found)

    async def read_customer_org(self, customer_id: str) -> UUID | None:
        found = self.customers.get(customer_id)
        if found is None:
            return None
        return UUID(found["metadata"][ORG_METADATA_KEY])

    async def set_cancel_at_period_end(
        self, subscription_id: str, cancel: bool, *, deadline: datetime | None = None
    ) -> ProviderSubscription:
        found = self._subscription(subscription_id)
        found["cancel_at_period_end"] = cancel
        return self._view(found)

    async def set_quantity(
        self, subscription: ProviderSubscription, quantity: int, idempotency_key: str
    ) -> ProviderSubscription:
        found = self._subscription(subscription.id)
        if subscription.item_id != found["item"]:
            # The processor refuses an item that is not the subscription's.
            raise PaymentsRefused("update subscription quantity", "resource_missing")
        if idempotency_key not in self.quantity_keys:
            self.quantity_keys[idempotency_key] = quantity
            found["quantity"] = quantity
            self.quantity_changes.append((subscription.id, quantity))
        return self._view(found)

    async def cancel_subscription(self, subscription_id: str) -> None:
        found = self.subscriptions.get(subscription_id)
        if found is not None:
            found["status"] = "canceled"

    async def delete_customer(self, customer_id: str) -> None:
        found = self.customers.pop(customer_id, None)
        if found is None:
            return
        self._customer_by_org.pop(UUID(found["metadata"][ORG_METADATA_KEY]), None)
        for subscription in self.subscriptions.values():
            if subscription["customer"] == customer_id:
                subscription["status"] = "canceled"

    def verify_delivery(self, payload: bytes, signature: str | None) -> ProviderDelivery:
        return verified(payload, signature, self._secret)

    # What a test drives the processor with.

    def complete_checkout(self, checkout_url: str) -> tuple[bytes, str]:
        """The customer pays: the session's subscription starts, active for one
        period, and the signed `checkout.session.completed` comes back."""
        session_id = checkout_url.rsplit("/", 1)[-1]
        session = self.sessions[session_id]
        subscription_id = self._id("sub")
        self.subscriptions[subscription_id] = {
            "id": subscription_id,
            "item": self._id("si"),
            "customer": session["customer"],
            "status": "active",
            "lookup_key": session["lookup_key"],
            "quantity": session["quantity"],
            "current_period_end": self._now() + PERIOD,
            "cancel_at_period_end": False,
            "org_id": session["org_id"],
        }
        session["subscription"] = subscription_id
        return self.delivery(
            "checkout.session.completed",
            {
                "id": session_id,
                "object": "checkout.session",
                "customer": session["customer"],
                "subscription": subscription_id,
                "client_reference_id": str(session["org_id"]),
                "metadata": {ORG_METADATA_KEY: str(session["org_id"])},
            },
        )

    def move(self, subscription_id: str, **changes: Any) -> None:
        """Moves a subscription the way the processor would: another status,
        another period end, another price."""
        found = self._subscription(subscription_id)
        for key, value in changes.items():
            if key not in found:
                raise KeyError(key)
            found[key] = value

    def end_period(self, subscription_id: str) -> None:
        """The period passes: a subscription set to cancel at its end is
        canceled, and any other one renews for another period."""
        found = self._subscription(subscription_id)
        if found["cancel_at_period_end"]:
            found["status"] = "canceled"
        else:
            found["current_period_end"] = found["current_period_end"] + PERIOD

    def subscription_event(self, event_type: str, subscription_id: str) -> tuple[bytes, str]:
        """The signed delivery of a `customer.subscription.*` event, carrying
        the subscription as it stands now."""
        found = self._subscription(subscription_id)
        return self.delivery(event_type, self._raw(found))

    def invoice_event(self, event_type: str, subscription_id: str) -> tuple[bytes, str]:
        """The signed delivery of an `invoice.*` event about the subscription."""
        found = self._subscription(subscription_id)
        return self.delivery(
            event_type,
            {
                "id": self._id("in"),
                "object": "invoice",
                "customer": found["customer"],
                "parent": {
                    "type": "subscription_details",
                    "subscription_details": {
                        "subscription": subscription_id,
                        "metadata": {ORG_METADATA_KEY: str(found["org_id"])},
                    },
                },
            },
        )

    def delivery(self, event_type: str, obj: dict[str, Any]) -> tuple[bytes, str]:
        """Any event, signed now: the body and the header."""
        event = {
            "id": self._id("evt"),
            "object": "event",
            "type": event_type,
            "created": int(self._now().timestamp()),
            "livemode": False,
            "data": {"object": obj},
        }
        payload = json.dumps(event).encode()
        return payload, sign(payload, self._secret)

    def _subscription(self, subscription_id: str) -> dict[str, Any]:
        found = self.subscriptions.get(subscription_id)
        if found is None:
            raise PaymentsRefused("update subscription", "resource_missing")
        return found

    def _raw(self, found: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": found["id"],
            "object": "subscription",
            "customer": found["customer"],
            "status": found["status"],
            "cancel_at_period_end": found["cancel_at_period_end"],
            "metadata": {ORG_METADATA_KEY: str(found["org_id"])},
        }

    @staticmethod
    def _view(found: dict[str, Any]) -> ProviderSubscription:
        return ProviderSubscription(
            id=found["id"],
            customer_id=found["customer"],
            status=found["status"],
            price_lookup_key=found["lookup_key"],
            quantity=found["quantity"],
            current_period_end=found["current_period_end"],
            cancel_at_period_end=found["cancel_at_period_end"],
            org_id=found["org_id"],
            item_id=found["item"],
        )
