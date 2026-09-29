"""A provider's failure, read as the outcome of the work that called it.

Read by status, as every boundary reads an exception. A 503 is "not right
now": a provider out of reach, throttling, not configured here, or
refusing the process's own key. The item parks, spending no attempt, and
waits for the provider, or for a person to fix the key. A provider that
answered and refused the request itself fails the item at once: the same
call gets the same answer (NET-33). Anything else is raised as it is, and
the queue retries it until the attempts are spent."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

from acme.infra.exceptions import InfraException
from acme.integrations.exceptions import PaymentsRefused, ProviderRefused
from acme.om.exceptions import PlatformException
from acme.om.work.types.handler import WorkParked, WorkRefused

PROVIDER_WAIT = timedelta(minutes=1)
"""How long an item waits for a provider that did not answer before it asks
again. No attempt is spent, so it asks until the provider answers."""

UNAVAILABLE = 503
"""The status of every "not right now": a provider out of reach, or with no
credential in this process, or refusing the one it has."""


@asynccontextmanager
async def provider_calls() -> AsyncIterator[None]:
    """The calls inside end as the work should: parked, refused, or raised."""
    try:
        yield
    except (InfraException, PlatformException) as error:
        if error.http_status == UNAVAILABLE:
            raise WorkParked(f"a provider is out of reach: {error}", PROVIDER_WAIT) from None
        if isinstance(error, ProviderRefused | PaymentsRefused):
            raise WorkRefused(f"a provider refused the call: {error}") from None
        raise
