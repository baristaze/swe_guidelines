"""The breaker over the two interfaces that talk to Valkey: what it counts,
what it refuses, and what a caller reads while it is open. Each inner impl is a
fake whose calls cost whatever the case says on the fake clock the breaker
reads, so nothing here sleeps and nothing here needs a Valkey."""

import asyncio
from collections.abc import Callable
from datetime import timedelta
from uuid import UUID

import pytest

from acme.infra.base import new_id, utcnow
from acme.infra.breaker import Breaker
from acme.infra.cache import CacheInterface
from acme.infra.cache.breaker import CacheBreakerImpl
from acme.infra.exceptions import PayloadMismatch
from acme.infra.topics import (
    EntityChangedPayload,
    TopicHandler,
    TopicPayload,
    Topics,
    TopicsInterface,
    WorkAvailablePayload,
)
from acme.infra.topics.breaker import TopicsBreakerImpl

TIMEOUT = timedelta(seconds=5)
COOLDOWN = timedelta(seconds=30)
FAILURES = 3
TTL = timedelta(seconds=60)


class FakeClock:
    """The breaker's only source of time, advanced by the fake inner impl and
    by the cases, so a cool-down passes without waiting for one."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeCache(CacheInterface):
    """An inner impl that records every call and charges it `cost` seconds.
    `gate`, when set, holds a call open so a case can see what the breaker
    does while one is in flight."""

    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock
        self.cost = 0.0
        self.calls: list[str] = []
        self.gate: asyncio.Event | None = None
        self.started = asyncio.Event()

    async def _called(self, operation: str) -> None:
        self.calls.append(operation)
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        self._clock.advance(self.cost)

    async def get(self, org_id: UUID, key: str) -> bytes | None:
        await self._called("get")
        return b"inner"

    async def put(self, org_id: UUID, key: str, value: bytes, ttl: timedelta) -> None:
        await self._called("put")

    async def invalidate(self, org_id: UUID, key: str) -> None:
        await self._called("invalidate")

    async def increment(self, org_id: UUID, key: str, ttl: timedelta) -> tuple[int, timedelta]:
        await self._called("increment")
        return 7, ttl

    def describe(self) -> str:
        return "cache[rate_limit]=fake"

    async def start(self) -> None:
        self.calls.append("start")

    async def close(self) -> None:
        self.calls.append("close")


def a_breaker() -> tuple[CacheBreakerImpl, FakeCache, FakeClock, Breaker]:
    clock = FakeClock()
    inner = FakeCache(clock)
    breaker = Breaker(
        "valkey_breaker", failures=FAILURES, cooldown=COOLDOWN, slow=TIMEOUT, clock=clock
    )
    return CacheBreakerImpl(inner, breaker), inner, clock, breaker


async def spend_the_timeout(cache: CacheBreakerImpl, inner: FakeCache, times: int) -> None:
    inner.cost = TIMEOUT.total_seconds()
    org = new_id()
    for _ in range(times):
        await cache.get(org, "k")


async def test_the_breaker_opens_after_the_configured_failures_in_a_row() -> None:
    cache, inner, _, breaker = a_breaker()
    await spend_the_timeout(cache, inner, FAILURES - 1)
    assert not breaker.is_open, "a call short of the bound still goes out"
    await spend_the_timeout(cache, inner, 1)
    assert breaker.is_open
    assert len(inner.calls) == FAILURES


async def test_a_call_that_comes_back_in_time_resets_the_count() -> None:
    """Consecutive, not cumulative: a backend that answers between two slow
    calls is a backend that is up."""
    cache, inner, _, breaker = a_breaker()
    org = new_id()
    await spend_the_timeout(cache, inner, FAILURES - 1)
    inner.cost = 0.001
    await cache.get(org, "k")
    await spend_the_timeout(cache, inner, FAILURES - 1)
    assert not breaker.is_open


async def test_an_open_breaker_refuses_every_method_without_calling_the_inner() -> None:
    """The answers are the ones the Valkey impl gives for a backend it cannot
    reach, so no caller can tell the two apart: a miss, dropped writes, and the
    count no count that a rate limit reads as fail open."""
    cache, inner, _, _ = a_breaker()
    await spend_the_timeout(cache, inner, FAILURES)
    inner.calls.clear()
    org = new_id()
    assert await cache.get(org, "k") is None
    await cache.put(org, "k", b"v", TTL)
    await cache.invalidate(org, "k")
    assert await cache.increment(org, "login", TTL) == (0, TTL)
    assert inner.calls == []


async def test_the_lifecycle_is_forwarded_whatever_the_state_is() -> None:
    """`start` and `close` are the root's, not calls on the dependency."""
    cache, inner, _, breaker = a_breaker()
    await spend_the_timeout(cache, inner, FAILURES)
    assert breaker.is_open
    inner.calls.clear()
    await cache.start()
    await cache.close()
    assert inner.calls == ["start", "close"]


