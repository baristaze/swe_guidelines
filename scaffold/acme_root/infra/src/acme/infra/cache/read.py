"""A read cache: a projection with a generation, the shape a manager's cached
read takes.

An entry's key is the read's name, the window, and the tenant's generation,
`<name>:<window>:<generation>`, in the scope the manager was handed and under
the tenant the call names, so no tenant reads another's entry. The generation
is a counter in the same scope, one per tenant and window. A write bumps it
with one `increment` once its transaction commits, and every entry cached
before the write is orphaned at once: nothing enumerates a tenant's keys. A
bump before the commit, or inside the transaction, lets a read between the
bump and the commit cache the old value under the new generation.

The TTL is the backstop and the bound on staleness. No entry lives longer, so
a bump that is lost leaves the old entry readable until then, and never
longer.

It fails open, as the cache does. A miss, an entry this build cannot read,
and a cache that cannot be reached are each a read of the source. A cache
that cannot be reached drops the put, so it holds nothing it could serve.
What the caller may see of a value is decided above this, on every call.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import TypeAdapter, ValidationError

from acme.infra.base import utcnow
from acme.infra.cache import CacheInterface

GENERATION = "generation"
"""The tenant's generation in a window is the key `generation:<window>`. No
entry's key is one, since an entry's key ends in `:<window>:<generation>`."""

GENERATION_WINDOW = timedelta(days=1)
"""The span a generation counts in. Windows are whole spans from the epoch,
and the window is part of the generation's key and of every entry's, so a
count that starts again in a new window never meets an entry of an old one.
A counter's own window runs this long from its first bump, which falls inside
the window it counts, so it outlives that window and no number comes back
within it."""

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


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
        window = _window()
        generation = await self._generation(org_id, window)
        if generation is None:
            return await load()
        key = f"{name}:{window}:{generation}"
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
        await self._cache.increment(org_id, f"{GENERATION}:{_window()}", GENERATION_WINDOW)

    async def _generation(self, org_id: UUID, window: int) -> int | None:
        """The tenant's generation in the window, zero before its first bump
        there. A value that is no count was written by nothing that bumps, and
        the read goes to the source and caches nothing."""
        held = await self._cache.get(org_id, f"{GENERATION}:{window}")
        if held is None:
            return 0
        return int(held) if held.isdigit() else None


def _window() -> int:
    """The window now falls in, as whole windows since the epoch."""
    return (utcnow() - _EPOCH) // GENERATION_WINDOW
