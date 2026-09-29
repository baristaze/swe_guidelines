"""`acme-ops stripe-bootstrap`: the payment processor's account made to
match one committed definition, `deployment/stripe/desired-state.json`.

The run finds every object it manages by lookup key and by metadata
(`acme_managed=true`, `acme_desired_key=<key>`), creates what is missing,
and never makes a second one. A price is immutable: a changed amount is a
new price that takes the lookup key over (`transfer_lookup_key`), and the old
one is archived. The webhook endpoint's signing secret is shown once, on
create, and goes straight to the environment's secret store; when the
store holds none for an endpoint that exists, the endpoint is rolled
(deleted and made again), because the processor never shows a secret
twice. Nothing here prints a secret. A rerun against an account that
already matches changes nothing and says so.

It runs under a key of its own, ACME_STRIPE_BOOTSTRAP_KEY: a restricted
key held by the person who runs it and read from their shell, which may
touch the products, the prices, the webhook endpoints, and the portal
configurations (BOOTSTRAP_PERMISSIONS) and nothing else. The running
processes never hold it, and it never reaches the cloud."""

import hashlib
import json
import os
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

import aioboto3
from pydantic import BaseModel, ConfigDict, SecretStr

from acme.infra.aws_clients import client_config
from acme.integrations.payments.catalog import (
    CatalogPrice,
    CatalogTier,
    PaymentsCatalogInterface,
    PriceSpec,
    WebhookEndpoint,
)
from acme.integrations.payments.stripe import STRIPE_VERSION
from acme.integrations.settings import key_refusal

DESIRED_STATE = Path("deployment") / "stripe" / "desired-state.json"
ENVIRONMENTS = ("local", "staging", "production")
PORTAL_KEY = "portal"
KEY_VARIABLE = "ACME_STRIPE_BOOTSTRAP_KEY"
CHANGES = frozenset({"created", "updated", "archived", "rolled"})


def webhook_key(env: str) -> str:
    return f"webhook.{env}"


def webhook_secret_name(env: str) -> str:
    """The process credential the environment injects as
    ACME_STRIPE_WEBHOOK_SECRET."""
    return f"acme/{env}/stripe_webhook_secret"


# The definition.


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DesiredTier(_Frozen):
    up_to: int | str
    flat_amount: int | None = None
    unit_amount: int | None = None


class DesiredProduct(_Frozen):
    key: str
    name: str
    description: str


class DesiredPrice(_Frozen):
    lookup_key: str
    product: str
    interval: str
    unit_amount: int | None = None
    tiers_mode: str | None = None
    tiers: tuple[DesiredTier, ...] = ()


class DesiredSubscriptionUpdate(_Frozen):
    prices: tuple[str, ...]
    proration_behavior: str


class DesiredPortal(_Frozen):
    headline: str
    payment_method_update: bool
    invoice_history: bool
    customer_update: tuple[str, ...]
    subscription_cancel: str
    subscription_update: DesiredSubscriptionUpdate


class DesiredState(_Frozen):
    accounts: dict[str, str]
    currency: str
    products: tuple[DesiredProduct, ...]
    prices: tuple[DesiredPrice, ...]
    webhook_endpoints: dict[str, str | None]
    webhook_events: tuple[str, ...]
    portal: DesiredPortal

    def spec(self, price: DesiredPrice) -> PriceSpec:
        return PriceSpec(
            lookup_key=price.lookup_key,
            currency=self.currency,
            interval=price.interval,
            unit_amount=price.unit_amount,
            tiers_mode=price.tiers_mode if price.tiers else None,
            tiers=tuple(
                CatalogTier(
                    up_to=None if tier.up_to == "inf" else int(tier.up_to),
                    flat_amount=tier.flat_amount,
                    unit_amount=tier.unit_amount,
                )
                for tier in price.tiers
            ),
        )


def load_desired(root: Path) -> DesiredState:
    return DesiredState.model_validate(json.loads((root / DESIRED_STATE).read_text()))


