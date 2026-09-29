"""The catalog: what an account sells and where it delivers, as the
bootstrap reconciles it. Products, prices by lookup key, the webhook
endpoint, and the Billing Portal configuration. Every object Acme manages
carries `acme_managed=true` and `acme_desired_key=<key>` in its metadata,
which is how a rerun finds it instead of making a second one.

It is a separate interface from payments because its caller is separate:
the operator's bootstrap command, never a serving process. It has the real
client and an in-memory twin, which the bootstrap's tests run against."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

import stripe
from pydantic import SecretStr

from acme.infra.base import InfraModel
from acme.infra.exceptions import BackendFailed, BackendUnreachable
from acme.integrations.exceptions import PaymentsRefused
from acme.integrations.payments.stripe import STRIPE_VERSION

MANAGED = "acme_managed"
DESIRED_KEY = "acme_desired_key"
DESIRED_HASH = "acme_desired_hash"
"""On a portal configuration: a digest of the body it was last written
with, since the processor's answer does not echo the body back whole."""


def managed_metadata(key: str, **extra: str) -> dict[str, str]:
    return {MANAGED: "true", DESIRED_KEY: key, **extra}


class CatalogTier(InfraModel):
    up_to: int | None
    """None is the last tier, `inf`."""
    flat_amount: int | None = None
    unit_amount: int | None = None


class CatalogProduct(InfraModel):
    id: str
    key: str | None
    name: str
    description: str | None
    active: bool


class PriceSpec(InfraModel):
    """A price as the desired state says it: either a flat `unit_amount` or
    volume `tiers`, monthly or yearly, in one currency."""

    lookup_key: str
    currency: str
    interval: str
    unit_amount: int | None = None
    tiers_mode: str | None = None
    tiers: tuple[CatalogTier, ...] = ()


class CatalogPrice(InfraModel):
    id: str
    lookup_key: str | None
    product_id: str
    active: bool
    currency: str
    interval: str | None
    unit_amount: int | None
    tiers_mode: str | None
    tiers: tuple[CatalogTier, ...]


class WebhookEndpoint(InfraModel):
    id: str
    key: str | None
    url: str
    enabled_events: tuple[str, ...]
    api_version: str | None


class CreatedWebhookEndpoint(InfraModel):
    """The one answer that carries the endpoint's signing secret."""

    endpoint: WebhookEndpoint
    secret: SecretStr


class PortalConfiguration(InfraModel):
    id: str
    key: str | None
    active: bool
    desired_hash: str | None


class PaymentsCatalogInterface(ABC):
    @abstractmethod
    async def list_products(self) -> list[CatalogProduct]:
        """Every product of the account, active or not."""
        ...

    @abstractmethod
    async def create_product(self, key: str, name: str, description: str) -> CatalogProduct: ...

    @abstractmethod
    async def update_product(
        self, product_id: str, name: str, description: str
    ) -> CatalogProduct: ...

    @abstractmethod
    async def list_prices(self, lookup_keys: Sequence[str]) -> list[CatalogPrice]:
        """The prices that hold these lookup keys, active or not."""
        ...

    @abstractmethod
    async def create_price(self, spec: PriceSpec, product_id: str) -> CatalogPrice:
        """A new price under the spec's lookup key, moving the key from the
        price that holds it (`transfer_lookup_key`)."""
        ...

    @abstractmethod
    async def archive_price(self, price_id: str) -> None:
        """Prices are immutable; an old one is made inactive, never deleted."""
        ...

    @abstractmethod
    async def list_webhook_endpoints(self) -> list[WebhookEndpoint]: ...

    @abstractmethod
    async def create_webhook_endpoint(
        self, key: str, url: str, events: Sequence[str], api_version: str
    ) -> CreatedWebhookEndpoint: ...

    @abstractmethod
    async def update_webhook_endpoint(
        self, endpoint_id: str, url: str, events: Sequence[str]
    ) -> WebhookEndpoint: ...

    @abstractmethod
    async def delete_webhook_endpoint(self, endpoint_id: str) -> None: ...

    @abstractmethod
    async def list_portal_configurations(self) -> list[PortalConfiguration]: ...

    @abstractmethod
    async def create_portal_configuration(
        self, key: str, body: Mapping[str, Any], desired_hash: str
    ) -> PortalConfiguration: ...

    @abstractmethod
    async def update_portal_configuration(
        self, configuration_id: str, body: Mapping[str, Any], desired_hash: str
    ) -> PortalConfiguration: ...

    @abstractmethod
    def describe(self) -> str: ...

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...


