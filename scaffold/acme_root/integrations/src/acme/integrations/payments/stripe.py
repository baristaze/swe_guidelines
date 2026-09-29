"""The real client: Stripe, through its SDK.

Every call carries two headers the account decides with: `Stripe-Context`,
naming the account the settings name, and `Stripe-Version`, pinned to the
version the SDK was released against, so a payload's shape changes only
when this pin does. One client is opened at `start` and closed at `close`,
and every call is bounded by the timeout the settings give it.

At `start` the client reads once with each permission of the runtime key
(one item of each resource), so a key of another account, or one that
lacks a resource, is named in the start line and the log before the
first checkout finds it. A read proves the resource is not None; it
cannot prove Write without writing.

An answer of the SDK is a `StripeObject`, and so is every object nested
in it. A `StripeObject` is not a dict: `.get` and `.keys` raise on it. So
a field is read by its name (`session.url`), and anything read as a
mapping is read off `.to_dict()`."""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import stripe

from acme.infra.deadline import bounded, unreachable
from acme.infra.exceptions import BackendFailed, BackendUnreachable
from acme.integrations.exceptions import (
    PaymentsKeyRefused,
    PaymentsRefused,
    PaymentsUnconfigured,
    ProviderUnavailable,
)
from acme.integrations.payments import PaymentsInterface
from acme.integrations.payments.deliveries import verified
from acme.integrations.payments.permissions import (
    CHECKOUT_SESSIONS,
    CUSTOMER_PORTAL,
    CUSTOMERS,
    PRICES_READ,
    SUBSCRIPTIONS,
    Permission,
)
from acme.integrations.payments.types import (
    ORG_METADATA_KEY,
    ProviderDelivery,
    ProviderSubscription,
)

log = logging.getLogger(__name__)

STRIPE_VERSION = stripe.api_version
"""The API version every request names and every endpoint Acme registers
is pinned to: the one this SDK release was built against."""

ENDED_STATUSES = frozenset({"canceled", "incomplete_expired"})
"""The statuses of a subscription that bills nobody again: nothing is left
to cancel."""

PORTAL_DESIRED_KEY = "portal"
"""The metadata the bootstrap stamps on the one portal configuration it
manages (`acme_desired_key`), which a portal session names."""


