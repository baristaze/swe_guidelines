"""The payments integration without an account: the signature check every
delivery passes, the ids a delivery names, the twin's refusal outside a
local environment, the boot's refusal of a key that is not a restricted key
of the environment's mode, the processes
reading the runtime key alone, the boot's check of what the key may read,
the real client's reading of a subscription and of the portal
configuration a session names, the timeout and the retries
every call it makes is sent with, the request's deadline a call a request
makes is cut at, and its translation of a failure by whose problem it is:
the key's, the request's, or the processor's."""

import asyncio
import json
import logging
import time
from collections.abc import Callable, Coroutine
from datetime import timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest
import stripe

from acme.infra.base import utcnow
from acme.infra.deadline import PASSED
from acme.infra.exceptions import BackendFailed, BackendUnreachable
from acme.integrations.exceptions import (
    DeliveryRefused,
    PaymentsKeyRefused,
    PaymentsRefused,
    PaymentsUnconfigured,
    ProviderUnavailable,
    UnsafeIntegration,
)
from acme.integrations.impl.configured import payments_for, refuse_unsafe_payments
from acme.integrations.payments.deliveries import delivery_of, sign, verified
from acme.integrations.payments.permissions import CHECKOUT_SESSIONS, RUNTIME_PERMISSIONS
from acme.integrations.payments.stripe import PaymentsStripeImpl, subscription_of, translated
from acme.integrations.payments.twin import TWIN_WEBHOOK_SECRET, PaymentsTwinImpl
from acme.integrations.payments.types import ORG_METADATA_KEY, delivery_key
from acme.integrations.settings import IntegrationsSettings, key_mode

SECRET = "whsec_test_only"


def event(event_type: str, obj: dict[str, object], event_id: str = "evt_1") -> bytes:
    return json.dumps(
        {
            "id": event_id,
            "object": "event",
            "type": event_type,
            "created": int(time.time()),
            "livemode": False,
            "data": {"object": obj},
        }
    ).encode()


def settings(**fields: object) -> IntegrationsSettings:
    return IntegrationsSettings.model_validate({"_env_file": None, **fields})


def answered[T: stripe.StripeObject](kind: type[T], values: dict[str, Any]) -> T:
    """An answer built the way the SDK builds one from the processor's JSON:
    an object of its kind, the nested ones too. None of them is a dict, so a
    fake that answers one catches a read that works only on a dict."""
    return kind.construct_from(values, "rk_test_x")


# The signature.


def test_a_delivery_signed_with_the_secret_checks_out() -> None:
    org = uuid4()
    payload = event(
        "customer.subscription.updated",
        {"id": "sub_1", "customer": "cus_1", "metadata": {ORG_METADATA_KEY: str(org)}},
    )
    delivery = verified(payload, sign(payload, SECRET), SECRET)
    assert (delivery.event_id, delivery.event_type) == ("evt_1", "customer.subscription.updated")
    assert (delivery.customer_id, delivery.subscription_id, delivery.org_hint) == (
        "cus_1",
        "sub_1",
        org,
    )
    assert delivery.idempotency_key == delivery_key("evt_1")


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "t=1,v1=00",
        sign(b"another body", SECRET),
        sign(event("invoice.paid", {}), "whsec_someone_else"),
    ],
)
def test_a_delivery_that_does_not_check_out_is_refused(header: str | None) -> None:
    payload = event("invoice.paid", {})
    with pytest.raises(DeliveryRefused) as refused:
        verified(payload, header, SECRET)
    assert refused.value.http_status == 400
    assert refused.value.code == "webhook_signature_invalid"
    assert SECRET not in refused.value.message


def test_a_delivery_outside_the_replay_window_is_refused() -> None:
    payload = event("invoice.paid", {})
    stale = sign(payload, SECRET, int(time.time()) - 3600)
    with pytest.raises(DeliveryRefused):
        verified(payload, stale, SECRET)


def test_a_signed_body_that_is_not_an_event_is_refused() -> None:
    for payload in (b"[]", b"not json", json.dumps({"id": "evt"}).encode()):
        with pytest.raises(DeliveryRefused):
            verified(payload, sign(payload, SECRET), SECRET)


# The ids a delivery names.


