"""The outage signal on the shared cache: one entry per pair, under the org
that holds the credential, living until the mark's retry time. Every process
on the same cache reads the same marks.

It fails open, as the cache does: a cache that cannot be reached is a miss,
and a miss is a pair with no mark."""

from datetime import timedelta
from urllib.parse import quote
from uuid import UUID

from pydantic import ValidationError

from acme.infra.base import utcnow
from acme.infra.cache import CacheInterface
from acme.infra.outages import Outage, OutageSignalInterface

LEAST_TTL = timedelta(milliseconds=1)


def outage_key(provider: str, credential: str) -> str:
    """One key per pair. Each part is quoted, so no credential's name can
    reach another pair's key by holding the separator."""
    return f"outage:{quote(provider, safe='')}:{quote(credential, safe='')}"


class OutageSignalCacheImpl(OutageSignalInterface):
    def __init__(self, cache: CacheInterface) -> None:
        self._cache = cache

    async def mark(self, outage: Outage) -> None:
        now = utcnow()
        if outage.retry_at <= now:
            return
        # A read, then a write: two marks that race may leave the earlier
        # retry time of the two. That costs one early call to a provider still
        # failing, which marks it again; it never holds a caller back longer.
        held = await self.current(outage.org_id, outage.provider, outage.credential)
        if held is not None and held.retry_at >= outage.retry_at:
            return
        ttl = max(outage.retry_at - now, LEAST_TTL)
        key = outage_key(outage.provider, outage.credential)
        await self._cache.put(outage.org_id, key, outage.model_dump_json().encode(), ttl)

    async def current(self, org_id: UUID, provider: str, credential: str) -> Outage | None:
        value = await self._cache.get(org_id, outage_key(provider, credential))
        if value is None:
            return None
        try:
            outage = Outage.model_validate_json(value)
        except ValidationError:
            return None  # written by nothing that marks: no mark
        if (outage.org_id, outage.provider, outage.credential) != (org_id, provider, credential):
            return None
        return outage if utcnow() < outage.retry_at else None

    async def clear(self, org_id: UUID, provider: str, credential: str) -> None:
        await self._cache.invalidate(org_id, outage_key(provider, credential))

    def describe(self) -> str:
        return f"outages=shared({self._cache.describe()})"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None
