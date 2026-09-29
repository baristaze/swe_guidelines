"""The consumer of what providers deliver: an identity provider's event
appended once to the stream of the org it names, however often the queue
hands it over; a delivery that can never apply dropped and deleted; one that
failed, or names a provider this worker lacks, left to come back; and the
consumer running until it is stopped."""

import asyncio
import json
from datetime import timedelta
from pathlib import Path
from typing import cast

import pytest
from worker_support import build_container, sign_in

from acme.infra.queues import QueueMessage, Queues
from acme.integrations.identity import ProvidedDelivery
from acme.integrations.identity.twin import IdentityProviderTwinImpl
from acme.om.base import new_id, utcnow
from acme.om.events.types.event import Event
from acme.om.opcontext import OpContext
from acme.workers.maintenance.container import WorkerContainer
from acme.workers.maintenance.deliveries import DeliveryConsumer, DeliveryOptions
from acme.workers.maintenance.main import build_consumer

RECEIVED = "identity.event.received"


def consumer_of(container: WorkerContainer) -> DeliveryConsumer:
    """The worker's own consumer, with a long poll that answers at once."""
    built = build_consumer(container)
    built._options = DeliveryOptions(  # pyright: ignore[reportPrivateUsage]
        worker_id="maintenance-test", wait=timedelta(0)
    )
    return built


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


async def received(container: WorkerContainer, ctx: OpContext) -> list[Event]:
    events = await container.managers.events.get_events(ctx, after_seq=0, limit=100)
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

    async def down(*_: object) -> Event:
        raise ConnectionResetError("the database went away")

    monkeypatch.setattr(container.managers.events, "append_event", down)
    delivery = delivery_about(container, str(ctx.org_id))
    assert await consumer.handle(await queued(container, body_of(delivery))) == "failed"
    assert await depth(container) == (0, 1), "received, not deleted: back after its visibility"
    monkeypatch.undo()
    assert await received(container, ctx) == []


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
    await asyncio.wait_for(running, timeout=5)
    assert len(await received(container, ctx)) == 1
    assert await depth(container) == (0, 0)