class CatalogStripeImpl(PaymentsCatalogInterface):
    """The catalog on Stripe, under the account `Stripe-Context` names and the
    pinned `Stripe-Version`, every call bounded by the timeout."""

    def __init__(self, *, api_key: str, account_id: str, timeout: timedelta) -> None:
        self._api_key = api_key
        self._account_id = account_id
        self._timeout = timeout
        self._http: stripe.HTTPXClient | None = None
        self._client: stripe.StripeClient | None = None

    def describe(self) -> str:
        return f"catalog=stripe ({self._account_id}, {STRIPE_VERSION})"

    async def start(self) -> None:
        self._http = stripe.HTTPXClient(timeout=self._timeout.total_seconds())
        self._client = stripe.StripeClient(
            self._api_key,
            stripe_context=self._account_id,
            stripe_version=STRIPE_VERSION,
            http_client=self._http,
            max_network_retries=2,
        )

    async def close(self) -> None:
        if self._http is not None:
            await self._http.close_async()
        self._http = None
        self._client = None

    def _v1(self) -> Any:
        if self._client is None:
            raise RuntimeError("the catalog client is used before start()")
        return self._client.v1

    async def list_products(self) -> list[CatalogProduct]:
        async with translated("list products"):
            page = await self._v1().products.list_async({"limit": 100})
            return [_product(p.to_dict()) async for p in page.auto_paging_iter()]

    async def create_product(self, key: str, name: str, description: str) -> CatalogProduct:
        async with translated("create product"):
            product = await self._v1().products.create_async(
                {"name": name, "description": description, "metadata": managed_metadata(key)}
            )
        return _product(product.to_dict())

    async def update_product(self, product_id: str, name: str, description: str) -> CatalogProduct:
        async with translated("update product"):
            product = await self._v1().products.update_async(
                product_id, {"name": name, "description": description, "active": True}
            )
        return _product(product.to_dict())

    async def list_prices(self, lookup_keys: Sequence[str]) -> list[CatalogPrice]:
        async with translated("list prices"):
            page = await self._v1().prices.list_async(
                {"lookup_keys": list(lookup_keys), "limit": 100, "expand": ["data.tiers"]}
            )
        return [_price(p.to_dict()) for p in page.data]

    async def create_price(self, spec: PriceSpec, product_id: str) -> CatalogPrice:
        params: dict[str, Any] = {
            "currency": spec.currency,
            "product": product_id,
            "recurring": {"interval": spec.interval},
            "lookup_key": spec.lookup_key,
            "transfer_lookup_key": True,
            "metadata": managed_metadata(spec.lookup_key),
            "expand": ["tiers"],
        }
        if spec.tiers:
            params["billing_scheme"] = "tiered"
            params["tiers_mode"] = spec.tiers_mode or "volume"
            params["tiers"] = [_tier_param(tier) for tier in spec.tiers]
        else:
            params["unit_amount"] = spec.unit_amount
        async with translated("create price"):
            price = await self._v1().prices.create_async(params)
        return _price(price.to_dict())

    async def archive_price(self, price_id: str) -> None:
        async with translated("archive price"):
            await self._v1().prices.update_async(price_id, {"active": False})

    async def list_webhook_endpoints(self) -> list[WebhookEndpoint]:
        async with translated("list webhook endpoints"):
            page = await self._v1().webhook_endpoints.list_async({"limit": 100})
        return [_endpoint(e.to_dict()) for e in page.data]

    async def create_webhook_endpoint(
        self, key: str, url: str, events: Sequence[str], api_version: str
    ) -> CreatedWebhookEndpoint:
        async with translated("create webhook endpoint"):
            endpoint = await self._v1().webhook_endpoints.create_async(
                {
                    "url": url,
                    "enabled_events": list(events),
                    "api_version": api_version,
                    "description": "Acme: plans and billing",
                    "metadata": managed_metadata(key),
                }
            )
        raw = endpoint.to_dict()
        return CreatedWebhookEndpoint(endpoint=_endpoint(raw), secret=SecretStr(raw["secret"]))

    async def update_webhook_endpoint(
        self, endpoint_id: str, url: str, events: Sequence[str]
    ) -> WebhookEndpoint:
        async with translated("update webhook endpoint"):
            endpoint = await self._v1().webhook_endpoints.update_async(
                endpoint_id, {"url": url, "enabled_events": list(events)}
            )
        return _endpoint(endpoint.to_dict())

    async def delete_webhook_endpoint(self, endpoint_id: str) -> None:
        async with translated("delete webhook endpoint"):
            await self._v1().webhook_endpoints.delete_async(endpoint_id)

    async def list_portal_configurations(self) -> list[PortalConfiguration]:
        async with translated("list billing portal configurations"):
            page = await self._v1().billing_portal.configurations.list_async({"limit": 100})
        return [_portal(c.to_dict()) for c in page.data]

    async def create_portal_configuration(
        self, key: str, body: Mapping[str, Any], desired_hash: str
    ) -> PortalConfiguration:
        params = {**body, "metadata": managed_metadata(key, **{DESIRED_HASH: desired_hash})}
        async with translated("create billing portal configuration"):
            configuration = await self._v1().billing_portal.configurations.create_async(params)
        return _portal(configuration.to_dict())

    async def update_portal_configuration(
        self, configuration_id: str, body: Mapping[str, Any], desired_hash: str
    ) -> PortalConfiguration:
        params = {**body, "active": True, "metadata": {DESIRED_HASH: desired_hash}}
        async with translated("update billing portal configuration"):
            configuration = await self._v1().billing_portal.configurations.update_async(
                configuration_id, params
            )
        return _portal(configuration.to_dict())


