"""The Valkey cache and topics impls over the compose stack's Valkey, through
the configured infra root a process builds."""

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest

from acme.infra.base import SYSTEM_SCOPE, new_id, utcnow
from acme.infra.cache import CacheScope, cache_key
from acme.infra.cache.valkey import CacheValkeyImpl
from acme.infra.impl.configured import InfraConfiguredImpl
from acme.infra.impl.settings import InfraSettings
from acme.infra.impl.valkey import ValkeyConnection
from acme.infra.observability import OUTCOMES
from acme.infra.topics import TopicPayload, Topics, WorkAvailablePayload
from acme.infra.topics.valkey import TopicsValkeyImpl

pytestmark = pytest.mark.integration


@pytest.fixture
async def infra() -> AsyncIterator[InfraConfiguredImpl]:
    settings = InfraSettings.model_validate(
        {
            "environment": "local",
            "cache_backend": "valkey",
            "topics_backend": "valkey",
            "valkey_url": InfraSettings().valkey_url,
        }
    )
    root = InfraConfiguredImpl(settings)
    await root.start()
    try:
        yield root
    finally:
        await root.close()


async def test_put_get_invalidate_and_tenant_scoping(infra: InfraConfiguredImpl) -> None:
    cache = infra.get_cache(CacheScope.NETWORK_RESPONSE)
    org_a, org_b = new_id(), new_id()
    await cache.put(org_a, "k", b"a", timedelta(seconds=30))
    await cache.put(SYSTEM_SCOPE, "k", b"system", timedelta(seconds=30))
    assert await cache.get(org_a, "k") == b"a"
    assert await cache.get(org_b, "k") is None
    assert await cache.get(SYSTEM_SCOPE, "k") == b"system"
    await cache.invalidate(org_a, "k")
    assert await cache.get(org_a, "k") is None


async def test_entries_expire(infra: InfraConfiguredImpl) -> None:
    cache = infra.get_cache(CacheScope.NETWORK_RESPONSE)
    org = new_id()
    await cache.put(org, "k", b"v", timedelta(milliseconds=50))
    await asyncio.sleep(0.2)
    assert await cache.get(org, "k") is None


async def test_increment_counts_within_a_window(infra: InfraConfiguredImpl) -> None:
    cache = infra.get_cache(CacheScope.RATE_LIMIT)
    org = new_id()
    count, remaining = await cache.increment(org, "login", timedelta(seconds=60))
    assert count == 1 and timedelta(seconds=59) < remaining <= timedelta(seconds=60)
    count, _ = await cache.increment(org, "login", timedelta(seconds=60))
    assert count == 2


async def test_a_counter_reads_back_as_its_count(infra: InfraConfiguredImpl) -> None:
    """The contract a generation stands on, the same in memory: `get` of a
    counted key answers the count in decimal ASCII, and a count dropped by
    `invalidate` starts again at one."""
    cache = infra.get_cache(CacheScope.NETWORK_RESPONSE)
    org = new_id()
    assert await cache.get(org, "generation") is None
    await cache.increment(org, "generation", timedelta(days=1))
    await cache.increment(org, "generation", timedelta(days=1))
    assert await cache.get(org, "generation") == b"2"
    assert await cache.get(new_id(), "generation") is None
    await cache.invalidate(org, "generation")
    assert await cache.get(org, "generation") is None
    assert (await cache.increment(org, "generation", timedelta(days=1)))[0] == 1


async def test_increment_always_leaves_a_window_on_the_counter() -> None:
    """A counter that lost its TTL (a crash between the count and the expiry
    under the old three-command increment) is given one by the next call, so
    no subject stays rate-limited for good."""
    settings = InfraSettings()
    connection = ValkeyConnection(
        settings.valkey_url, timedelta(seconds=settings.valkey_timeout_seconds)
    )
    try:
        cache = CacheValkeyImpl(connection, CacheScope.RATE_LIMIT)
        org = new_id()
        stored = f"acme:cache:{CacheScope.RATE_LIMIT.value}:{cache_key(org, 'login')}"
        client = await connection.client()
        assert client is not None
        await client.set(stored, "4")  # no expiry: the half-done state
        assert await client.pttl(stored) == -1
        count, remaining = await cache.increment(org, "login", timedelta(seconds=60))
        assert count == 5 and timedelta(seconds=59) < remaining <= timedelta(seconds=60)
        assert 0 < await client.pttl(stored) <= 60_000
        count, _ = await cache.increment(org, "login", timedelta(seconds=60))
        assert count == 6
        await client.delete([stored])
    finally:
        await connection.close()


async def test_a_publish_reaches_a_subscriber(infra: InfraConfiguredImpl) -> None:
    topics = infra.get_topics()
    received: asyncio.Queue[TopicPayload] = asyncio.Queue()

    async def handler(payload: TopicPayload) -> None:
        await received.put(payload)

    topics.subscribe(Topics.WORK_AVAILABLE, "test", handler)
    sent = WorkAvailablePayload(
        idempotency_key=new_id(),
        produced_at=utcnow(),
        org_id=new_id(),
        lane="default",
        kind="NOOP",
    )
    # The subscriber connects lazily; publish until the subscription is live.
    for _ in range(50):
        await topics.publish(Topics.WORK_AVAILABLE, sent)
        try:
            got = await asyncio.wait_for(received.get(), timeout=0.1)
        except TimeoutError:
            continue
        assert got == sent
        return
    pytest.fail("no message arrived")


async def test_a_publish_says_whether_the_bus_took_it(infra: InfraConfiguredImpl) -> None:
    """A bus that cannot be reached drops the publish, counts it, and says so,
    and the bus that answers takes it. Nothing is raised either way."""
    sent = WorkAvailablePayload(
        idempotency_key=new_id(), produced_at=utcnow(), org_id=new_id(), lane="l", kind="NOOP"
    )
    assert await infra.get_topics().publish(Topics.WORK_AVAILABLE, sent) is True
    down = ValkeyConnection("valkey://127.0.0.1:1", timedelta(milliseconds=200))
    failed = OUTCOMES.labels(subsystem="topics", outcome="publish_failed")
    counted = failed._value.get()
    try:
        assert await TopicsValkeyImpl(down).publish(Topics.WORK_AVAILABLE, sent) is False
    finally:
        await down.close()
    assert failed._value.get() == counted + 1


async def test_a_cache_read_works_without_start() -> None:
    """The worker's health probe reads the liveness key without starting the root."""
    settings = InfraSettings.model_validate(
        {
            "environment": "local",
            "cache_backend": "valkey",
            "valkey_url": InfraSettings().valkey_url,
        }
    )
    writer, probe = InfraConfiguredImpl(settings), InfraConfiguredImpl(settings)
    org = new_id()
    try:
        await writer.get_cache(CacheScope.WORKER_LIVENESS).put(
            org, "k", b"v", timedelta(seconds=30)
        )
        assert await probe.get_cache(CacheScope.WORKER_LIVENESS).get(org, "k") == b"v"
    finally:
        await writer.close()
        await probe.close()
