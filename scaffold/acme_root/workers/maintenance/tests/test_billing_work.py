"""The worker's billing work: the processor's deliveries consumed from the
inbound queue, each applied once; the seat count of a per-seat subscription
held to the members; and the rule that no role asks, through the queue, for
work it could not do itself."""

import asyncio
import json
from datetime import timedelta
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from worker_support import build_container, request, sign_in

from acme.infra.exceptions import BackendFailed, BackendUnreachable
from acme.infra.queues import QueueMessage, Queues
from acme.integrations.exceptions import PaymentsKeyRefused, PaymentsRefused, ProviderUnavailable
from acme.integrations.payments.deliveries import delivery_of
from acme.integrations.payments.twin import PaymentsTwinImpl
from acme.om.base import EMPTY_UUID, new_id, utcnow
from acme.om.billing.types.plan import Plan
from acme.om.opcontext import OpContext, Role
from acme.om.tenancy.rules import ROLE_PERMISSIONS
from acme.om.work.types.handler import WorkParked, WorkRefused
from acme.om.work.types.work_item import WORK_ENQUEUE_PERMISSIONS, WorkItem, WorkKind
from acme.workers.maintenance.container import WorkerContainer
from acme.workers.maintenance.deliveries import DeliveryConsumer, DeliveryOptions
from acme.workers.maintenance.handler import SyncSeatsHandlerImpl
from acme.workers.maintenance.main import build_loop

PORTAL = "http://portal.test/settings/billing"


def twin_of(container: WorkerContainer) -> PaymentsTwinImpl:
    return cast(PaymentsTwinImpl, container.payments)


def consumer_of(container: WorkerContainer) -> DeliveryConsumer:
    return DeliveryConsumer(
        queues=container.infra.get_queues(),
        billing=container.managers.billing,
        tenancy=container.managers.tenancy,
        options=DeliveryOptions(worker_id="maintenance-test", wait=timedelta(0)),
    )


async def queued(container: WorkerContainer, payload: bytes) -> QueueMessage:
    """The message the webhook route would queue for this delivery."""
    delivery = delivery_of(json.loads(payload))
    body = {
        "idempotency_key": str(delivery.idempotency_key),
        "provider": "stripe",
        "delivery": delivery.model_dump(mode="json"),
    }
    queues = container.infra.get_queues()
    await queues.send(Queues.WEBHOOKS, json.dumps(body).encode())
    [message] = await queues.receive(Queues.WEBHOOKS, 1, timedelta(0), timedelta(seconds=30))
    return message


async def checkout(container: WorkerContainer, ctx: OpContext, plan: Plan, seats: int) -> bytes:
    start = await container.managers.billing.start_checkout(
        ctx, plan, seats, "Ajax", PORTAL, PORTAL
    )
    payload, _ = twin_of(container).complete_checkout(start.url)
    return payload


async def test_a_delivery_is_applied_once_and_its_copy_is_a_duplicate(tmp_path: Path) -> None:
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    payload = await checkout(container, ctx, Plan.PRO, 1)
    consumer = consumer_of(container)
    assert await consumer.handle(await queued(container, payload)) == "applied"
    assert (await container.managers.billing.get_billing(ctx)).plan is Plan.PRO
    assert await consumer.handle(await queued(container, payload)) == "duplicate"
    depth = await container.infra.get_queues().depth(Queues.WEBHOOKS)
    assert (depth.visible, depth.in_flight) == (0, 0)


async def test_a_delivery_that_can_never_apply_is_dropped_and_one_that_failed_comes_back(
    tmp_path: Path,
) -> None:
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    consumer = consumer_of(container)
    queues = container.infra.get_queues()
    await queues.send(Queues.WEBHOOKS, b"not a delivery")
    [malformed] = await queues.receive(Queues.WEBHOOKS, 1, timedelta(0), timedelta(seconds=30))
    assert await consumer.handle(malformed) == "malformed"
    twin = twin_of(container)
    orphan, _ = twin.delivery(
        "checkout.session.completed", {"client_reference_id": str(uuid4()), "customer": "cus_x"}
    )
    assert await consumer.handle(await queued(container, orphan)) == "unowned"
    # A processor that cannot be read is a failure: the message stays, to be
    # received again once its visibility passes.
    payload = await checkout(container, ctx, Plan.PRO, 1)
    twin.subscriptions.clear()

    async def down(_: str) -> None:
        raise ConnectionError("the processor is down")

    twin.read_subscription = down  # type: ignore[method-assign]
    message = await queued(container, payload)
    assert await consumer.handle(message) == "failed"
    depth = await queues.depth(Queues.WEBHOOKS)
    assert (depth.visible, depth.in_flight) == (0, 1)


