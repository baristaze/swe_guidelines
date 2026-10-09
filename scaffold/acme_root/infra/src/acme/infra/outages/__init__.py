"""The outage signal: what every process knows of a provider that is failing
for one credential, so that a process reads it before it calls and does not
pay for the outage on its own. It sits beside the breaker. A breaker cuts a
dependency off for the process that holds it; the signal tells every other
process at once.

A mark is keyed by the provider and the credential the call is made with:
the org that holds it (`SYSTEM_SCOPE` for the platform's own) and the
secret's name, never its value. One org's revoked key is no outage of
another's, nor of the platform's. A caller marks the pair when its calls
fail together, with the time to try again, and clears it when a call
succeeds.

One process needs no signal: its own breaker already holds what it learned,
so the null impl never marks. Processes that share a provider share one
signal, on the shared cache, and it fails open as the cache does: a cache
that cannot answer is a pair with no mark, and the provider's own errors
still stop the caller."""

from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from pydantic import Field

from acme.infra.base import InfraModel

MAX_NAME = 200


class Outage(InfraModel):
    """A provider known to be failing for one credential until `retry_at`."""

    org_id: UUID  # whose credential: SYSTEM_SCOPE for the platform's own
    provider: str = Field(min_length=1, max_length=MAX_NAME)
    credential: str = Field(min_length=1, max_length=MAX_NAME)  # the secret's name
    retry_at: datetime


class OutageSignalInterface(ABC):
    @abstractmethod
    async def mark(self, outage: Outage) -> None:
        """Records that the provider is failing for the credential until
        `outage.retry_at`. A mark that ends earlier than the one held leaves
        the held one; one whose retry time has passed records nothing."""
        ...

    @abstractmethod
    async def current(self, org_id: UUID, provider: str, credential: str) -> Outage | None:
        """The mark on the pair, read before a call: None when there is none,
        its retry time has come, or the store cannot answer."""
        ...

    @abstractmethod
    async def clear(self, org_id: UUID, provider: str, credential: str) -> None:
        """A call that succeeded ends the mark on the pair."""
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