def test_a_checkout_names_the_org_by_its_client_reference() -> None:
    org = uuid4()
    raw = json.loads(
        event(
            "checkout.session.completed",
            {"customer": "cus_1", "subscription": "sub_1", "client_reference_id": str(org)},
        )
    )
    delivery = delivery_of(raw)
    assert (delivery.customer_id, delivery.subscription_id, delivery.org_hint) == (
        "cus_1",
        "sub_1",
        org,
    )


def test_an_invoice_names_its_subscription_where_the_pinned_version_puts_it() -> None:
    org = uuid4()
    raw = json.loads(
        event(
            "invoice.payment_failed",
            {
                "customer": {"id": "cus_1"},
                "parent": {
                    "subscription_details": {
                        "subscription": "sub_1",
                        "metadata": {ORG_METADATA_KEY: str(org)},
                    }
                },
            },
        )
    )
    delivery = delivery_of(raw)
    assert (delivery.customer_id, delivery.subscription_id, delivery.org_hint) == (
        "cus_1",
        "sub_1",
        org,
    )
    older = delivery_of(json.loads(event("invoice.paid", {"subscription": "sub_2"})))
    assert older.subscription_id == "sub_2" and older.org_hint is None


def test_an_event_of_another_type_names_nothing_but_itself() -> None:
    delivery = delivery_of(json.loads(event("product.created", {"id": "prod_1"})))
    assert (delivery.customer_id, delivery.subscription_id, delivery.org_hint) == (
        None,
        None,
        None,
    )


# The twin.


def test_the_twin_refuses_to_run_outside_a_local_environment() -> None:
    for environment in ("dev", "staging", "production"):
        with pytest.raises(UnsafeIntegration):
            PaymentsTwinImpl(environment=environment)
        with pytest.raises(UnsafeIntegration):
            payments_for(settings(billing_backend="twin"), environment)
    assert payments_for(settings(billing_backend="twin"), "test").describe() == "payments=twin"


async def test_the_twins_deliveries_pass_the_check_the_real_ones_do() -> None:
    twin = PaymentsTwinImpl(environment="test")
    org = uuid4()
    customer = await twin.create_customer(org, "Ajax")
    assert await twin.create_customer(org, "Ajax") == customer
    url = await twin.create_checkout(
        customer_id=customer,
        org_id=org,
        lookup_key="acme.pro.monthly",
        quantity=1,
        success_url="http://portal.test/done",
        cancel_url="http://portal.test/cancelled",
    )
    payload, header = twin.complete_checkout(url)
    delivery = verified(payload, header, TWIN_WEBHOOK_SECRET)
    assert delivery.org_hint == org and delivery.customer_id == customer
    assert delivery.subscription_id is not None
    subscription = await twin.read_subscription(delivery.subscription_id)
    assert subscription is not None and subscription.status == "active"
    assert await twin.read_customer_org(customer) == org


# The boot's refusals.


@pytest.mark.parametrize(
    ("key", "mode"),
    [
        ("sk_live_x", "live"),
        ("rk_live_x", "live"),
        ("sk_org_live_x", "live"),
        ("sk_test_x", "test"),
        ("rk_test_x", "test"),
        ("sk_org_test_x", "test"),
        ("pk_test_x", None),
    ],
)
def test_a_keys_prefix_says_its_mode(key: str, mode: str | None) -> None:
    assert key_mode(key) == mode


@pytest.mark.parametrize(
    ("environment", "key", "refused"),
    [
        ("production", "rk_live_x", False),
        ("production", "rk_test_x", True),
        ("staging", "rk_test_x", False),
        ("staging", "rk_live_x", True),
        ("local", "rk_live_x", True),
        ("local", "rk_test_x", False),
        ("staging", "pk_test_x", True),
    ],
)
def test_a_key_whose_mode_is_not_the_environments_is_refused_at_boot(
    environment: str, key: str, refused: bool
) -> None:
    configured = settings(billing_backend="stripe", stripe_runtime_key=key)
    if refused:
        with pytest.raises(UnsafeIntegration) as refusal:
            refuse_unsafe_payments(configured, environment)
        assert key not in refusal.value.message
        assert "ACME_STRIPE_RUNTIME_KEY" in refusal.value.message
    else:
        refuse_unsafe_payments(configured, environment)


