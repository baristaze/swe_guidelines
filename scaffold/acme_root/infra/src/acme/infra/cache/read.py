"""A read cache: a projection with a generation, the shape a manager's cached
read takes.

An entry's key is the read's name and the tenant's generation,
`<name>:<generation>`, in the scope the manager was handed and under the
tenant the call names, so no tenant reads another's entry. The generation is
a counter in the same scope, one per tenant. A write bumps it with one
`increment` once its transaction commits, and every entry cached before the
write is orphaned at once: nothing enumerates a tenant's keys. A bump before
the commit, or inside the transaction, lets a read between the bump and the
commit cache the old value under the new generation.

The TTL is the backstop and the bound on staleness. No entry lives longer, so
a bump that is lost leaves the old entry readable until then, and never
longer.

It fails open, as the cache does. A miss, an entry this build cannot read,
and a cache that cannot be reached are each a read of the source. A cache
that cannot be reached drops the put, so it holds nothing it could serve.
What the caller may see of a value is decided above this, on every call.
"""

from collections.abc import Awaitable, Callable
from datetime import timedelta
from uuid import UUID

from pydantic import TypeAdapter, ValidationError

from acme.infra.cache import CacheInterface

GENERATION = "generation"
"""The key of the tenant's generation in the scope. No entry's key is it,
since an entry's key always ends in `:<generation>`."""

GENERATION_WINDOW = timedelta(days=1)
"""How long a generation counts from its first bump; `increment` never moves
the window. When it ends the count starts again from none, and the entries of
its first number, cached before the first bump, are gone by then, since every
TTL is shorter. An entry a later number finds again is at most a TTL old,
within the bound the TTL already sets."""


class ReadCache[T]:
    """One manager's cached reads of one type, on the scope it was handed:
    the tenant's data, encoded by `adapter`, for at most `ttl`."""

    def __init__(self, cache: CacheInterface, adapter: TypeAdapter[T], ttl: timedelta) -> None:
        if not timedelta(0) < ttl < GENERATION_WINDOW:
            raise ValueError(
                f"a read cache's TTL is above zero and under {GENERATION_WINDOW}: {ttl}"
            )
        self._cache = cache
        self._adapter = adapter
        self._ttl = ttl

    async def read(self, org_id: UUID, name: str, load: Callable[[], Awaitable[T]]) -> T:
        """The value from the cache, else from `load`, which is then cached.
        The generation is read before `load`, so a value loaded before a write
        and put after its bump lands under a generation no read asks for."""
        generation = await self._generation(org_id)
        if generation is None:
            return await load()
        key = f"{name}:{generation}"
        cached = await self._cache.get(org_id, key)
        if cached is not None:
            try:
                return self._adapter.validate_json(cached)
            except ValidationError:
                pass  # another build's shape: a miss, and the put below replaces it
        value = await load()
        await self._cache.put(org_id, key, self._adapter.dump_json(value), self._ttl)
        return value

    async def bump(self, org_id: UUID) -> None:
        """Called once a write of the tenant's data commits, never before and
        never inside its transaction: one `increment` of the generation, which
        orphans every entry the scope holds for the tenant."""
        await self._cache.increment(org_id, GENERATION, GENERATION_WINDOW)

    async def _generation(self, org_id: UUID) -> int | None:
        """The tenant's generation, zero before its first bump. A value that
        is no count was written by nothing that bumps, and the read goes to
        the source and caches nothing."""
        held = await self._cache.get(org_id, GENERATION)
        if held is None:
            return 0
        return int(held) if held.isdigit() else None