async def test_one_call_goes_through_after_the_cooldown_and_closes_on_success() -> None:
    cache, inner, clock, breaker = a_breaker()
    await spend_the_timeout(cache, inner, FAILURES)
    org = new_id()
    clock.advance(COOLDOWN.total_seconds() - 0.001)
    inner.calls.clear()
    assert await cache.get(org, "k") is None, "still inside the cool-down"
    assert inner.calls == []
    clock.advance(0.002)
    inner.cost = 0.001
    assert await cache.get(org, "k") == b"inner"
    assert inner.calls == ["get"]
    assert not breaker.is_open


async def test_a_probe_that_spends_the_timeout_opens_for_another_cooldown() -> None:
    cache, inner, clock, breaker = a_breaker()
    await spend_the_timeout(cache, inner, FAILURES)
    clock.advance(COOLDOWN.total_seconds())
    inner.calls.clear()
    await spend_the_timeout(cache, inner, 1)
    assert inner.calls == ["get"], "the probe went out"
    assert breaker.is_open
    org = new_id()
    clock.advance(COOLDOWN.total_seconds() - 0.001)
    assert await cache.get(org, "k") is None
    assert inner.calls == ["get"], "the cool-down runs again from the probe"


async def test_only_the_probe_goes_out_while_it_is_in_flight() -> None:
    """A dependency that is still down is asked by one call, not by every call
    that arrives in the timeout the probe is spending."""
    cache, inner, clock, _ = a_breaker()
    await spend_the_timeout(cache, inner, FAILURES)
    clock.advance(COOLDOWN.total_seconds())
    inner.calls.clear()
    inner.gate = asyncio.Event()
    inner.started.clear()
    org = new_id()
    probe = asyncio.create_task(cache.get(org, "k"))
    await inner.started.wait()
    assert await cache.get(org, "k") is None, "refused while the probe is out"
    assert inner.calls == ["get"]
    inner.gate.set()
    assert await probe == b"inner", "the probe is a real call and answers what the backend said"


async def test_a_probe_cancelled_in_time_neither_closes_nor_blocks_the_next_probe() -> None:
    """A probe whose caller left says nothing about the backend: the breaker
    stays open, and the next call is the probe instead of waiting behind one
    that will never answer."""
    cache, inner, clock, breaker = a_breaker()
    await spend_the_timeout(cache, inner, FAILURES)
    clock.advance(COOLDOWN.total_seconds())
    inner.calls.clear()
    inner.cost = 0.001
    inner.gate = asyncio.Event()
    inner.started.clear()
    org = new_id()
    probe = asyncio.create_task(cache.get(org, "k"))
    await inner.started.wait()
    probe.cancel()
    with pytest.raises(asyncio.CancelledError):
        await probe
    assert breaker.is_open, "a cancelled probe is not a success"
    inner.gate = None
    assert await cache.get(org, "k") == b"inner", "the next call is the probe"
    assert not breaker.is_open


async def test_every_impl_that_shares_a_breaker_shares_its_failures() -> None:
    """One breaker stands for one dependency, not for one interface: the cache
    scope that pays the timeouts opens it for every other scope and for the
    publisher behind the same Valkey."""
    clock = FakeClock()
    breaker = Breaker(
        "valkey_breaker", failures=FAILURES, cooldown=COOLDOWN, slow=TIMEOUT, clock=clock
    )
    limits, tickets, bus = FakeCache(clock), FakeCache(clock), FakeTopics(clock)
    over_limits = CacheBreakerImpl(limits, breaker)
    over_tickets = CacheBreakerImpl(tickets, breaker)
    over_bus = TopicsBreakerImpl(bus, breaker)
    await spend_the_timeout(over_limits, limits, FAILURES)
    assert breaker.is_open
    assert await over_tickets.get(new_id(), "k") is None
    await over_bus.publish(Topics.ENTITY_CHANGED, a_change())
    assert tickets.calls == [] and bus.published == []


async def test_describe_names_the_inner_backend_and_the_breaker() -> None:
    cache, _, _, _ = a_breaker()
    assert cache.describe() == "cache[rate_limit]=fake+breaker(3/30s)"


@pytest.mark.parametrize("failures", [1, 2, 5])
async def test_the_failure_bound_is_the_one_it_was_built_with(failures: int) -> None:
    clock = FakeClock()
    inner = FakeCache(clock)
    breaker = Breaker(
        "valkey_breaker", failures=failures, cooldown=COOLDOWN, slow=TIMEOUT, clock=clock
    )
    cache = CacheBreakerImpl(inner, breaker)
    await spend_the_timeout(cache, inner, failures - 1)
    assert not breaker.is_open
    await spend_the_timeout(cache, inner, 1)
    assert breaker.is_open
    assert len(inner.calls) == failures