@pytest.mark.parametrize(
    ("environment", "key", "said"),
    [
        ("staging", "rk_org_test_x", "organization key"),
        ("staging", "sk_org_test_x", "organization key"),
        ("production", "sk_org_live_x", "organization key"),
        ("staging", "sk_test_x", "secret key"),
        ("production", "sk_live_x", "secret key"),
    ],
)
def test_only_a_restricted_key_of_the_account_is_taken_at_boot(
    environment: str, key: str, said: str
) -> None:
    """An organization key reaches every account, and a secret key may do
    everything in one; the processes take a restricted key alone."""
    configured = settings(billing_backend="stripe", stripe_runtime_key=key)
    with pytest.raises(UnsafeIntegration) as refusal:
        refuse_unsafe_payments(configured, environment)
    assert said in refusal.value.message
    assert key not in refusal.value.message


def test_the_processes_read_the_runtime_key_and_never_the_bootstraps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ACME_STRIPE_BOOTSTRAP_KEY", "rk_test_bootstrap")
    monkeypatch.delenv("ACME_STRIPE_RUNTIME_KEY", raising=False)
    alone = IntegrationsSettings.model_validate(
        {"_env_file": None, "stripe_account_id": "acct_test"}
    )
    assert alone.stripe_runtime_key is None
    assert payments_for(alone, "staging").configured is False
    assert "rk_test_bootstrap" not in repr(alone.model_dump())
    monkeypatch.setenv("ACME_STRIPE_RUNTIME_KEY", "rk_test_runtime")
    both = IntegrationsSettings.model_validate(
        {"_env_file": None, "stripe_account_id": "acct_test"}
    )
    assert both.stripe_runtime_key is not None
    assert both.stripe_runtime_key.get_secret_value() == "rk_test_runtime"
    assert payments_for(both, "staging").configured is True


async def test_no_key_leaves_billing_unconfigured_and_every_call_says_so() -> None:
    for off in (None, "", "off"):
        payments = payments_for(
            settings(billing_backend="stripe", stripe_runtime_key=off), "staging"
        )
        await payments.start()
        assert payments.configured is False
        with pytest.raises(PaymentsUnconfigured) as refused:
            await payments.create_customer(uuid4(), "Ajax")
        assert refused.value.http_status == 503
        with pytest.raises(PaymentsUnconfigured):
            payments.verify_delivery(b"{}", "t=1,v1=00")
        await payments.close()


# The boot's check of what the key may read.


class _Lister:
    def __init__(self, name: str, refused: set[str], error: type[Exception]) -> None:
        self._name = name
        self._refused = refused
        self._error = error
        self.params: list[dict[str, Any]] = []

    async def list_async(self, params: dict[str, Any]) -> object:
        self.params.append(params)
        if self._name in self._refused:
            raise self._error("refused")
        return answered(stripe.ListObject, {"object": "list", "data": []})


class _ProcessorOf:
    """The SDK client's `v1` as far as the key check reads it: five lists,
    each refused when the key lacks its resource."""

    def __init__(self, refused: set[str], error: type[Exception] = stripe.PermissionError) -> None:
        def lister(name: str) -> _Lister:
            return _Lister(name, refused, error)

        self.customers = lister("customers")
        self.subscriptions = lister("subscriptions")
        self.prices = lister("prices")
        self.checkout = type("Checkout", (), {"sessions": lister("checkout.sessions")})()
        self.billing_portal = type(
            "Portal", (), {"configurations": lister("billing_portal.configurations")}
        )()
        self.v1 = self


async def _checked(refused: set[str], error: type[Exception] = stripe.PermissionError) -> Any:
    payments = PaymentsStripeImpl(
        api_key="rk_test_x",
        account_id="acct_test",
        webhook_secret=SECRET,
        timeout=timedelta(seconds=3),
        check_at_start=False,
    )
    await payments.start()
    await payments.close()
    payments._client = _ProcessorOf(refused, error)  # type: ignore[assignment]
    return payments


async def test_a_key_that_reads_every_resource_passes_the_check() -> None:
    payments = await _checked(set())
    assert await payments.check_access() == ()
    assert "lacks" not in payments.describe()
    client = payments._client
    assert all(
        lister.params == [{"limit": 1}]
        for lister in (
            client.customers,
            client.subscriptions,
            client.prices,
            client.checkout.sessions,
            client.billing_portal.configurations,
        )
    )


