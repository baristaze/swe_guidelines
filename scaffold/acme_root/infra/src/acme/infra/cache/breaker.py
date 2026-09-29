"""The cache breaker: the cost bound in front of a cache that lives out of
process. It is a decoration like every other one here, an impl of
`CacheInterface` holding an inner impl of the same interface, and it changes
when an answer arrives, never what the answer is.

An open breaker answers the way the dependency's own failure answers, because
a caller holding a `CacheInterface` cannot tell what is behind it. A cache
fails open, so here that answer is not an exception: `get` is a miss, `put` and
`invalidate` are dropped, and `increment` is the count no count, which is what
the login rate limit reads as fail open. Each is what the Valkey impl answers
for a backend it cannot reach, given at once and without going out, so nothing
above can tell an open breaker from a backend that is down. Raising the
unavailable shape here would turn a Valkey that is down into a 503 on the login
route, a refusal the caller was written not to get, which is the outage the
miss exists to prevent. The breaker declines to pay the timeout, never to keep
the contract.

A refusal is counted as a refusal and never as a miss, because nothing was
looked up: a miss counted for a lookup that never happened would report the
backend as answering when it was never asked. The hit and miss counters go
quiet while the breaker is open, and the breaker's own counter is what says
why.

`start()` and `close()` are the root's lifecycle and not calls on the
dependency, so they are forwarded whatever the breaker's state is.
"""

from datetime import timedelta
from uuid import UUID

from acme.infra.breaker import Breaker
from acme.infra.cache import CacheInterface


class CacheBreakerImpl(CacheInterface):
    def __init__(self, inner: CacheInterface, breaker: Breaker) -> None:
        self._inner = inner
        self._breaker = breaker

    async def get(self, org_id: UUID, key: str) -> bytes | None:
        if not self._breaker.allows():
            return None
        with self._breaker.measured():
            return await self._inner.get(org_id, key)

    async def put(self, org_id: UUID, key: str, value: bytes, ttl: timedelta) -> None:
        if not self._breaker.allows():
            return None
        with self._breaker.measured():
            await self._inner.put(org_id, key, value, ttl)

    async def invalidate(self, org_id: UUID, key: str) -> None:
        """Dropped while open, and the entry it would have removed stays
        readable until its TTL. That is the bound on staleness the caller
        already accepts, and it is what a backend that cannot be reached
        leaves behind anyway; the breaker only declines to pay for it."""
        if not self._breaker.allows():
            return None
        with self._breaker.measured():
            await self._inner.invalidate(org_id, key)

    async def increment(self, org_id: UUID, key: str, ttl: timedelta) -> tuple[int, timedelta]:
        """The count no count, and the window asked for: the same pair the
        Valkey impl returns when it cannot reach the backend, so a rate limit
        over an open breaker allows exactly as it does over a backend that is
        down, and a caller cannot tell the two apart."""
        if not self._breaker.allows():
            return 0, ttl
        with self._breaker.measured():
            return await self._inner.increment(org_id, key, ttl)

    def describe(self) -> str:
        return f"{self._inner.describe()}+{self._breaker.describe()}"

    async def start(self) -> None:
        await self._inner.start()

    async def close(self) -> None:
        await self._inner.close()