class PaymentsStripeImpl(PaymentsInterface):
    def __init__(
        self,
        *,
        api_key: str | None,
        account_id: str | None,
        webhook_secret: str | None,
        timeout: timedelta,
        max_network_retries: int = 2,
        check_at_start: bool = True,
    ) -> None:
        self._api_key = api_key
        self._account_id = account_id
        self._webhook_secret = webhook_secret
        self._timeout = timeout
        self._retries = max_network_retries
        self._client: stripe.StripeClient | None = None
        self._http: stripe.HTTPXClient | None = None
        self._portal_configuration: str | None = None
        self._check_at_start = check_at_start
        self._lacking: tuple[Permission, ...] = ()

    @property
    def configured(self) -> bool:
        return bool(self._api_key and self._account_id)

    def describe(self) -> str:
        if not self.configured:
            return "payments=stripe (not configured)"
        described = f"{self._account_id}, {STRIPE_VERSION}"
        if self._lacking:
            described += "; the key lacks " + ", ".join(p.resource for p in self._lacking)
        return f"payments=stripe ({described})"

    async def start(self) -> None:
        if not self.configured or self._client is not None:
            return
        self._http = stripe.HTTPXClient(timeout=self._timeout.total_seconds())
        self._client = stripe.StripeClient(
            self._api_key or "",
            stripe_context=self._account_id,
            stripe_version=STRIPE_VERSION,
            http_client=self._http,
            max_network_retries=self._retries,
        )
        if self._check_at_start:
            await self.check_access()

    async def check_access(self) -> tuple[Permission, ...]:
        """One read under each permission of the runtime key; returns, and
        logs, the ones the processor refused. A processor that does not
        answer leaves the question open: the process starts either way,
        since billing is one part of it."""
        v1 = self._v1()
        reads: dict[Permission, Any] = {
            CUSTOMERS: v1.customers.list_async({"limit": 1}),
            CHECKOUT_SESSIONS: v1.checkout.sessions.list_async({"limit": 1}),
            CUSTOMER_PORTAL: v1.billing_portal.configurations.list_async({"limit": 1}),
            SUBSCRIPTIONS: v1.subscriptions.list_async({"limit": 1}),
            PRICES_READ: v1.prices.list_async({"limit": 1}),
        }
        answers = await asyncio.gather(*reads.values(), return_exceptions=True)
        lacking: list[Permission] = []
        for permission, answer in zip(reads, answers, strict=True):
            if isinstance(answer, stripe.APIConnectionError):
                log.warning("stripe did not answer the key check: %s", type(answer).__name__)
                return ()
            if isinstance(answer, stripe.PermissionError | stripe.AuthenticationError):
                lacking.append(permission)
            elif isinstance(answer, BaseException):
                log.warning(
                    "stripe key check: reading %s failed: %s",
                    permission.resource,
                    getattr(answer, "code", None) or type(answer).__name__,
                )
        self._lacking = tuple(lacking)
        if len(lacking) == len(reads):
            log.error(
                "stripe refused every read: the key is revoked, lacks every permission, or is "
                "not a key of %s",
                self._account_id,
            )
        else:
            for permission in lacking:
                log.error(
                    "the stripe runtime key lacks %s (group %s): set it to %s on the key",
                    permission.resource,
                    permission.group,
                    permission.level,
                )
        return self._lacking

    async def close(self) -> None:
        if self._http is not None:
            await self._http.close_async()
        self._client = None
        self._http = None

    def _v1(self) -> Any:
        if not self.configured:
            raise PaymentsUnconfigured("billing is not configured in this environment")
        if self._client is None:
            raise RuntimeError("the payments client is used before start()")
        return self._client.v1

    async def create_customer(
        self, org_id: UUID, name: str, *, deadline: datetime | None = None
    ) -> str:
        async with translated("create customer", deadline):
            customer = await self._v1().customers.create_async(
                {"name": name, "metadata": {ORG_METADATA_KEY: str(org_id)}},
                {"idempotency_key": f"acme-customer-{org_id}"},
            )
        return str(customer.id)

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
        v1 = self._v1()
        async with translated("find price", deadline):
            prices = await v1.prices.list_async({"lookup_keys": [lookup_key], "active": True})
        if not prices.data:
            raise PaymentsRefused("create checkout session", f"no active price {lookup_key}")
        org = {ORG_METADATA_KEY: str(org_id)}
        async with translated("create checkout session", deadline):
            session = await v1.checkout.sessions.create_async(
                {
                    "mode": "subscription",
                    "customer": customer_id,
                    "client_reference_id": str(org_id),
                    "line_items": [{"price": prices.data[0].id, "quantity": quantity}],
                    "success_url": success_url,
                    "cancel_url": cancel_url,
                    "metadata": org,
                    "subscription_data": {"metadata": org},
                }
            )
        return str(session.url)

    async def create_portal_session(
        self,
        customer_id: str,
        return_url: str,
        update_payment_method: bool = False,
        *,
        deadline: datetime | None = None,
    ) -> str:
        v1 = self._v1()
        params: dict[str, Any] = {"customer": customer_id, "return_url": return_url}
        if update_payment_method:
            # The portal's deep link: straight to adding a payment method,
            # then back to the page that sent the person. The configuration
            # must allow the payment method update (the bootstrap turns it on).
            params["flow_data"] = {
                "type": "payment_method_update",
                "after_completion": {"type": "redirect", "redirect": {"return_url": return_url}},
            }
        configuration = await self._managed_portal_configuration(v1, deadline)
        if configuration is not None:
            params["configuration"] = configuration
        async with translated("create billing portal session", deadline):
            session = await v1.billing_portal.sessions.create_async(params)
        return str(session.url)

    async def _managed_portal_configuration(self, v1: Any, deadline: datetime | None) -> str | None:
        """The configuration the bootstrap manages, found once; the account's
        default when the bootstrap has not run."""
        if self._portal_configuration is None:
            async with translated("list billing portal configurations", deadline):
                found = await v1.billing_portal.configurations.list_async(
                    {"active": True, "limit": 100}
                )
            for configuration in found.data:
                metadata = configuration.to_dict().get("metadata") or {}
                if metadata.get("acme_desired_key") == PORTAL_DESIRED_KEY:
                    self._portal_configuration = str(configuration.id)
                    break
        return self._portal_configuration

    async def read_subscription(self, subscription_id: str) -> ProviderSubscription | None:
        try:
            async with translated("read subscription"):
                subscription = await self._v1().subscriptions.retrieve_async(subscription_id)
        except PaymentsRefused as refused:
            if "resource_missing" in refused.message:
                return None
            raise
        return subscription_of(subscription.to_dict())

    async def read_customer_org(self, customer_id: str) -> UUID | None:
        async with translated("read customer"):
            customer = await self._v1().customers.retrieve_async(customer_id)
        value = (customer.to_dict().get("metadata") or {}).get(ORG_METADATA_KEY)
        try:
            return UUID(value) if isinstance(value, str) else None
        except ValueError:
            return None

    async def set_cancel_at_period_end(
        self, subscription_id: str, cancel: bool, *, deadline: datetime | None = None
    ) -> ProviderSubscription:
        async with translated("update subscription", deadline):
            subscription = await self._v1().subscriptions.update_async(
                subscription_id, {"cancel_at_period_end": cancel}
            )
        return subscription_of(subscription.to_dict())

    async def set_quantity(
        self, subscription: ProviderSubscription, quantity: int, idempotency_key: str
    ) -> ProviderSubscription:
        if subscription.item_id is None:
            raise PaymentsRefused("update subscription quantity", "the subscription has no item")
        async with translated("update subscription quantity"):
            updated = await self._v1().subscriptions.update_async(
                subscription.id,
                {
                    "items": [{"id": subscription.item_id, "quantity": quantity}],
                    # A seat added or removed is billed from the next invoice:
                    # no prorated line per member change, and nothing to
                    # refund when one is added and removed within a period.
                    "proration_behavior": "none",
                },
                {"idempotency_key": idempotency_key},
            )
        return subscription_of(updated.to_dict())

    async def cancel_subscription(self, subscription_id: str) -> None:
        v1 = self._v1()
        found: dict[str, Any] = {}

        async def read() -> None:
            found.update((await v1.subscriptions.retrieve_async(subscription_id)).to_dict())

        # The read on the way is the end's too: one the processor no longer
        # holds has nothing to cancel.
        await ended("read subscription", read)
        if not found or subscription_of(found).status in ENDED_STATUSES:
            return
        await ended("cancel subscription", lambda: v1.subscriptions.cancel_async(subscription_id))

    async def delete_customer(self, customer_id: str) -> None:
        v1 = self._v1()
        await ended("delete customer", lambda: v1.customers.delete_async(customer_id))

    def verify_delivery(self, payload: bytes, signature: str | None) -> ProviderDelivery:
        if not self._webhook_secret:
            raise PaymentsUnconfigured("no webhook signing secret is configured")
        return verified(payload, signature, self._webhook_secret)


