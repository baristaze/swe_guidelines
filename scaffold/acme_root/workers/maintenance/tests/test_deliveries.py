"""The consumer of what providers deliver: an identity provider's event
appended once to the stream of the org it names, however often the queue
hands it over; a delivery that can never apply dropped and deleted; one that
failed, or names a provider this worker lacks, left to come back later on
each receive; and the consumer running until it is stopped."""

import asyncio
import json
from collections.abc import Callable
from datetime import timedelta
from itertools import pairwise
from pathlib import Path
from typing import cast

import pytest
from worker_support import build_container, ended, sign_in

from acme.infra.queues import QueueMessage, Queues
from acme.integrations.identity import ProvidedDelivery
from acme.integrations.identity.twin import IdentityProviderTwinImpl
from acme.om.base import new_id, utcnow
from acme.om.change_events.types.change_event import ChangeEvent
from acme.om.context import TenantContext
from acme.workers.maintenance.container import WorkerContainer
from acme.workers.maintenance.deliveries import MAX_VISIBILITY, DeliveryConsumer, DeliveryOptions
from acme.workers.maintenance.main import build_consumer

RECEIVED = "identity.event.received"


def consumer_of(
    container: WorkerContainer, jitter: Callable[[], float] | None = None
) -> DeliveryConsumer:
    """The worker's own consumer, with a long poll that answers at once, and
    the jitter handed in when a test asserts the waits."""
    built = build_consumer(container)
    options = built._options  # pyright: ignore[reportPrivateUsage]
    built._options = options.model_copy(  # pyright: ignore[reportPrivateUsage]
        update={"wait": timedelta(0)}
    )
    if jitter is not None:
        built._jitter = jitter  # pyright: ignore[reportPrivateUsage]
    return built


def held_by(container: WorkerContainer, monkeypatch: pytest.MonkeyPatch) -> list[timedelta]:
    """The visibility each kept message is given, in order, on its way to
    the memory queue."""
    queues = container.infra.get_queues()
    change = queues.change_visibility
    held: list[timedelta] = []

    async def recorded(queue: Queues, receipt: str, visibility: timedelta) -> None:
        held.append(visibility)
        await change(queue, receipt, visibility)

    monkeypatch.setattr(queues, "change_visibility", recorded)
    return held


async def back_now(container: WorkerContainer, message: QueueMessage) -> None:
    """The message's wait passes: visible to the next receive at once."""
    queues = container.infra.get_queues()
    await type(queues).change_visibility(
        queues, Queues.WEBHOOKS, message.receipt, timedelta(seconds=-1)
    )


async def down(*_: object) -> ChangeEvent:
    raise ConnectionResetError("the database went away")


def delivery_about(container: WorkerContainer, external_id: str | None) -> ProvidedDelivery:
    """An organization's event as the provider signs it and the API verifies
    it, the organization naming `external_id` as its Acme org."""
    twin = cast(IdentityProviderTwinImpl, container.identity_provider)
    data: dict[str, object] = {"object": "organization", "id": "org_twin_1", "name": "Ajax"}
    if external_id is not None:
        data["external_id"] = external_id
    payload, signature = twin.signed_event("organization.updated", data)
    return twin.verify_delivery(payload, signature)


async def queued(container: WorkerContainer, body: bytes) -> QueueMessage:
    """What the queue hands the consumer for a body the API sent."""
    queues = container.infra.get_queues()
    await queues.send(Queues.WEBHOOKS, body)
    [message] = await queues.receive(Queues.WEBHOOKS, 1, timedelta(0), timedelta(seconds=30))
    return message


def body_of(delivery: ProvidedDelivery, provider: str = "identity") -> bytes:
    """The message the API queues for a verified delivery."""
    return json.dumps(
        {
            "idempotency_key": str(delivery.key),
            "provider": provider,
            "delivery": delivery.model_dump(mode="json"),
        }
    ).encode()


async def received(container: WorkerContainer, ctx: TenantContext) -> list[ChangeEvent]:
    events = await container.managers.change_events.get_change_events(ctx, after_seq=0, limit=100)
    return [event for event in events if event.kind == RECEIVED]


async def depth(container: WorkerContainer) -> tuple[int, int]:
    found = await container.infra.get_queues().depth(Queues.WEBHOOKS)
    return found.visible, found.in_flight


async def test_a_delivery_is_appended_once_when_the_queue_hands_it_over_twice(
    tmp_path: Path,
) -> None:
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    delivery = delivery_about(container, str(ctx.org_id))
    consumer = consumer_of(container)
    assert await consumer.handle(await queued(container, body_of(delivery))) == "applied"
    assert await consumer.handle(await queued(container, body_of(delivery))) == "duplicate"
    (event,) = await received(container, ctx)
    assert event.target_id == ctx.org_id
    assert dict(event.payload) == {"event_id": delivery.event_id, "event_type": delivery.event_type}
    assert await depth(container) == (0, 0), "both copies are deleted"