async def test_the_consumer_runs_until_it_is_stopped(tmp_path: Path) -> None:
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    payload = await checkout(container, ctx, Plan.TEAM, 1)
    delivery = delivery_of(json.loads(payload))
    await container.infra.get_queues().send(
        Queues.WEBHOOKS, json.dumps({"delivery": delivery.model_dump(mode="json")}).encode()
    )
    consumer = consumer_of(container)
    running = asyncio.create_task(consumer.run())
    for _ in range(100):
        if (await container.managers.billing.get_billing(ctx)).plan is Plan.TEAM:
            break
        await asyncio.sleep(0.01)
    consumer.stop()
    await asyncio.wait_for(running, timeout=5)
    assert (await container.managers.billing.get_billing(ctx)).plan is Plan.TEAM


async def seats_to_sync(tmp_path: Path) -> tuple[WorkerContainer, OpContext, OpContext, WorkItem]:
    """Ajax pays for one seat on Max and has just added Bob: the owner's
    context, the service context the item runs under, and the item."""
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    consumer = consumer_of(container)
    bought = await checkout(container, ctx, Plan.MAX, 1)
    assert await consumer.handle(await queued(container, bought)) == "applied"
    await container.managers.tenancy.add_member(
        request(), "ajax", "bob@example.test", "Bob", Role.MEMBER
    )
    service = await container.managers.tenancy.service_context(request(), ctx.org_id, EMPTY_UUID)
    now = utcnow()
    item = WorkItem(
        id=new_id(),
        created_at=now,
        updated_at=now,
        created_by=ctx.user_id,
        updated_by=ctx.user_id,
        kind=WorkKind.SYNC_SEATS,
        target_id=ctx.org_id,
        idempotency_key=new_id(),
        request_id=ctx.request_id,
        available_at=now,
    )
    return container, ctx, service, item


async def test_the_seat_count_follows_the_members_when_the_item_runs(tmp_path: Path) -> None:
    container, ctx, service, item = await seats_to_sync(tmp_path)
    handler = SyncSeatsHandlerImpl(container.managers.tenancy, container.managers.billing)
    await handler.handle(service, item)
    await handler.handle(service, item)  # at least once: the second run changes nothing
    twin = twin_of(container)
    assert [quantity for _, quantity in twin.quantity_changes] == [2]
    billing = await container.managers.billing.get_billing(ctx)
    assert billing.account is not None and billing.account.quantity == 2


@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        # What the Stripe client raises for a refusal of its own key (401,
        # 403), a throttle, and an unreachable processor: the item waits.
        (PaymentsKeyRefused("update subscription quantity", "PermissionError"), WorkParked),
        (PaymentsKeyRefused("update subscription quantity", "AuthenticationError"), WorkParked),
        (ProviderUnavailable("the payment processor throttled update"), WorkParked),
        (BackendUnreachable("stripe", "update subscription quantity", "ConnectError"), WorkParked),
        # A refusal of the request itself: the same call gets the same answer.
        (PaymentsRefused("update subscription quantity", "parameter_invalid"), WorkRefused),
        # A failure that may come out differently is the queue's to retry.
        (BackendFailed("stripe", "update subscription quantity", "api_error"), BackendFailed),
    ],
)
async def test_a_seat_count_the_processor_refuses_fails_and_one_that_may_pass_parks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    outcome: type[Exception],
) -> None:
    container, ctx, service, item = await seats_to_sync(tmp_path)

    async def answer(subscription: object, quantity: int, idempotency_key: str) -> object:
        raise error

    monkeypatch.setattr(twin_of(container), "set_quantity", answer)
    handler = SyncSeatsHandlerImpl(container.managers.tenancy, container.managers.billing)
    with pytest.raises(outcome) as raised:
        await handler.handle(service, item)
    assert str(error) in str(raised.value)
    # Either way the mirror still says what the processor bills.
    billing = await container.managers.billing.get_billing(ctx)
    assert billing.account is not None and billing.account.quantity == 1


def test_every_kind_is_asked_for_by_a_permission_as_wide_as_its_handler(tmp_path: Path) -> None:
    """Whoever may ask for a kind may make every call its handler makes: the
    authorization at enqueue covers the whole run."""
    loop = build_loop(build_container(tmp_path))
    handlers = loop._handlers  # the worker's own table, read to hold it to the rule
    assert set(handlers) == set(WorkKind) == set(WORK_ENQUEUE_PERMISSIONS)
    for kind, handler in handlers.items():
        asking = WORK_ENQUEUE_PERMISSIONS[kind]
        requires = type(handler).REQUIRES
        for role, permissions in ROLE_PERMISSIONS.items():
            if asking in permissions:
                missing = [p for p in requires if p not in permissions]
                assert not missing, f"{role.value} asks for {kind.value} without {missing}"


@pytest.mark.parametrize("role", [Role.MEMBER, Role.VIEWER])
def test_a_role_that_does_not_manage_members_never_asks_for_the_seat_count(role: Role) -> None:
    assert WORK_ENQUEUE_PERMISSIONS[WorkKind.SYNC_SEATS] not in ROLE_PERMISSIONS[role]