def a_change() -> EntityChangedPayload:
    return EntityChangedPayload(
        idempotency_key=new_id(),
        produced_at=utcnow(),
        org_id=new_id(),
        kind="tasks.task.updated",
        target_id=new_id(),
        seq=1,
    )


class FakeTopics(TopicsInterface):
    """A publisher that records what reached the bus and charges each publish
    `cost` seconds on the same fake clock."""

    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock
        self.cost = 0.0
        self.published: list[Topics] = []
        self.subscribed: list[str] = []
        self.lifecycle: list[str] = []

    async def publish(self, topic: Topics, payload: TopicPayload) -> bool:
        self.published.append(topic)
        self._clock.advance(self.cost)
        return True

    def subscribe(self, topic: Topics, consumer: str, handler: TopicHandler) -> Callable[[], None]:
        self.subscribed.append(consumer)
        return lambda: None

    def describe(self) -> str:
        return "topics=fake"

    async def start(self) -> None:
        self.lifecycle.append("start")

    async def close(self) -> None:
        self.lifecycle.append("close")


def a_topics_breaker() -> tuple[TopicsBreakerImpl, FakeTopics, FakeClock, Breaker]:
    clock = FakeClock()
    inner = FakeTopics(clock)
    breaker = Breaker(
        "valkey_breaker", failures=FAILURES, cooldown=COOLDOWN, slow=TIMEOUT, clock=clock
    )
    return TopicsBreakerImpl(inner, breaker), inner, clock, breaker


async def publish_the_timeout(topics: TopicsBreakerImpl, inner: FakeTopics, times: int) -> None:
    inner.cost = TIMEOUT.total_seconds()
    for _ in range(times):
        await topics.publish(Topics.ENTITY_CHANGED, a_change())


async def test_the_publisher_opens_after_the_same_bound() -> None:
    topics, inner, _, breaker = a_topics_breaker()
    await publish_the_timeout(topics, inner, FAILURES - 1)
    assert not breaker.is_open
    await publish_the_timeout(topics, inner, 1)
    assert breaker.is_open
    assert len(inner.published) == FAILURES


async def test_an_open_publisher_drops_the_publish_without_reaching_the_bus() -> None:
    """A topic is best effort, so that is the answer: the durable part of the
    write has landed, and the publish says it was dropped, never that it
    landed, so the outbox relay keeps its row."""
    topics, inner, _, _ = a_topics_breaker()
    assert await topics.publish(Topics.ENTITY_CHANGED, a_change()) is True
    await publish_the_timeout(topics, inner, FAILURES)
    inner.published.clear()
    assert await topics.publish(Topics.ENTITY_CHANGED, a_change()) is False
    assert inner.published == []


async def test_a_wrong_payload_raises_whether_the_breaker_is_open_or_closed() -> None:
    """The breaker declines to pay the timeout, never to keep the contract: a
    bug in a caller must not go quiet for a cool-down."""
    topics, inner, _, breaker = a_topics_breaker()
    wrong = WorkAvailablePayload(
        idempotency_key=new_id(), produced_at=utcnow(), org_id=new_id(), lane="l", kind="k"
    )
    with pytest.raises(PayloadMismatch):
        await topics.publish(Topics.ENTITY_CHANGED, wrong)
    await publish_the_timeout(topics, inner, FAILURES)
    assert breaker.is_open
    with pytest.raises(PayloadMismatch):
        await topics.publish(Topics.ENTITY_CHANGED, wrong)


async def test_subscribing_and_the_lifecycle_are_never_refused() -> None:
    """`subscribe` registers a handler in this process and opens nothing, and
    `start` and `close` are the root's, not calls on the dependency."""
    topics, inner, _, breaker = a_topics_breaker()
    await publish_the_timeout(topics, inner, FAILURES)
    assert breaker.is_open
    unsubscribe = topics.subscribe(Topics.WORK_AVAILABLE, "worker", _ignore)
    unsubscribe()
    await topics.start()
    await topics.close()
    assert inner.subscribed == ["worker"]
    assert inner.lifecycle == ["start", "close"]


async def test_the_publisher_closes_on_a_publish_that_lands_in_time() -> None:
    topics, inner, clock, breaker = a_topics_breaker()
    await publish_the_timeout(topics, inner, FAILURES)
    clock.advance(COOLDOWN.total_seconds())
    inner.cost = 0.001
    await topics.publish(Topics.ENTITY_CHANGED, a_change())
    assert not breaker.is_open


async def test_the_publisher_describe_names_the_inner_backend_and_the_breaker() -> None:
    topics, _, _, _ = a_topics_breaker()
    assert topics.describe() == "topics=fake+breaker(3/30s)"


async def _ignore(payload: TopicPayload) -> None:
    return None
