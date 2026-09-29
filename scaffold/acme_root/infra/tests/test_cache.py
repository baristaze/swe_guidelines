from datetime import timedelta

from acme.infra.base import SYSTEM_SCOPE, new_id
from acme.infra.cache import CacheScope
from acme.infra.cache.memory import CacheMemoryImpl


async def test_put_get_invalidate() -> None:
    cache = CacheMemoryImpl(CacheScope.NETWORK_RESPONSE)
    org = new_id()
    assert await cache.get(org, "k") is None
    await cache.put(org, "k", b"v", timedelta(seconds=30))
    assert await cache.get(org, "k") == b"v"
    await cache.invalidate(org, "k")
    assert await cache.get(org, "k") is None


async def test_keys_are_tenant_scoped_and_system_scope_is_disjoint() -> None:
    cache = CacheMemoryImpl(CacheScope.NETWORK_RESPONSE)
    org_a, org_b = new_id(), new_id()
    await cache.put(org_a, "k", b"a", timedelta(seconds=30))
    await cache.put(SYSTEM_SCOPE, "k", b"system", timedelta(seconds=30))
    assert await cache.get(org_b, "k") is None
    assert await cache.get(org_a, "k") == b"a"
    assert await cache.get(SYSTEM_SCOPE, "k") == b"system"


async def test_expired_entries_are_misses() -> None:
    cache = CacheMemoryImpl(CacheScope.NETWORK_RESPONSE)
    org = new_id()
    await cache.put(org, "k", b"v", timedelta(seconds=-1))
    assert await cache.get(org, "k") is None


async def test_increment_counts_within_a_window() -> None:
    cache = CacheMemoryImpl(CacheScope.RATE_LIMIT)
    org = new_id()
    count, remaining = await cache.increment(org, "login", timedelta(seconds=60))
    assert count == 1 and remaining == timedelta(seconds=60)
    count, remaining = await cache.increment(org, "login", timedelta(seconds=60))
    assert count == 2 and remaining <= timedelta(seconds=60)
    assert (await cache.increment(new_id(), "login", timedelta(seconds=60)))[0] == 1
    assert cache.describe() == "cache[rate_limit]=memory"


async def test_a_counter_reads_back_as_its_count() -> None:
    """The contract a generation stands on, the same in Valkey: `get` of a
    counted key answers the count in decimal ASCII, and a count dropped by
    `invalidate` starts again at one."""
    cache = CacheMemoryImpl(CacheScope.NETWORK_RESPONSE)
    org = new_id()
    assert await cache.get(org, "generation") is None
    await cache.increment(org, "generation", timedelta(days=1))
    await cache.increment(org, "generation", timedelta(days=1))
    assert await cache.get(org, "generation") == b"2"
    assert await cache.get(new_id(), "generation") is None
    await cache.invalidate(org, "generation")
    assert await cache.get(org, "generation") is None
    assert (await cache.increment(org, "generation", timedelta(days=1)))[0] == 1


async def test_a_counter_past_its_window_reads_as_a_miss() -> None:
    cache = CacheMemoryImpl(CacheScope.NETWORK_RESPONSE)
    org = new_id()
    await cache.increment(org, "generation", timedelta(seconds=-1))
    assert await cache.get(org, "generation") is None
