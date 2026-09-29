"""The billing routes and the processor's webhook over the in-process app and
the payment processor's twin: the plan an org reads, the typed refusal a
lever answers with, a checkout, a signed delivery queued and applied once,
a cancellation that holds the plan to its period's end, and the status
each route answers when the processor fails."""

import json
from datetime import timedelta
from typing import cast
from uuid import UUID

import httpx
import pytest
import stripe
from api_support import add_member, on_plan, seed_request, sign_in, sign_in_as

from acme.infra.queues import Queues
from acme.integrations.payments import ProviderDelivery
from acme.integrations.payments.deliveries import sign
from acme.integrations.payments.stripe import translated
from acme.integrations.payments.twin import PaymentsTwinImpl
from acme.om.base import EMPTY_UUID
from acme.om.billing.types.plan import Plan
from acme.om.opcontext import Role
from acme.services.api.container import AppContainer

PORTAL = "http://localhost:5173/settings/billing"


def twin_of(container: AppContainer) -> PaymentsTwinImpl:
    return cast(PaymentsTwinImpl, container.payments)


async def org_id_of(client: httpx.AsyncClient, headers: dict[str, str]) -> UUID:
    return UUID((await client.get("/v1/orgs/current", headers=headers)).json()["id"])


async def drain(container: AppContainer) -> list[bool]:
    """What the worker does with every delivery the route queued: find the
    org, mint its service context, apply, delete. Answers each apply."""
    queues = container.infra.get_queues()
    applied: list[bool] = []
    for message in await queues.receive(Queues.WEBHOOKS, 10, timedelta(0), timedelta(30)):
        body = json.loads(message.body)
        delivery = ProviderDelivery.model_validate(body["delivery"])
        assert body["idempotency_key"] == str(delivery.idempotency_key)
        billing = container.managers.billing
        org_id = await billing.org_of_delivery(seed_request(), delivery)
        assert org_id is not None
        ctx = await container.managers.tenancy.service_context(seed_request(), org_id, EMPTY_UUID)
        applied.append(await billing.apply_delivery(ctx, delivery))
        await queues.delete(Queues.WEBHOOKS, message.receipt)
    return applied


async def pay(client: httpx.AsyncClient, container: AppContainer, headers: dict[str, str]) -> None:
    """The owner starts a checkout for Pro, pays, and the delivery arrives."""
    started = await client.post(
        "/v1/billing/checkout", headers=headers, json={"plan": "pro", "return_url": PORTAL}
    )
    assert started.status_code == 201, started.text
    payload, signature = twin_of(container).complete_checkout(started.json()["url"])
    delivered = await client.post(
        "/webhooks/stripe", content=payload, headers={"Stripe-Signature": signature}
    )
    assert delivered.status_code == 200 and delivered.json() == {"received": True}
    assert await drain(container) == [True]


@pytest.fixture
async def free(client: httpx.AsyncClient, container: AppContainer) -> dict[str, str]:
    return await sign_in(client, container, plan=None)


async def test_a_new_org_reads_free_and_the_plans_on_offer(
    client: httpx.AsyncClient, free: dict[str, str]
) -> None:
    billing = await client.get("/v1/billing", headers=free)
    assert billing.status_code == 200, billing.text
    body = billing.json()
    assert (body["plan"], body["paid_plan"], body["seats"], body["active_tasks"]) == (
        "free",
        None,
        1,
        0,
    )
    assert body["limits"] == {
        "members": 1,
        "api_keys": False,
        "active_tasks": 10,
        "storage_bytes": 1024**3,
    }
    assert body["can_manage"] is True and body["monthly_cents"] == 0
    offers = {offer["plan"]: offer for offer in body["plans"]}
    assert list(offers) == ["free", "pro", "team", "max"]
    assert (offers["max"]["flat_cents"], offers["max"]["included_seats"]) == (3000, 10)
    assert offers["max"]["per_seat_cents"] == 300


async def test_the_eleventh_active_task_is_a_typed_refusal_naming_the_plan_that_lifts_it(
    client: httpx.AsyncClient, container: AppContainer, free: dict[str, str]
) -> None:
    for n in range(10):
        made = await client.post("/v1/tasks", headers=free, json={"title": f"task {n}"})
        assert made.status_code == 201, made.text
    refused = await client.post("/v1/tasks", headers=free, json={"title": "one too many"})
    assert refused.status_code == 402, refused.text
    error = refused.json()["error"]
    assert error["code"] == "plan_limit_reached"
    assert error["message"] == "the free plan allows 10 active tasks"
    assert error["plan_limit"] == {
        "lever": "active_tasks",
        "plan": "free",
        "limit": 10,
        "suggested_plan": "pro",
    }
    assert error["request_id"] == refused.headers["x-request-id"]
    # Paid for, the same task lands.
    await pay(client, container, free)
    landed = await client.post("/v1/tasks", headers=free, json={"title": "one too many"})
    assert landed.status_code == 201, landed.text
    assert (await client.get("/v1/billing", headers=free)).json()["active_tasks"] == 11


