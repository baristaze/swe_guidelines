"""The outage signal: the shared impl over a cache, which every process on
that cache reads, and the null impl of one process, which never marks."""

from datetime import timedelta
from uuid import UUID

import pytest

from acme.infra.base import SYSTEM_SCOPE, new_id, utcnow
from acme.infra.cache import CacheScope
from acme.infra.cache.memory import CacheMemoryImpl
from acme.infra.outages import Outage
from acme.infra.outages.cache import OutageSignalCacheImpl, outage_key
from acme.infra.outages.null import OutageSignalNullImpl


def outage(
    provider: str = "identity",
    credential: str = "identity_api_key",
    *,
    seconds: float = 60,
    org_id: UUID = SYSTEM_SCOPE,
) -> Outage:
    return Outage(
        org_id=org_id,
        provider=provider,
        credential=credential,
        retry_at=utcnow() + timedelta(seconds=seconds),
    )


def shared() -> tuple[OutageSignalCacheImpl, CacheMemoryImpl]:
    cache = CacheMemoryImpl(CacheScope.OUTAGE)
    return OutageSignalCacheImpl(cache), cache


async def test_a_mark_is_read_by_every_signal_on_the_same_cache() -> None:
    """Two signals over one cache stand for two processes over one Valkey."""
    first, cache = shared()
    second = OutageSignalCacheImpl(cache)
    marked = outage()
    await first.mark(marked)
    assert await second.current(SYSTEM_SCOPE, "identity", "identity_api_key") == marked


async def test_a_success_clears_the_mark() -> None:
    signal, _ = shared()
    await signal.mark(outage())
    await signal.clear(SYSTEM_SCOPE, "identity", "identity_api_key")
    assert await signal.current(SYSTEM_SCOPE, "identity", "identity_api_key") is None


async def test_a_mark_is_keyed_by_provider_and_credential() -> None:
    """One org's credential failing is no outage of another's, nor of the
    platform's, nor of another provider's."""
    signal, _ = shared()
    org = new_id()
    await signal.mark(outage(org_id=org))
    assert await signal.current(org, "identity", "identity_api_key") is not None
    assert await signal.current(SYSTEM_SCOPE, "identity", "identity_api_key") is None
    assert await signal.current(new_id(), "identity", "identity_api_key") is None
    assert await signal.current(org, "identity", "other_key") is None
    assert await signal.current(org, "mail", "identity_api_key") is None


async def test_the_later_retry_time_is_kept() -> None:
    signal, _ = shared()
    later = outage(seconds=120)
    await signal.mark(later)
    await signal.mark(outage(seconds=30))
    assert await signal.current(SYSTEM_SCOPE, "identity", "identity_api_key") == later
    latest = outage(seconds=300)
    await signal.mark(latest)
    assert await signal.current(SYSTEM_SCOPE, "identity", "identity_api_key") == latest


async def test_a_retry_time_that_has_passed_marks_nothing() -> None:
    signal, _ = shared()
    await signal.mark(outage(seconds=-1))
    assert await signal.current(SYSTEM_SCOPE, "identity", "identity_api_key") is None


async def test_a_mark_past_its_retry_time_is_no_mark() -> None:
    """The entry's lifetime ends at the retry time; a value read past it, as a
    slow clock between processes may leave, is no mark either."""
    signal, cache = shared()
    stale = outage(seconds=-5)
    key = outage_key("identity", "identity_api_key")
    await cache.put(SYSTEM_SCOPE, key, stale.model_dump_json().encode(), timedelta(minutes=5))
    assert await signal.current(SYSTEM_SCOPE, "identity", "identity_api_key") is None


@pytest.mark.parametrize("value", [b"not json", b'{"provider": "identity"}'])
async def test_a_value_no_mark_wrote_is_no_mark(value: bytes) -> None:
    signal, cache = shared()
    key = outage_key("identity", "identity_api_key")
    await cache.put(SYSTEM_SCOPE, key, value, timedelta(minutes=5))
    assert await signal.current(SYSTEM_SCOPE, "identity", "identity_api_key") is None


async def test_a_mark_under_another_pairs_key_is_no_mark() -> None:
    signal, cache = shared()
    other = outage("identity", "other_key")
    key = outage_key("identity", "identity_api_key")
    await cache.put(SYSTEM_SCOPE, key, other.model_dump_json().encode(), timedelta(minutes=5))
    assert await signal.current(SYSTEM_SCOPE, "identity", "identity_api_key") is None


def test_no_credential_reaches_another_pairs_key() -> None:
    assert outage_key("a:b", "c") != outage_key("a", "b:c")
    assert outage_key("identity", "a/b") == "outage:identity:a%2Fb"


async def test_a_cache_that_cannot_answer_is_no_mark() -> None:
    """It fails open: what the cache answers for a backend it cannot reach,
    a miss, is a pair with no mark, and the caller calls."""

    class Unreachable(CacheMemoryImpl):
        async def get(self, org_id: UUID, key: str) -> bytes | None:
            return None

        async def put(self, org_id: UUID, key: str, value: bytes, ttl: timedelta) -> None:
            return None

    signal = OutageSignalCacheImpl(Unreachable(CacheScope.OUTAGE))
    await signal.mark(outage())
    assert await signal.current(SYSTEM_SCOPE, "identity", "identity_api_key") is None


async def test_the_null_signal_never_marks() -> None:
    signal = OutageSignalNullImpl()
    await signal.mark(outage())
    assert await signal.current(SYSTEM_SCOPE, "identity", "identity_api_key") is None
    await signal.clear(SYSTEM_SCOPE, "identity", "identity_api_key")
    assert signal.describe() == "outages=none"
    await signal.start()
    await signal.close()


def test_the_shared_signal_names_its_cache() -> None:
    signal, _ = shared()
    assert signal.describe() == "outages=shared(cache[outage]=memory)"
