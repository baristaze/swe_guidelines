from datetime import datetime, timedelta
from uuid import UUID

from acme.infra.base import utcnow
from acme.infra.cache import CacheInterface, CacheScope, cache_key
from acme.infra.observability import OUTCOMES


class CacheMemoryImpl(CacheInterface):
    """One map of values, counters among them, as in Valkey: a counter is a
    value `get` reads, and `put` or `invalidate` of its key replaces it."""

    def __init__(self, scope: CacheScope) -> None:
        self._scope = scope
        self._values: dict[str, tuple[bytes, datetime]] = {}

    async def get(self, org_id: UUID, key: str) -> bytes | None:
        value = self._lookup(cache_key(org_id, key))
        OUTCOMES.labels(subsystem="cache", outcome="miss" if value is None else "hit").inc()
        return value

    def _lookup(self, full_key: str) -> bytes | None:
        entry = self._values.get(full_key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at <= utcnow():
            self._values.pop(full_key, None)
            return None
        return value

    async def put(self, org_id: UUID, key: str, value: bytes, ttl: timedelta) -> None:
        self._values[cache_key(org_id, key)] = (value, utcnow() + ttl)

    async def invalidate(self, org_id: UUID, key: str) -> None:
        self._values.pop(cache_key(org_id, key), None)

    async def increment(self, org_id: UUID, key: str, ttl: timedelta) -> tuple[int, timedelta]:
        now = utcnow()
        full_key = cache_key(org_id, key)
        entry = self._values.get(full_key)
        if entry is None or entry[1] <= now:
            self._values[full_key] = (b"1", now + ttl)
            return 1, ttl
        value, expires_at = entry
        count = int(value) + 1
        self._values[full_key] = (str(count).encode(), expires_at)
        return count, expires_at - now

    def describe(self) -> str:
        return f"cache[{self._scope.value}]=memory"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None