async def test_a_refused_create_under_a_key_lands_on_its_retry_after_an_upgrade(
    client: httpx.AsyncClient, container: AppContainer, free: dict[str, str]
) -> None:
    """A plan's bound is an answer about now: the marker is released, not
    kept, so the retry of the same create lands once the org has room."""
    for n in range(10):
        await client.post("/v1/tasks", headers=free, json={"title": f"task {n}"})
    keyed = {**free, "Idempotency-Key": "the-eleventh"}
    first = await client.post("/v1/tasks", headers=keyed, json={"title": "eleventh"})
    assert first.status_code == 402
    await on_plan(container, await org_id_of(client, free), Plan.PRO)
    retried = await client.post("/v1/tasks", headers=keyed, json={"title": "eleventh"})
    assert retried.status_code == 201, retried.text
    assert "Idempotent-Replayed" not in retried.headers


async def test_the_first_api_key_on_free_is_refused(
    client: httpx.AsyncClient, free: dict[str, str]
) -> None:
    refused = await client.post("/v1/api-keys", headers=free, json={"name": "ci", "role": "member"})
    assert refused.status_code == 402, refused.text
    assert refused.json()["error"]["plan_limit"]["lever"] == "api_keys"
    assert refused.json()["error"]["plan_limit"]["suggested_plan"] == "pro"


async def test_a_checkout_comes_back_to_the_portal_and_nowhere_else(
    client: httpx.AsyncClient, container: AppContainer, free: dict[str, str]
) -> None:
    started = await client.post(
        "/v1/billing/checkout", headers=free, json={"plan": "max", "return_url": PORTAL}
    )
    assert started.status_code == 201, started.text
    assert started.json()["url"].startswith("https://checkout.twin.invalid/")
    session = next(iter(twin_of(container).sessions.values()))
    assert session["success_url"] == f"{PORTAL}?checkout=done"
    assert session["cancel_url"] == f"{PORTAL}?checkout=cancelled"
    assert session["quantity"] == 1  # the one member
    for elsewhere in ("https://evil.example/billing", "javascript:alert(1)", "/settings"):
        refused = await client.post(
            "/v1/billing/checkout", headers=free, json={"plan": "pro", "return_url": elsewhere}
        )
        assert refused.status_code == 422, refused.text
    free_plan = await client.post(
        "/v1/billing/checkout", headers=free, json={"plan": "free", "return_url": PORTAL}
    )
    assert free_plan.status_code == 422


async def test_a_member_reads_the_plan_and_is_refused_every_change(
    client: httpx.AsyncClient, container: AppContainer, free: dict[str, str]
) -> None:
    org_id = await org_id_of(client, free)
    await add_member(container, org_id, "bob@example.test", Role.MEMBER)
    bob = await sign_in_as(client, "bob@example.test", org_id)
    read = await client.get("/v1/billing", headers=bob)
    assert read.status_code == 200 and read.json()["can_manage"] is False
    assert read.json()["seats"] == 2
    for path, body in (
        ("/v1/billing/checkout", {"plan": "pro", "return_url": PORTAL}),
        ("/v1/billing/portal", {"return_url": PORTAL}),
        ("/v1/billing/cancel", None),
    ):
        refused = await client.post(path, headers=bob, json=body)
        assert refused.status_code == 403, (path, refused.text)


async def test_a_delivery_that_does_not_check_out_is_refused_and_queues_nothing(
    client: httpx.AsyncClient, container: AppContainer, free: dict[str, str]
) -> None:
    payload = b'{"id": "evt_forged", "type": "invoice.paid", "created": 1}'
    for headers in ({}, {"Stripe-Signature": sign(payload, "whsec_not_ours")}):
        refused = await client.post("/webhooks/stripe", content=payload, headers=headers)
        assert refused.status_code == 400, refused.text
        assert refused.json()["error"]["code"] == "webhook_signature_invalid"
    depth = await container.infra.get_queues().depth(Queues.WEBHOOKS)
    assert (depth.visible, depth.in_flight) == (0, 0)


async def test_the_same_delivery_twice_is_queued_twice_and_applied_once(
    client: httpx.AsyncClient, container: AppContainer, free: dict[str, str]
) -> None:
    await pay(client, container, free)
    twin = twin_of(container)
    subscription = next(iter(twin.subscriptions))
    twin.move(subscription, cancel_at_period_end=True)
    payload, signature = twin.subscription_event("customer.subscription.updated", subscription)
    for _ in range(2):
        delivered = await client.post(
            "/webhooks/stripe", content=payload, headers={"Stripe-Signature": signature}
        )
        assert delivered.status_code == 200
    assert await drain(container) == [True, False]
    billing = (await client.get("/v1/billing", headers=free)).json()
    assert billing["cancel_at_period_end"] is True and billing["ends_at"] is not None


