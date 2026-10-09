"""A provider's failure, read as the outcome of the work that called it, and
told to every other worker through the outage signal.

Read by status, as every boundary reads an exception. A 503 is "not right
now": a provider out of reach, throttling, not configured here, or
refusing the process's own key. The item parks, spending no attempt, and
waits for the provider, or for a person to fix the key. A provider that
answered and refused the request itself fails the item at once: the same
call gets the same answer (NET-33). Anything else is raised as it is, and
the queue retries it until the attempts are spent.

A 503 is news for every worker that calls the provider with the same
credential, so the call marks the pair out until its wait ends. Each call
reads the mark first: a marked pair parks the item until the mark's retry
time, with no call made. A call that answers clears the mark."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from acme.infra.base import SYSTEM_SCOPE, utcnow
from acme.infra.exceptions import InfraException
from acme.infra.outages import Outage, OutageSignalInterface
from acme.integrations.exceptions import ProviderRefused
from acme.om.exceptions import PlatformException
from acme.om.work.types.handler import WorkParked, WorkRefused

PROVIDER_WAIT = timedelta(minutes=1)
"""How long an item waits for a provider that did not answer before it asks
again, and how long the outage it marks lasts. No attempt is spent, so it
asks until the provider answers."""

UNAVAILABLE = 503
"""The status of every "not right now": a provider out of reach, or with no
credential in this process, or refusing the one it has."""


@dataclass(frozen=True)
class ProviderCalls:
    """The calls a worker makes to one provider under one credential: the org
    that holds it (`SYSTEM_SCOPE` for the platform's own) and the secret's
    name, never its value. The root builds one per pair, over the signal
    every worker on the shared cache reads."""

    outages: OutageSignalInterface
    provider: str
    credential: str
    org_id: UUID = SYSTEM_SCOPE
    wait: timedelta = PROVIDER_WAIT

    @asynccontextmanager
    async def calls(self) -> AsyncIterator[None]:
        """The calls inside end as the work should: parked, refused, or raised.
        A pair marked out parks the item before any of them is made."""
        held = await self.outages.current(self.org_id, self.provider, self.credential)
        if held is not None:
            raise WorkParked(
                f"{self.provider} is marked out until {held.retry_at.isoformat()}",
                max(held.retry_at - utcnow(), timedelta(0)),
            )
        try:
            yield
        except (InfraException, PlatformException) as error:
            if error.http_status == UNAVAILABLE:
                await self.outages.mark(
                    Outage(
                        org_id=self.org_id,
                        provider=self.provider,
                        credential=self.credential,
                        retry_at=utcnow() + self.wait,
                    )
                )
                raise WorkParked(f"a provider is out of reach: {error}", self.wait) from None
            if isinstance(error, ProviderRefused):
                raise WorkRefused(f"a provider refused the call: {error}") from None
            raise
        await self.outages.clear(self.org_id, self.provider, self.credential)
