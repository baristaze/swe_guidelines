"""Cache: fast reads against data that is expensive to fetch. Scoped so
unrelated consumers do not step on each other's keys; fails open."""

from abc import ABC, abstractmethod
from datetime import timedelta
from enum import StrEnum
from uuid import UUID

from acme.infra.base import SYSTEM_SCOPE


class CacheScope(StrEnum):
    NETWORK_RESPONSE = "network_response"
    OUTAGE = "outage"  # the outage signal: a provider failing for one credential
    RATE_LIMIT = "rate_limit"
    REALTIME_TICKET = "realtime_ticket"
    WORKER_LIVENESS = "worker_liveness"


def cache_key(org_id: UUID, key: str) -> str:
    """System keys and tenant keys live in disjoint namespaces."""
    if org_id == SYSTEM_SCOPE:
        return f"system:{key}"
    return f"org:{org_id}:{key}"


class CacheInterface(ABC):
    @abstractmethod
    async def get(self, org_id: UUID, key: str) -> bytes | None: ...

    @abstractmethod
    async def put(self, org_id: UUID, key: str, value: bytes, ttl: timedelta) -> None: ...

    @abstractmethod
    async def invalidate(self, org_id: UUID, key: str) -> None: ...

    @abstractmethod
    async def increment(self, org_id: UUID, key: str, ttl: timedelta) -> tuple[int, timedelta]:
        """The one atomic primitive: the new count and the time left on the
        window. The window starts with the first count and is not moved by
        the next. Until it ends, `get` of the same key answers the count, in
        decimal ASCII: that is how a generation is read."""
        ...

    @abstractmethod
    def describe(self) -> str: ...

    @abstractmethod
    async def start(self) -> None:
        """Opened by the infra root at boot. An impl that holds no connection
        of its own returns None."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Closed by the infra root at shutdown, in reverse order of start."""
        ...