class CatalogTwinImpl(PaymentsCatalogInterface):
    """The catalog in memory, with the processor's rules that matter to a
    reconcile: a lookup key is held by one price, a transfer moves it, and an
    endpoint's secret is shown once, on create. `calls` counts the writes."""

    def __init__(self) -> None:
        self.products: dict[str, CatalogProduct] = {}
        self.prices: dict[str, CatalogPrice] = {}
        self.endpoints: dict[str, WebhookEndpoint] = {}
        self.portals: dict[str, PortalConfiguration] = {}
        self.writes: list[str] = []
        self._counter = 0

    def _id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}_twin_{self._counter:06d}"

    def describe(self) -> str:
        return "catalog=twin"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def list_products(self) -> list[CatalogProduct]:
        return list(self.products.values())

    async def create_product(self, key: str, name: str, description: str) -> CatalogProduct:
        product = CatalogProduct(
            id=self._id("prod"), key=key, name=name, description=description, active=True
        )
        self.products[product.id] = product
        self.writes.append(f"create product {key}")
        return product

    async def update_product(self, product_id: str, name: str, description: str) -> CatalogProduct:
        product = self.products[product_id].model_copy(
            update={"name": name, "description": description, "active": True}
        )
        self.products[product_id] = product
        self.writes.append(f"update product {product.key}")
        return product

    async def list_prices(self, lookup_keys: Sequence[str]) -> list[CatalogPrice]:
        return [p for p in self.prices.values() if p.lookup_key in set(lookup_keys)]

    async def create_price(self, spec: PriceSpec, product_id: str) -> CatalogPrice:
        for held in list(self.prices.values()):
            if held.lookup_key == spec.lookup_key:
                self.prices[held.id] = held.model_copy(update={"lookup_key": None})
        price = CatalogPrice(
            id=self._id("price"),
            lookup_key=spec.lookup_key,
            product_id=product_id,
            active=True,
            currency=spec.currency,
            interval=spec.interval,
            unit_amount=None if spec.tiers else spec.unit_amount,
            tiers_mode=spec.tiers_mode if spec.tiers else None,
            tiers=spec.tiers,
        )
        self.prices[price.id] = price
        self.writes.append(f"create price {spec.lookup_key}")
        return price

    async def archive_price(self, price_id: str) -> None:
        self.prices[price_id] = self.prices[price_id].model_copy(update={"active": False})
        self.writes.append(f"archive price {price_id}")

    async def list_webhook_endpoints(self) -> list[WebhookEndpoint]:
        return list(self.endpoints.values())

    async def create_webhook_endpoint(
        self, key: str, url: str, events: Sequence[str], api_version: str
    ) -> CreatedWebhookEndpoint:
        endpoint = WebhookEndpoint(
            id=self._id("we"),
            key=key,
            url=url,
            enabled_events=tuple(events),
            api_version=api_version,
        )
        self.endpoints[endpoint.id] = endpoint
        self.writes.append(f"create webhook endpoint {key}")
        return CreatedWebhookEndpoint(
            endpoint=endpoint, secret=SecretStr(f"whsec_{self._id('secret')}")
        )

    async def update_webhook_endpoint(
        self, endpoint_id: str, url: str, events: Sequence[str]
    ) -> WebhookEndpoint:
        endpoint = self.endpoints[endpoint_id].model_copy(
            update={"url": url, "enabled_events": tuple(events)}
        )
        self.endpoints[endpoint_id] = endpoint
        self.writes.append(f"update webhook endpoint {endpoint.key}")
        return endpoint

    async def delete_webhook_endpoint(self, endpoint_id: str) -> None:
        del self.endpoints[endpoint_id]
        self.writes.append(f"delete webhook endpoint {endpoint_id}")

    async def list_portal_configurations(self) -> list[PortalConfiguration]:
        return list(self.portals.values())

    async def create_portal_configuration(
        self, key: str, body: Mapping[str, Any], desired_hash: str
    ) -> PortalConfiguration:
        configuration = PortalConfiguration(
            id=self._id("bpc"), key=key, active=True, desired_hash=desired_hash
        )
        self.portals[configuration.id] = configuration
        self.writes.append(f"create portal configuration {key}")
        return configuration

    async def update_portal_configuration(
        self, configuration_id: str, body: Mapping[str, Any], desired_hash: str
    ) -> PortalConfiguration:
        configuration = self.portals[configuration_id].model_copy(
            update={"active": True, "desired_hash": desired_hash}
        )
        self.portals[configuration_id] = configuration
        self.writes.append(f"update portal configuration {configuration.key}")
        return configuration