def check_key(env: str, key: str) -> None:
    """A restricted key only, and in the environment's mode: production takes
    a live key and every other environment a test key."""
    refusal = key_refusal(KEY_VARIABLE, key, env)
    if refusal is not None:
        raise ValueError(refusal)


# Where the endpoint's secret goes.


class SecretStoreInterface(ABC):
    @abstractmethod
    async def holds(self, name: str) -> bool | None:
        """Whether the store holds a value for the name (not empty, not
        "off"); None when this store cannot say."""
        ...

    @abstractmethod
    async def put(self, name: str, value: SecretStr) -> None: ...

    @abstractmethod
    def describe(self) -> str: ...


class SecretStoreAwsImpl(SecretStoreInterface):
    """Secrets Manager in the environment's account, under the person's own
    sign-in profile. A key exported in the shell would outrank the profile,
    so the two variables are dropped before the session is made."""

    def __init__(self, profile: str, region: str, timeout: timedelta) -> None:
        for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
            os.environ.pop(name, None)
        self._profile = profile
        # aioboto3 is typed loosely; the repo holds its sessions as Any too.
        self._session: Any = aioboto3.Session(profile_name=profile, region_name=region)
        self._config = client_config(timeout)

    def describe(self) -> str:
        return f"aws secrets manager ({self._profile})"

    async def holds(self, name: str) -> bool | None:
        async with self._session.client("secretsmanager", config=self._config) as client:
            try:
                answer = await client.get_secret_value(SecretId=name)
            except client.exceptions.ResourceNotFoundException:
                return False
        value = str(answer.get("SecretString") or "").strip()
        return bool(value) and value.lower() != "off"

    async def put(self, name: str, value: SecretStr) -> None:
        async with self._session.client("secretsmanager", config=self._config) as client:
            try:
                await client.put_secret_value(SecretId=name, SecretString=value.get_secret_value())
            except client.exceptions.ResourceNotFoundException:
                await client.create_secret(Name=name, SecretString=value.get_secret_value())


class SecretStoreNoneImpl(SecretStoreInterface):
    """Holds nothing: the run says where the secret belongs and does not keep
    the one a create showed."""

    def describe(self) -> str:
        return "no secret store"

    async def holds(self, name: str) -> bool | None:
        return None

    async def put(self, name: str, value: SecretStr) -> None:
        return None


# The run.


@dataclass
class Outcome:
    action: str
    kind: str
    key: str
    note: str = ""

    def line(self) -> str:
        text = f"{self.action:<10} {self.kind:<22} {self.key}"
        return f"{text}  ({self.note})" if self.note else text


@dataclass
class Run:
    env: str
    account: str
    outcomes: list[Outcome] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add(self, action: str, kind: str, key: str, note: str = "") -> None:
        self.outcomes.append(Outcome(action, kind, key, note))

    @property
    def changes(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.action in CHANGES)

    def count(self, action: str) -> int:
        return sum(1 for outcome in self.outcomes if outcome.action == action)

    def summary(self) -> str:
        if not self.changes:
            return f"no changes: {self.account} matches the desired state for {self.env}"
        counted = ", ".join(
            f"{self.count(action)} {action}"
            for action in ("created", "updated", "archived", "rolled", "unchanged")
            if self.count(action)
        )
        return f"{counted} on {self.account} for {self.env}"

    def lines(self) -> list[str]:
        return [*(o.line() for o in self.outcomes), *self.notes, self.summary()]


def _same_price(found: CatalogPrice, spec: PriceSpec, product_id: str) -> bool:
    if not found.active or found.product_id != product_id:
        return False
    if found.currency != spec.currency or found.interval != spec.interval:
        return False
    if spec.tiers:
        return found.tiers_mode == (spec.tiers_mode or "volume") and _tiers(found.tiers) == _tiers(
            spec.tiers
        )
    return not found.tiers and found.unit_amount == spec.unit_amount


def _tiers(tiers: tuple[CatalogTier, ...]) -> list[tuple[int | None, int, int]]:
    return [(t.up_to, t.flat_amount or 0, t.unit_amount or 0) for t in tiers]


