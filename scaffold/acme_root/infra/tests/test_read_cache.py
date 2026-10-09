"""The read cache over each backend: the memory impl, and Valkey over the
compose stack's, through the configured infra root a process builds."""

from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from pydantic import BaseModel, TypeAdapter

from acme.infra.base import new_id
from acme.infra.cache import CacheInterface, CacheScope
from acme.infra.cache.memory import CacheMemoryImpl
from acme.infra.cache.read import GENERATION, GENERATION_WINDOW, ReadCache
from acme.infra.cache.valkey import CacheValkeyImpl
from acme.infra.impl.configured import InfraConfiguredImpl
from acme.infra.impl.settings import InfraSettings
from acme.infra.impl.valkey import ValkeyConnection

TTL = timedelta(seconds=30)


class Product(BaseModel):
    sku: str
    price_cents: int


PRODUCT = TypeAdapter(Product)


class Source:
    """The source of truth a read falls through to, counting its loads."""

    def __init__(self, value: Product) -> None:
        self.value = value
        self.loads = 0

    async def __call__(self) -> Product:
        self.loads += 1
        return self.value


@pytest.fixture(params=["memory", pytest.param("valkey", marks=pytest.mark.integration)])
async def cache(request: pytest.FixtureRequest) -> AsyncIterator[CacheInterface]:
    if request.param == "memory":
        yield CacheMemoryImpl(CacheScope.NETWORK_RESPONSE)
        return
    settings = InfraSettings.model_validate(
        {
            "environment": "local",
            "cache_backend": "valkey",
            "valkey_url": InfraSettings().valkey_url,
        }
    )
    root = InfraConfiguredImpl(settings)
    await root.start()
    try:
        yield root.get_cache(CacheScope.NETWORK_RESPONSE)
    finally:
        await root.close()


async def test_a_bump_makes_the_next_read_miss(cache: CacheInterface) -> None:
    reads = ReadCache(cache, PRODUCT, TTL)
    org = new_id()
    source = Source(Product(sku="mug", price_cents=900))
    assert await reads.read(org, "product:mug", source) == Product(sku="mug", price_cents=900)
    assert await reads.read(org, "product:mug", source) == Product(sku="mug", price_cents=900)
    assert source.loads == 1
    source.value = Product(sku="mug", price_cents=1200)  # the write commits
    await reads.bump(org)
    assert await reads.read(org, "product:mug", source) == Product(sku="mug", price_cents=1200)
    assert source.loads == 2
    assert await reads.read(org, "product:mug", source) == Product(sku="mug", price_cents=1200)
    assert source.loads == 2


async def test_one_tenants_bump_leaves_another_tenants_entry(cache: CacheInterface) -> None:
    """Two tenants read the same name, and at each step both stand at the
    same generation or one apart, so the same key inside each tenant."""
    reads = ReadCache(cache, PRODUCT, TTL)
    org_a, org_b = new_id(), new_id()
    a = Source(Product(sku="mug", price_cents=900))
    b = Source(Product(sku="mug", price_cents=1500))
    assert await reads.read(org_a, "product:mug", a) == a.value
    assert await reads.read(org_b, "product:mug", b) == b.value
    await reads.bump(org_a)
    assert await reads.read(org_b, "product:mug", b) == b.value
    assert (a.loads, b.loads) == (1, 1)
    assert await reads.read(org_a, "product:mug", a) == a.value
    assert (a.loads, b.loads) == (2, 1)
    await reads.bump(org_b)
    assert await reads.read(org_a, "product:mug", a) == a.value
    assert await reads.read(org_b, "product:mug", b) == b.value
    assert (a.loads, b.loads) == (2, 2)


async def test_an_entry_this_build_cannot_read_is_a_miss(cache: CacheInterface) -> None:
    reads = ReadCache(cache, PRODUCT, TTL)
    org = new_id()
    await cache.put(org, "product:mug:0", b'{"sku": "mug"}', TTL)
    source = Source(Product(sku="mug", price_cents=900))
    assert await reads.read(org, "product:mug", source) == source.value
    assert await reads.read(org, "product:mug", source) == source.value
    assert source.loads == 1


async def test_a_generation_that_is_no_count_reads_through(cache: CacheInterface) -> None:
    reads = ReadCache(cache, PRODUCT, TTL)
    org = new_id()
    await cache.put(org, GENERATION, b"not a count", TTL)
    source = Source(Product(sku="mug", price_cents=900))
    assert await reads.read(org, "product:mug", source) == source.value
    assert await reads.read(org, "product:mug", source) == source.value
    assert source.loads == 2
    assert await cache.get(org, "product:mug:0") is None


async def test_an_unreachable_cache_reads_through() -> None:
    """A Valkey whose connection has closed answers as one that is down,
    without the wait: every read goes to the source, and neither a read nor
    a bump raises."""
    connection = ValkeyConnection("valkey://127.0.0.1:1/0", timedelta(milliseconds=200))
    await connection.close()
    reads = ReadCache(CacheValkeyImpl(connection, CacheScope.NETWORK_RESPONSE), PRODUCT, TTL)
    org = new_id()
    source = Source(Product(sku="mug", price_cents=900))
    assert await reads.read(org, "product:mug", source) == source.value
    await reads.bump(org)
    assert await reads.read(org, "product:mug", source) == source.value
    assert source.loads == 2


@pytest.mark.parametrize("ttl", [timedelta(0), GENERATION_WINDOW])
def test_the_ttl_is_shorter_than_the_generations_window(ttl: timedelta) -> None:
    with pytest.raises(ValueError, match="TTL"):
        ReadCache(CacheMemoryImpl(CacheScope.NETWORK_RESPONSE), PRODUCT, ttl)