async def test_cancel_holds_the_plan_to_the_period_end_and_resume_takes_it_back(
    client: httpx.AsyncClient, container: AppContainer, free: dict[str, str]
) -> None:
    nothing = await client.post("/v1/billing/cancel", headers=free)
    assert nothing.status_code == 404
    await pay(client, container, free)
    cancelled = await client.post("/v1/billing/cancel", headers=free)
    assert cancelled.status_code == 200, cancelled.text
    body = cancelled.json()
    assert (body["plan"], body["plan_after"], body["cancel_at_period_end"]) == ("pro", "free", True)
    assert body["ends_at"] == body["current_period_end"] and body["monthly_cents"] == 500
    resumed = (await client.post("/v1/billing/resume", headers=free)).json()
    assert (resumed["plan"], resumed["ends_at"], resumed["plan_after"]) == ("pro", None, None)
    portal = await client.post("/v1/billing/portal", headers=free, json={"return_url": PORTAL})
    assert portal.status_code == 200 and portal.json()["url"].startswith("https://billing.twin")


async def test_a_failed_payment_is_read_and_the_portal_opens_on_a_new_payment_method(
    client: httpx.AsyncClient, container: AppContainer, free: dict[str, str]
) -> None:
    await pay(client, container, free)
    assert (await client.get("/v1/billing", headers=free)).json()["payment_failed"] is False
    twin = twin_of(container)
    subscription = next(iter(twin.subscriptions))
    twin.move(subscription, status="past_due")
    payload, signature = twin.invoice_event("invoice.payment_failed", subscription)
    delivered = await client.post(
        "/webhooks/stripe", content=payload, headers={"Stripe-Signature": signature}
    )
    assert delivered.status_code == 200
    assert await drain(container) == [True]
    failed = (await client.get("/v1/billing", headers=free)).json()
    assert (failed["payment_failed"], failed["plan"], failed["status"]) == (True, "pro", "past_due")
    fix = await client.post(
        "/v1/billing/portal",
        headers=free,
        json={"return_url": PORTAL, "flow": "payment_method_update"},
    )
    assert fix.status_code == 200, fix.text
    assert fix.json()["url"].endswith("/payment-method")
    unknown = await client.post(
        "/v1/billing/portal", headers=free, json={"return_url": PORTAL, "flow": "cancel"}
    )
    assert unknown.status_code == 422
    twin.move(subscription, status="active")
    payload, signature = twin.invoice_event("invoice.paid", subscription)
    await client.post("/webhooks/stripe", content=payload, headers={"Stripe-Signature": signature})
    assert await drain(container) == [True]
    assert (await client.get("/v1/billing", headers=free)).json()["payment_failed"] is False


async def test_every_other_org_is_untouched_by_a_delivery(
    client: httpx.AsyncClient, container: AppContainer, free: dict[str, str]
) -> None:
    _, other = await container.managers.tenancy.bootstrap(
        seed_request(), "Other", "other", "otto@example.test", "Otto"
    )
    await pay(client, container, free)
    otto = await sign_in_as(client, "otto@example.test", other.id)
    assert (await client.get("/v1/billing", headers=otto)).json()["plan"] == "free"


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        # The runtime key refused: unavailable until a person fixes the key.
        (stripe.PermissionError("no", http_status=403), 503, "payments_key_refused"),
        (stripe.AuthenticationError("revoked", http_status=401), 503, "payments_key_refused"),
        # A throttle, and a processor out of reach: not right now.
        (stripe.RateLimitError("slow down", http_status=429), 503, "unavailable"),
        (stripe.APIConnectionError("reset"), 503, "unavailable"),
        # The request itself.
        (
            stripe.InvalidRequestError("bad", None, code="parameter_invalid"),
            502,
            "payments_refused",
        ),
        (stripe.CardError("declined", None, code="card_declined"), 502, "payments_refused"),
        # The processor's own failure.
        (stripe.APIError("boom", http_status=500), 500, "backend_failed"),
    ],
)
async def test_each_billing_route_answers_the_status_of_whose_problem_it_is(
    client: httpx.AsyncClient,
    container: AppContainer,
    free: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    error: stripe.StripeError,
    status: int,
    code: str,
) -> None:
    """The processor's failure, through the real client's translation, as
    the checkout, the portal, a cancel, and a resume answer it. The billing
    read asks the processor nothing and answers from the mirror."""
    twin = twin_of(container)

    async def failing(*args: object, **kwargs: object) -> object:
        async with translated("the call"):
            raise error

    def answered(response: httpx.Response) -> None:
        assert response.status_code == status, response.text
        assert response.json()["error"]["code"] == code
        assert response.json()["error"]["message"] == "internal error"

    monkeypatch.setattr(twin, "create_checkout", failing)
    answered(
        await client.post(
            "/v1/billing/checkout", headers=free, json={"plan": "pro", "return_url": PORTAL}
        )
    )
    monkeypatch.undo()
    await pay(client, container, free)
    monkeypatch.setattr(twin, "create_portal_session", failing)
    monkeypatch.setattr(twin, "set_cancel_at_period_end", failing)
    answered(await client.post("/v1/billing/portal", headers=free, json={"return_url": PORTAL}))
    answered(await client.post("/v1/billing/cancel", headers=free))
    answered(await client.post("/v1/billing/resume", headers=free))
    read = await client.get("/v1/billing", headers=free)
    assert read.status_code == 200 and read.json()["plan"] == "pro"