def portal_body(
    desired: DesiredState, products: Mapping[str, str], prices: Mapping[str, str]
) -> dict[str, Any]:
    """The configuration's body, with the product and price ids this account
    gave the desired keys."""
    portal = desired.portal
    by_product: dict[str, list[str]] = {}
    for price in desired.prices:
        if price.lookup_key in portal.subscription_update.prices:
            by_product.setdefault(products[price.product], []).append(prices[price.lookup_key])
    return {
        "business_profile": {"headline": portal.headline},
        "features": {
            "customer_update": {
                "enabled": bool(portal.customer_update),
                "allowed_updates": list(portal.customer_update),
            },
            "invoice_history": {"enabled": portal.invoice_history},
            "payment_method_update": {"enabled": portal.payment_method_update},
            "subscription_cancel": {"enabled": True, "mode": portal.subscription_cancel},
            "subscription_update": {
                "enabled": True,
                "default_allowed_updates": ["price"],
                "proration_behavior": portal.subscription_update.proration_behavior,
                "products": [
                    {"product": product, "prices": ids} for product, ids in by_product.items()
                ],
            },
        },
    }


def digest(body: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:32]


async def reconcile(
    desired: DesiredState,
    env: str,
    catalog: PaymentsCatalogInterface,
    store: SecretStoreInterface,
    *,
    dry_run: bool = False,
) -> Run:
    run = Run(env=env, account=desired.accounts[env])
    products = await _products(desired, catalog, run, dry_run)
    prices = await _prices(desired, catalog, run, products, dry_run)
    await _portal(desired, catalog, run, products, prices, dry_run)
    await _webhook(desired, env, catalog, store, run, dry_run)
    return run


async def _products(
    desired: DesiredState, catalog: PaymentsCatalogInterface, run: Run, dry_run: bool
) -> dict[str, str]:
    found = {p.key: p for p in await catalog.list_products() if p.key}
    ids: dict[str, str] = {}
    for product in desired.products:
        existing = found.get(product.key)
        if existing is None:
            ids[product.key] = "new"
            if not dry_run:
                ids[product.key] = (
                    await catalog.create_product(product.key, product.name, product.description)
                ).id
            run.add("created", "product", product.key, ids[product.key])
            continue
        ids[product.key] = existing.id
        if (existing.name, existing.description, existing.active) == (
            product.name,
            product.description,
            True,
        ):
            run.add("unchanged", "product", product.key, existing.id)
            continue
        if not dry_run:
            await catalog.update_product(existing.id, product.name, product.description)
        run.add("updated", "product", product.key, existing.id)
    return ids


async def _prices(
    desired: DesiredState,
    catalog: PaymentsCatalogInterface,
    run: Run,
    products: Mapping[str, str],
    dry_run: bool,
) -> dict[str, str]:
    held = {
        p.lookup_key: p
        for p in await catalog.list_prices([p.lookup_key for p in desired.prices])
        if p.lookup_key
    }
    ids: dict[str, str] = {}
    for price in desired.prices:
        spec = desired.spec(price)
        product_id = products[price.product]
        existing = held.get(price.lookup_key)
        if existing is not None and _same_price(existing, spec, product_id):
            ids[price.lookup_key] = existing.id
            run.add("unchanged", "price", price.lookup_key, existing.id)
            continue
        ids[price.lookup_key] = "new"
        if not dry_run:
            ids[price.lookup_key] = (await catalog.create_price(spec, product_id)).id
        if existing is None:
            run.add("created", "price", price.lookup_key, ids[price.lookup_key])
            continue
        run.add(
            "created",
            "price",
            price.lookup_key,
            f"{ids[price.lookup_key]}, took the lookup key from {existing.id}",
        )
        if existing.active and not dry_run:
            await catalog.archive_price(existing.id)
        run.add("archived", "price", price.lookup_key, existing.id)
    return ids