async def test_a_delivery_that_can_never_apply_is_dropped_and_deleted(tmp_path: Path) -> None:
    """An org that is gone, or was never Acme's, a delivery that names none,
    and a body that is not a delivery: each counted, and deleted, since no
    receive would apply it."""
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    consumer = consumer_of(container)
    storage = container.storage.get_tenancy_storage()
    org = await storage.read_org(ctx.org_id)
    assert org is not None
    await storage.write_org(org.id, org.model_copy(update={"deleted_at": utcnow()}))
    cases = [
        (body_of(delivery_about(container, str(ctx.org_id))), "unowned"),
        (body_of(delivery_about(container, str(new_id()))), "unowned"),
        (body_of(delivery_about(container, "not-an-acme-org")), "unowned"),
        (body_of(delivery_about(container, None)), "unowned"),
        (b"not a delivery", "malformed"),
        (json.dumps({"provider": "identity"}).encode(), "malformed"),
        (json.dumps({"provider": "identity", "delivery": {"key": "k"}}).encode(), "malformed"),
    ]
    for body, outcome in cases:
        assert await consumer.handle(await queued(container, body)) == outcome, body
    assert await depth(container) == (0, 0)


async def test_a_failed_append_leaves_the_message_to_come_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    consumer = consumer_of(container)
    monkeypatch.setattr(container.managers.change_events, "append_change_event", down)
    delivery = delivery_about(container, str(ctx.org_id))
    assert await consumer.handle(await queued(container, body_of(delivery))) == "failed"
    assert await depth(container) == (0, 1), "received, not deleted: back after its visibility"
    monkeypatch.undo()
    assert await received(container, ctx) == []


@pytest.mark.parametrize(
    "draws",
    [[0.0] * 5, [1.0] * 5, [1.0, 0.0, 1.0, 0.0, 1.0]],
    ids=["jitter-low", "jitter-high", "jitter-high-then-low"],
)
async def test_a_failed_delivery_comes_back_later_on_each_receive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, draws: list[float]
) -> None:
    """Five failed receives hide the delivery for longer each time, so an
    outage of more than an hour passes before the queue dead-letters it."""
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    consumer = consumer_of(container, iter(draws).__next__)
    held = held_by(container, monkeypatch)
    monkeypatch.setattr(container.managers.change_events, "append_change_event", down)
    queues = container.infra.get_queues()
    await queues.send(Queues.WEBHOOKS, body_of(delivery_about(container, str(ctx.org_id))))
    for receive in range(1, 6):
        [message] = await queues.receive(Queues.WEBHOOKS, 1, timedelta(0), timedelta(seconds=60))
        assert message.attempts == receive
        assert await consumer.handle(message) == "failed"
        await back_now(container, message)
    assert len(held) == 5, held
    assert all(earlier < later for earlier, later in pairwise(held)), held
    assert max(held) <= MAX_VISIBILITY
    assert sum(held, timedelta()) > timedelta(hours=1), held


async def test_deliveries_that_failed_together_come_back_apart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    consumer = consumer_of(container, iter([0.25, 0.75]).__next__)
    held = held_by(container, monkeypatch)
    monkeypatch.setattr(container.managers.change_events, "append_change_event", down)
    queues = container.infra.get_queues()
    for _ in range(2):
        await queues.send(Queues.WEBHOOKS, body_of(delivery_about(container, str(ctx.org_id))))
    both = await queues.receive(Queues.WEBHOOKS, 2, timedelta(0), timedelta(seconds=60))
    assert [message.attempts for message in both] == [1, 1]
    for message in both:
        assert await consumer.handle(message) == "failed"
    assert len(held) == 2, held
    assert held[0] != held[1]


async def test_the_wait_never_passes_the_queue_maximum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """However many receives a queue allows, no wait passes the queue's
    maximum less the receive's visibility, nor overflows on its way there."""
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    consumer = consumer_of(container, lambda: 1.0)
    held = held_by(container, monkeypatch)
    monkeypatch.setattr(container.managers.change_events, "append_change_event", down)
    message = await queued(container, body_of(delivery_about(container, str(ctx.org_id))))
    for attempts in (1, 5, 8, 50, 10_000):
        again = message.model_copy(update={"attempts": attempts})
        assert await consumer.handle(again) == "failed"
    cap = MAX_VISIBILITY - DeliveryOptions(worker_id="maintenance-test").visibility
    assert all(wait <= cap for wait in held), held
    assert held[-1] == cap


async def test_a_provider_this_worker_lacks_leaves_the_message(tmp_path: Path) -> None:
    """A newer API may queue a provider this worker does not know: the
    message stays, for a worker that does, or for the dead letters."""
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    delivery = delivery_about(container, str(ctx.org_id))
    message = await queued(container, body_of(delivery, provider="elsewhere"))
    assert await consumer_of(container).handle(message) == "unknown_provider"
    assert await depth(container) == (0, 1)


async def test_the_consumer_runs_until_it_is_stopped(tmp_path: Path) -> None:
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    delivery = delivery_about(container, str(ctx.org_id))
    await container.infra.get_queues().send(Queues.WEBHOOKS, body_of(delivery))
    consumer = consumer_of(container)
    running = asyncio.create_task(consumer.run())
    for _ in range(100):
        if await received(container, ctx):
            break
        await asyncio.sleep(0.01)
    consumer.stop()
    await ended(running)
    assert len(await received(container, ctx)) == 1
    assert await depth(container) == (0, 0)