def subscription_of(raw: dict[str, Any]) -> ProviderSubscription:
    """The fields Acme reads off a subscription. Since the 2025 API versions
    the period sits on the item, not the subscription; either is read."""
    items = (raw.get("items") or {}).get("data") or []
    item: dict[str, Any] = items[0] if items else {}
    price = item.get("price") or {}
    period_end = raw.get("current_period_end") or item.get("current_period_end")
    org = (raw.get("metadata") or {}).get(ORG_METADATA_KEY)
    customer = raw.get("customer")
    if isinstance(customer, dict):
        customer = customer.get("id")
    try:
        org_id = UUID(org) if isinstance(org, str) else None
    except ValueError:
        org_id = None
    return ProviderSubscription(
        id=str(raw["id"]),
        customer_id=str(customer),
        status=str(raw.get("status", "")),
        price_lookup_key=price.get("lookup_key"),
        quantity=int(item.get("quantity") or 0),
        current_period_end=(
            datetime.fromtimestamp(int(period_end), UTC) if period_end is not None else None
        ),
        cancel_at_period_end=bool(raw.get("cancel_at_period_end")),
        org_id=org_id,
        item_id=str(item["id"]) if item.get("id") else None,
    )


async def ended(operation: str, call: Callable[[], Awaitable[object]]) -> None:
    """One call on the way to ending something of a deleted account. What the
    processor no longer holds is ended already."""
    try:
        async with translated(operation):
            await call()
    except PaymentsRefused as refused:
        if "resource_missing" not in refused.message:
            raise


@asynccontextmanager
async def translated(operation: str, deadline: datetime | None = None) -> AsyncIterator[None]:
    """The SDK's errors as the integrations family, by whose problem it is,
    and the call bounded by the request's deadline when it carries one.

    - A refusal of the runtime key itself (401 revoked, 403 without the
      permission) is the process's: `PaymentsKeyRefused`, unavailable
      until a person fixes the key.
    - A throttle (429, after the SDK's own retries) is not right now:
      `ProviderUnavailable`.
    - A refusal of the request (400, 402, 404, an idempotency key reused
      for another request) is the call's: `PaymentsRefused`, and the same
      request gets the same answer.
    - An unreachable processor is `BackendUnreachable`, and anything else,
      a 5xx among it, is a failure of the backend.
    - A call still waiting at the request's deadline, on an attempt or on the
      wait before the next, is cut there, and is `BackendUnreachable` too: to
      the caller the processor did not answer in time. The SDK takes no
      timeout per call, so the cut is what bounds the attempt in flight.

    Each names the processor's error code. No payload and no key reaches
    the message."""
    try:
        async with bounded(deadline, unreachable("stripe", operation)):
            yield
    except stripe.APIConnectionError as error:
        raise BackendUnreachable("stripe", operation, type(error).__name__) from None
    except (stripe.PermissionError, stripe.AuthenticationError) as error:
        reason = error.code or type(error).__name__
        log.error("stripe refused the runtime key for %s: %s", operation, reason)
        raise PaymentsKeyRefused(operation, reason) from None
    except stripe.RateLimitError as error:
        reason = error.code or type(error).__name__
        log.warning("stripe throttled %s: %s", operation, reason)
        throttled = f"the payment processor throttled {operation}: {reason}"
        raise ProviderUnavailable(throttled) from None
    except (
        stripe.InvalidRequestError,
        stripe.CardError,
        stripe.IdempotencyError,
    ) as error:
        reason = error.code or type(error).__name__
        log.warning("stripe refused %s: %s", operation, reason)
        raise PaymentsRefused(operation, reason) from None
    except stripe.StripeError as error:
        raise BackendFailed("stripe", operation, error.code or type(error).__name__) from None