async def _portal(
    desired: DesiredState,
    catalog: PaymentsCatalogInterface,
    run: Run,
    products: Mapping[str, str],
    prices: Mapping[str, str],
    dry_run: bool,
) -> None:
    body = portal_body(desired, products, prices)
    wanted = digest(body)
    existing = next(
        (c for c in await catalog.list_portal_configurations() if c.key == PORTAL_KEY), None
    )
    if existing is None:
        note = "new"
        if not dry_run:
            note = (await catalog.create_portal_configuration(PORTAL_KEY, body, wanted)).id
        run.add("created", "portal configuration", PORTAL_KEY, note)
    elif existing.desired_hash == wanted and existing.active:
        run.add("unchanged", "portal configuration", PORTAL_KEY, existing.id)
    else:
        if not dry_run:
            await catalog.update_portal_configuration(existing.id, body, wanted)
        run.add("updated", "portal configuration", PORTAL_KEY, existing.id)


async def _webhook(
    desired: DesiredState,
    env: str,
    catalog: PaymentsCatalogInterface,
    store: SecretStoreInterface,
    run: Run,
    dry_run: bool,
) -> None:
    url = desired.webhook_endpoints.get(env)
    key = webhook_key(env)
    if url is None:
        run.notes.append(
            f"{env} has no webhook endpoint: the processor cannot reach it. For a local run, "
            "`stripe listen --forward-to http://127.0.0.1:8000/webhooks/stripe` forwards "
            "deliveries and prints the signing secret it uses for ACME_STRIPE_WEBHOOK_SECRET"
        )
        return
    events = tuple(sorted(desired.webhook_events))
    existing = _endpoint_for(await catalog.list_webhook_endpoints(), key, url)
    name = webhook_secret_name(env)
    if existing is None:
        await _create_endpoint(catalog, store, run, key, url, events, name, "created", dry_run)
        return
    if existing.api_version not in (None, STRIPE_VERSION):
        run.notes.append(
            f"the endpoint {existing.id} delivers under {existing.api_version}, not "
            f"{STRIPE_VERSION}; an endpoint's version is set on create, so roll it to move it"
        )
    held = await store.holds(name)
    if held is False:
        if not dry_run:
            await catalog.delete_webhook_endpoint(existing.id)
        await _create_endpoint(
            catalog,
            store,
            run,
            key,
            url,
            events,
            name,
            "rolled",
            dry_run,
            note=f"{name} held no secret; {existing.id} deleted and made again",
        )
        return
    if existing.url == url and tuple(sorted(existing.enabled_events)) == events:
        run.add("unchanged", "webhook endpoint", key, f"{existing.id}, {url}")
    else:
        if not dry_run:
            await catalog.update_webhook_endpoint(existing.id, url, events)
        run.add("updated", "webhook endpoint", key, f"{existing.id}, {url}")
    if held is None:
        run.notes.append(
            f"the endpoint's signing secret is not held here ({store.describe()}); it belongs in "
            f"{name}. Rerun with --secret-store aws: when {name} holds none, the run rolls the "
            "endpoint and stores the new secret there"
        )


def _endpoint_for(endpoints: list[WebhookEndpoint], key: str, url: str) -> WebhookEndpoint | None:
    by_key = [e for e in endpoints if e.key == key]
    if by_key:
        return by_key[0]
    return next((e for e in endpoints if e.url == url), None)


async def _create_endpoint(
    catalog: PaymentsCatalogInterface,
    store: SecretStoreInterface,
    run: Run,
    key: str,
    url: str,
    events: tuple[str, ...],
    name: str,
    action: str,
    dry_run: bool,
    note: str = "",
) -> None:
    if dry_run:
        run.add(action, "webhook endpoint", key, note or url)
        return
    created = await catalog.create_webhook_endpoint(key, url, events, STRIPE_VERSION)
    await store.put(name, created.secret)
    stored = f"secret written to {name}"
    if isinstance(store, SecretStoreNoneImpl):
        stored = (
            f"its signing secret was shown once and is not stored here; rerun with "
            f"--secret-store aws to roll the endpoint and write the new one to {name}"
        )
    run.add(action, "webhook endpoint", key, f"{created.endpoint.id}, {note or url}; {stored}")