async def test_a_key_without_checkout_sessions_is_named_at_start(
    caplog: pytest.LogCaptureFixture,
) -> None:
    payments = await _checked({"checkout.sessions"})
    with caplog.at_level(logging.ERROR):
        assert await payments.check_access() == (CHECKOUT_SESSIONS,)
    assert payments.describe().endswith("; the key lacks Checkout Sessions)")
    assert "lacks Checkout Sessions (group Checkout Sessions)" in caplog.text
    assert "rk_test_x" not in caplog.text


async def test_a_key_every_read_refuses_is_named_as_the_wrong_account(
    caplog: pytest.LogCaptureFixture,
) -> None:
    payments = await _checked(
        {
            "customers",
            "subscriptions",
            "prices",
            "checkout.sessions",
            "billing_portal.configurations",
        },
        stripe.AuthenticationError,
    )
    with caplog.at_level(logging.ERROR):
        assert await payments.check_access() == RUNTIME_PERMISSIONS
    assert "not a key of acct_test" in caplog.text


async def test_a_processor_that_does_not_answer_leaves_the_check_open() -> None:
    payments = await _checked({"customers"}, stripe.APIConnectionError)
    assert await payments.check_access() == ()
    assert "lacks" not in payments.describe()


async def test_the_real_client_names_the_account_and_the_pinned_version() -> None:
    payments = PaymentsStripeImpl(
        api_key="rk_test_x",
        account_id="acct_test",
        webhook_secret=SECRET,
        timeout=timedelta(seconds=3),
        check_at_start=False,
    )
    await payments.start()
    try:
        assert payments.describe().startswith("payments=stripe (acct_test, ")
        client = payments._client  # the SDK's own, read to see what every call names
        assert client is not None
        options = client._requestor._options
        assert options.stripe_context == "acct_test"
        assert options.stripe_version == stripe.api_version
    finally:
        await payments.close()