def _metadata(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    return raw.get("metadata") or {}


def _product(raw: Mapping[str, Any]) -> CatalogProduct:
    return CatalogProduct(
        id=str(raw["id"]),
        key=_metadata(raw).get(DESIRED_KEY),
        name=str(raw.get("name") or ""),
        description=raw.get("description"),
        active=bool(raw.get("active")),
    )


def _price(raw: Mapping[str, Any]) -> CatalogPrice:
    recurring = raw.get("recurring") or {}
    product = raw.get("product")
    if isinstance(product, Mapping):
        product = product.get("id")
    return CatalogPrice(
        id=str(raw["id"]),
        lookup_key=raw.get("lookup_key"),
        product_id=str(product),
        active=bool(raw.get("active")),
        currency=str(raw.get("currency") or ""),
        interval=recurring.get("interval"),
        unit_amount=raw.get("unit_amount"),
        tiers_mode=raw.get("tiers_mode"),
        tiers=tuple(
            CatalogTier(
                up_to=tier.get("up_to"),
                flat_amount=tier.get("flat_amount"),
                unit_amount=tier.get("unit_amount"),
            )
            for tier in (raw.get("tiers") or [])
        ),
    )


def _endpoint(raw: Mapping[str, Any]) -> WebhookEndpoint:
    return WebhookEndpoint(
        id=str(raw["id"]),
        key=_metadata(raw).get(DESIRED_KEY),
        url=str(raw.get("url") or ""),
        enabled_events=tuple(raw.get("enabled_events") or ()),
        api_version=raw.get("api_version"),
    )


def _portal(raw: Mapping[str, Any]) -> PortalConfiguration:
    metadata = _metadata(raw)
    return PortalConfiguration(
        id=str(raw["id"]),
        key=metadata.get(DESIRED_KEY),
        active=bool(raw.get("active")),
        desired_hash=metadata.get(DESIRED_HASH),
    )


def _tier_param(tier: CatalogTier) -> dict[str, Any]:
    param: dict[str, Any] = {"up_to": "inf" if tier.up_to is None else tier.up_to}
    if tier.flat_amount is not None:
        param["flat_amount"] = tier.flat_amount
    if tier.unit_amount is not None:
        param["unit_amount"] = tier.unit_amount
    return param


@asynccontextmanager
async def translated(operation: str) -> AsyncIterator[None]:
    """The SDK's errors as the integrations family, naming the processor's
    error code and message (which names a missing permission) and never a
    payload or a key."""
    try:
        yield
    except stripe.APIConnectionError as error:
        raise BackendUnreachable("stripe", operation, type(error).__name__) from None
    except (
        stripe.PermissionError,
        stripe.AuthenticationError,
        stripe.InvalidRequestError,
        stripe.IdempotencyError,
    ) as error:
        reason = error.code or type(error).__name__
        # An authentication error's message quotes part of the key: left out.
        detail = "" if isinstance(error, stripe.AuthenticationError) else str(error)
        raise PaymentsRefused(operation, f"{reason}: {detail}" if detail else reason) from None
    except stripe.StripeError as error:
        raise BackendFailed("stripe", operation, error.code or type(error).__name__) from None