async def test_every_call_is_sent_with_the_timeout_and_a_timeout_is_tried_twice_more(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SDK's transport names the timeout from settings on every request,
    to the fraction, and nothing in the SDK names another over it. Its two
    retries take a timeout too, so a Stripe that hangs costs three attempts
    of the timeout, and the call is then unreachable."""
    monkeypatch.setattr(stripe.HTTPXClient, "_sleep_time_seconds", lambda self, retries: 0.0)
    sent: list[Any] = []

    def hang(request: httpx.Request) -> httpx.Response:
        sent.append(request.extensions["timeout"])
        raise httpx.ReadTimeout("no answer", request=request)

    payments = PaymentsStripeImpl(
        api_key="rk_test_x",
        account_id="acct_test",
        webhook_secret=SECRET,
        timeout=timedelta(seconds=2.5),
        check_at_start=False,
    )
    await payments.start()
    transport = payments._http  # the SDK's own, given a transport that records
    assert transport is not None
    await transport._client_async.aclose()
    transport._client_async = httpx.AsyncClient(transport=httpx.MockTransport(hang))
    try:
        with pytest.raises(BackendUnreachable):
            await payments.read_subscription("sub_1")
    finally:
        await payments.close()
    at = 2.5
    assert sent == [{"connect": at, "read": at, "write": at, "pool": at}] * 3


async def stripe_answering(
    answer: Callable[[httpx.Request], Coroutine[None, None, httpx.Response]],
) -> PaymentsStripeImpl:
    """The real client, as the process opens it (a ten second timeout, two
    retries), over a transport the test answers."""
    payments = PaymentsStripeImpl(
        api_key="rk_test_x",
        account_id="acct_test",
        webhook_secret=SECRET,
        timeout=timedelta(seconds=10),
        check_at_start=False,
    )
    await payments.start()
    transport = payments._http  # the SDK's own, given a transport the test answers
    assert transport is not None
    await transport._client_async.aclose()
    transport._client_async = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    return payments


async def test_a_stripe_that_hangs_ends_at_the_deadline_not_after_its_retries() -> None:
    """At a ten second timeout and two retries a Stripe that hangs holds a
    call about thirty seconds. Under a request's deadline the attempt in
    flight is cut there, and the call is unreachable."""
    sent: list[httpx.Request] = []

    async def hang(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        await asyncio.sleep(3600)
        raise AssertionError("never answered")

    payments = await stripe_answering(hang)
    began = time.monotonic()
    try:
        with pytest.raises(BackendUnreachable) as raised:
            deadline = utcnow() + timedelta(seconds=0.3)
            await asyncio.wait_for(payments.create_customer(uuid4(), "Ajax", deadline=deadline), 5)
    finally:
        await payments.close()
    waited = time.monotonic() - began
    assert raised.value.message == f"stripe create customer could not reach the backend: {PASSED}"
    assert 0.25 <= waited < 1.0, waited
    # The attempt, cut at the deadline, and no retry after it; none at all on
    # a runner slow enough that the call's own work takes the whole of it.
    assert len(sent) <= 1


CHECKOUT = {"/v1/prices": "find price", "/v1/checkout/sessions": "create checkout session"}
"""The calls of a checkout, in turn, and the operation each is refused as."""


async def test_the_two_calls_of_a_checkout_share_the_deadline() -> None:
    """A checkout finds its price, then makes its session: each answers in a
    fifth of a second, well inside the timeout, and each fits in the three
    tenths the request has left; together they do not. So the deadline ends
    one of them, where a budget per call would end none: the session on a
    quick runner, the price on one slow enough that the call's own work took
    a tenth. The test holds whichever it is."""
    sent: list[str] = []
    answered: list[str] = []

    async def slow(request: httpx.Request) -> httpx.Response:
        sent.append(request.url.path)
        await asyncio.sleep(0.2)
        answered.append(request.url.path)
        if request.url.path == "/v1/prices":
            price = {"id": "price_1", "object": "price", "lookup_key": "team_monthly"}
            return httpx.Response(200, json={"object": "list", "data": [price], "has_more": False})
        return httpx.Response(200, json={"id": "cs_1", "object": "checkout.session", "url": "u"})

    payments = await stripe_answering(slow)
    try:
        with pytest.raises(BackendUnreachable) as raised:
            await payments.create_checkout(
                customer_id="cus_1",
                org_id=uuid4(),
                lookup_key="team_monthly",
                quantity=1,
                success_url="https://portal.example/ok",
                cancel_url="https://portal.example/no",
                deadline=utcnow() + timedelta(seconds=0.3),
            )
    finally:
        await payments.close()
    refusals = [f"stripe {op} could not reach the backend: {PASSED}" for op in CHECKOUT.values()]
    assert raised.value.message in refusals
    caught = refusals.index(raised.value.message)
    calls = list(CHECKOUT)
    # The calls before the one the deadline caught answered in full; it was
    # cut, or never started; none came after it.
    assert answered[:caught] == calls[:caught], answered
    assert sent in (calls[:caught], calls[: caught + 1]), sent


def test_a_subscription_is_read_from_the_item_where_the_period_now_sits() -> None:
    org = uuid4()
    read = subscription_of(
        {
            "id": "sub_1",
            "customer": "cus_1",
            "status": "active",
            "cancel_at_period_end": True,
            "metadata": {ORG_METADATA_KEY: str(org)},
            "items": {
                "data": [
                    {
                        "id": "si_1",
                        "quantity": 11,
                        "current_period_end": 1_900_000_000,
                        "price": {"lookup_key": "acme.max.monthly"},
                    }
                ]
            },
        }
    )
    assert (read.price_lookup_key, read.quantity, read.cancel_at_period_end) == (
        "acme.max.monthly",
        11,
        True,
    )
    assert read.current_period_end is not None and read.org_id == org
    assert read.item_id == "si_1"


class _Sessions:
    def __init__(self) -> None:
        self.params: list[dict[str, Any]] = []

    async def create_async(self, params: dict[str, Any]) -> object:
        self.params.append(params)
        return answered(
            stripe.billing_portal.Session,
            {
                "id": f"bps_{len(self.params)}",
                "object": "billing_portal.session",
                "customer": params["customer"],
                "url": "https://billing.stripe.com/p/session",
            },
        )


class _Configurations:
    def __init__(self, configurations: list[dict[str, Any]]) -> None:
        self._configurations = configurations
        self.params: list[dict[str, Any]] = []

    async def list_async(self, params: dict[str, Any]) -> object:
        self.params.append(params)
        return answered(
            stripe.ListObject,
            {
                "object": "list",
                "url": "/v1/billing_portal/configurations",
                "has_more": False,
                "data": self._configurations,
            },
        )


def configuration(
    configuration_id: str, metadata: dict[str, str] | None, *, is_default: bool = False
) -> dict[str, Any]:
    """A portal configuration as the processor answers one. Its metadata is
    nullable: null, an empty object, or keys."""
    return {
        "id": configuration_id,
        "object": "billing_portal.configuration",
        "active": True,
        "is_default": is_default,
        "livemode": False,
        "metadata": metadata,
    }


class _PortalOf:
    """The SDK client's `v1` as far as a portal session reads it: the
    account's active configurations, and the sessions made."""

    def __init__(self, configurations: list[dict[str, Any]] | None = None) -> None:
        self.sessions = _Sessions()
        self.configurations = _Configurations(configurations or [])
        self.billing_portal = type(
            "Portal", (), {"sessions": self.sessions, "configurations": self.configurations}
        )()
        self.v1 = self


MANAGED_PORTAL = {
    "acme_managed": "true",
    "acme_desired_key": "portal",
    "acme_desired_hash": "0f3c",
}
"""The metadata the bootstrap writes on the configuration it manages."""


async def test_a_portal_session_names_the_configuration_the_bootstrap_manages() -> None:
    """The list answers SDK objects, whose metadata is not a dict either.
    Among the account's default, one with empty metadata, and the
    bootstrap's, every session names the bootstrap's, and the list is read
    once per process."""
    payments = await _checked(set())
    portal = _PortalOf(
        [
            configuration("bpc_default", None, is_default=True),
            configuration("bpc_bare", {}),
            configuration("bpc_managed", MANAGED_PORTAL),
        ]
    )
    payments._client = portal  # type: ignore[assignment]
    await payments.create_portal_session("cus_1", "http://portal.test/settings/billing")
    await payments.create_portal_session(
        "cus_1", "http://portal.test/settings/billing", update_payment_method=True
    )
    assert [p["configuration"] for p in portal.sessions.params] == ["bpc_managed"] * 2
    assert portal.configurations.params == [{"active": True, "limit": 100}]


async def test_a_portal_session_before_the_bootstrap_runs_opens_the_accounts_default() -> None:
    """No configuration carries the bootstrap's key: the session names none,
    and the processor opens the account's default."""
    payments = await _checked(set())
    portal = _PortalOf(
        [configuration("bpc_default", None, is_default=True), configuration("bpc_bare", {})]
    )
    payments._client = portal  # type: ignore[assignment]
    url = await payments.create_portal_session("cus_1", "http://portal.test/settings/billing")
    assert url == "https://billing.stripe.com/p/session"
    assert "configuration" not in portal.sessions.params[0]


async def test_a_portal_session_for_a_failed_payment_opens_the_payment_method_flow() -> None:
    """The processor's documented deep link: `flow_data` of type
    `payment_method_update`, redirecting back to the page that asked."""
    payments = await _checked(set())
    portal = _PortalOf()
    payments._client = portal  # type: ignore[assignment]
    await payments.create_portal_session("cus_1", "http://portal.test/settings/billing")
    await payments.create_portal_session(
        "cus_1", "http://portal.test/settings/billing", update_payment_method=True
    )
    home, fix = portal.sessions.params
    assert "flow_data" not in home
    assert fix["flow_data"] == {
        "type": "payment_method_update",
        "after_completion": {
            "type": "redirect",
            "redirect": {"return_url": "http://portal.test/settings/billing"},
        },
    }
    assert fix["customer"] == "cus_1"


class _Ends:
    """The SDK client's `v1` as far as ending an account reads it: one
    subscription in a status, and the calls made."""

    def __init__(self, status: str | None, missing: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self._status = status
        self._missing = missing
        ends = self

        class Subscriptions:
            async def retrieve_async(self, subscription_id: str) -> object:
                if ends._status is None:
                    raise stripe.InvalidRequestError("gone", None, code="resource_missing")
                return answered(
                    stripe.Subscription,
                    {
                        "id": subscription_id,
                        "object": "subscription",
                        "customer": "cus_1",
                        "status": ends._status,
                    },
                )

            async def cancel_async(self, subscription_id: str) -> object:
                ends.calls.append(("cancel", subscription_id))
                return answered(
                    stripe.Subscription,
                    {"id": subscription_id, "object": "subscription", "status": "canceled"},
                )

        class Customers:
            async def delete_async(self, customer_id: str) -> object:
                ends.calls.append(("delete", customer_id))
                if ends._missing:
                    raise stripe.InvalidRequestError("gone", None, code="resource_missing")
                return answered(
                    stripe.Customer, {"id": customer_id, "object": "customer", "deleted": True}
                )

        self.subscriptions = Subscriptions()
        self.customers = Customers()
        self.v1 = self


@pytest.mark.parametrize(
    ("status", "calls"),
    [
        ("active", [("cancel", "sub_1"), ("delete", "cus_1")]),
        ("past_due", [("cancel", "sub_1"), ("delete", "cus_1")]),
        ("canceled", [("delete", "cus_1")]),
        (None, [("delete", "cus_1")]),
    ],
)
async def test_an_accounts_subscription_ends_now_and_its_customer_goes(
    status: str | None, calls: list[tuple[str, str]]
) -> None:
    """A live subscription is canceled at once, one that ended or that the
    processor no longer knows is left alone, and the customer is deleted."""
    payments = await _checked(set())
    ends = _Ends(status)
    payments._client = ends  # type: ignore[assignment]
    await payments.cancel_subscription("sub_1")
    await payments.delete_customer("cus_1")
    assert ends.calls == calls


async def test_a_customer_the_processor_no_longer_knows_is_deleted_already() -> None:
    payments = await _checked(set())
    payments._client = _Ends("canceled", missing=True)  # type: ignore[assignment]
    await payments.delete_customer("cus_1")


async def test_a_runtime_key_refused_the_end_of_an_account_is_unavailable_not_refused() -> None:
    """A key without the permission is the process's to fix: the work waits."""
    payments = await _checked(set())

    class Refusing(_Ends):
        def __init__(self) -> None:
            super().__init__("active")

            class Customers:
                async def delete_async(self, customer_id: str) -> object:
                    raise stripe.PermissionError("no", None, code=None)

            self.customers = Customers()

    payments._client = Refusing()  # type: ignore[assignment]
    with pytest.raises(ProviderUnavailable) as raised:
        await payments.delete_customer("cus_1")
    assert "delete customer" in str(raised.value)


async def test_a_runtime_key_refused_the_read_before_a_cancel_is_unavailable() -> None:
    """The read on the way to the cancel is the end's too: a key without the
    permission to read subscriptions parks the work, never fails it."""
    payments = await _checked(set())

    class Unreadable(_Ends):
        def __init__(self) -> None:
            super().__init__("active")
            ends = self

            class Subscriptions:
                async def retrieve_async(self, subscription_id: str) -> object:
                    raise stripe.PermissionError("no", None, code=None)

                async def cancel_async(self, subscription_id: str) -> object:
                    ends.calls.append(("cancel", subscription_id))
                    return object()

            self.subscriptions = Subscriptions()

    unreadable = Unreadable()
    payments._client = unreadable  # type: ignore[assignment]
    with pytest.raises(ProviderUnavailable) as raised:
        await payments.cancel_subscription("sub_1")
    assert "read subscription" in str(raised.value)
    assert unreadable.calls == []


async def test_a_cancel_the_processor_refuses_as_a_request_is_refused() -> None:
    """A refusal of the request itself stays a refusal: the caller decides."""
    payments = await _checked(set())

    class Invalid(_Ends):
        def __init__(self) -> None:
            super().__init__("active")

            class Subscriptions:
                async def retrieve_async(self, subscription_id: str) -> object:
                    raise stripe.InvalidRequestError("bad", None, code="parameter_invalid")

            self.subscriptions = Subscriptions()

    payments._client = Invalid()  # type: ignore[assignment]
    with pytest.raises(PaymentsRefused) as raised:
        await payments.cancel_subscription("sub_1")
    assert "parameter_invalid" in str(raised.value)


KEY_TEXT = "Invalid API Key provided: rk_test_****abcd"
"""What the processor says of a key it refuses: part of the key itself."""


@pytest.mark.parametrize(
    ("error", "raised", "status", "code"),
    [
        # The process's own key: revoked, or without the permission.
        (
            stripe.AuthenticationError(KEY_TEXT, http_status=401),
            PaymentsKeyRefused,
            503,
            "payments_key_refused",
        ),
        (
            stripe.PermissionError("no", http_status=403, code="secret_key_required"),
            PaymentsKeyRefused,
            503,
            "payments_key_refused",
        ),
        # Not right now.
        (
            stripe.RateLimitError("slow down", http_status=429, code="rate_limit"),
            ProviderUnavailable,
            503,
            "unavailable",
        ),
        (stripe.APIConnectionError("reset"), BackendUnreachable, 503, "unavailable"),
        # The request itself.
        (
            stripe.InvalidRequestError("bad", "items", code="parameter_invalid", http_status=400),
            PaymentsRefused,
            502,
            "payments_refused",
        ),
        (
            stripe.InvalidRequestError("gone", None, code="resource_missing", http_status=404),
            PaymentsRefused,
            502,
            "payments_refused",
        ),
        (
            stripe.CardError("declined", None, code="card_declined", http_status=402),
            PaymentsRefused,
            502,
            "payments_refused",
        ),
        (
            stripe.IdempotencyError("reused", http_status=400, code="idempotency_key_in_use"),
            PaymentsRefused,
            502,
            "payments_refused",
        ),
        # The processor's own failure.
        (stripe.APIError("boom", http_status=500), BackendFailed, 500, "backend_failed"),
    ],
)
async def test_a_failure_is_translated_by_whose_problem_it_is(
    error: stripe.StripeError, raised: type[Exception], status: int, code: str
) -> None:
    with pytest.raises(raised) as caught:
        async with translated("update subscription quantity"):
            raise error
    assert type(caught.value) is raised
    assert (caught.value.http_status, caught.value.code) == (status, code)  # type: ignore[attr-defined]
    assert "update subscription quantity" in str(caught.value)
    assert "rk_test" not in str(caught.value)


class _Seats:
    """The SDK client's `v1` as far as a seat count reads it: one
    subscription with one item, every call recorded, and an update that
    fails as it is told."""

    def __init__(self, update_error: Exception | None = None) -> None:
        seats = self
        self.calls: list[tuple[Any, ...]] = []
        self._raw: dict[str, Any] = {
            "id": "sub_1",
            "customer": "cus_1",
            "object": "subscription",
            "status": "active",
            "items": {
                "object": "list",
                "data": [{"id": "si_1", "object": "subscription_item", "quantity": 1}],
            },
        }

        def answer() -> object:
            return answered(stripe.Subscription, seats._raw)

        class Subscriptions:
            async def retrieve_async(self, subscription_id: str) -> object:
                seats.calls.append(("retrieve", subscription_id))
                return answer()

            async def update_async(self, *args: Any) -> object:
                seats.calls.append(("update", *args))
                if seats._error is not None:
                    raise seats._error
                [item] = args[1]["items"]
                seats._raw["items"]["data"][0]["quantity"] = item["quantity"]
                return answer()

        self._error = update_error
        self.subscriptions = Subscriptions()
        self.v1 = self


async def test_a_seat_count_is_one_read_and_one_update_of_the_item_read() -> None:
    payments = await _checked(set())
    payments._client = seats = _Seats()  # type: ignore[assignment]
    current = await payments.read_subscription("sub_1")
    assert current is not None and current.item_id == "si_1"
    updated = await payments.set_quantity(current, 3, "acme-seats-x-3")
    assert updated.quantity == 3
    assert seats.calls == [
        ("retrieve", "sub_1"),
        (
            "update",
            "sub_1",
            {"items": [{"id": "si_1", "quantity": 3}], "proration_behavior": "none"},
            {"idempotency_key": "acme-seats-x-3"},
        ),
    ]
    itemless = current.model_copy(update={"item_id": None})
    with pytest.raises(PaymentsRefused):
        await payments.set_quantity(itemless, 3, "acme-seats-x-3")
    assert len(seats.calls) == 2, "a subscription with no item is not sent"


@pytest.mark.parametrize(
    ("error", "raised"),
    [
        (stripe.PermissionError("no", http_status=403), PaymentsKeyRefused),
        (stripe.AuthenticationError(KEY_TEXT, http_status=401), PaymentsKeyRefused),
        (stripe.InvalidRequestError("bad", "quantity", code="parameter_invalid"), PaymentsRefused),
    ],
)
async def test_a_seat_count_tells_a_refused_key_from_a_refused_request(
    error: Exception, raised: type[Exception]
) -> None:
    """Every call translates the same way, not only an account's end."""
    payments = await _checked(set())
    payments._client = _Seats(error)  # type: ignore[assignment]
    current = await payments.read_subscription("sub_1")
    assert current is not None
    with pytest.raises(raised) as caught:
        await payments.set_quantity(current, 3, "acme-seats-x-3")
    assert type(caught.value) is raised
