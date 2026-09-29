"""A request's deadline: the instant its time runs out, which every call it
makes to something outside the process shares (ADR 0069).

The gateway stamps it on the request stage, the caller hands it to each
call, and the client that makes the call keeps to it with `bounded`. A call
made with none (a worker's, bounded by its item's lease; a boot's) waits
only for its own timeout and retries.

It is an instant, not a duration, so calls made one after another share
what is left instead of each starting a budget of its own."""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime

from acme.infra.base import utcnow
from acme.infra.exceptions import BackendUnreachable, InfraUnavailable

PASSED = "the request's deadline passed"
"""The reason a call gives when it ran out of the request's time."""


class DeadlineReached(InfraUnavailable):
    """Raised inside a bounded call by what can tell before it waits that the
    wait does not fit in the time left: a provider asking to be called again
    later than the deadline. `bounded` turns it into the call's own refusal,
    so the call ends now instead of at the deadline, and it never leaves
    `bounded` as itself."""


def unreachable(backend: str, operation: str) -> Callable[[str], BackendUnreachable]:
    """The refusal of a backend's call that ran out of the request's time: it
    did not answer in time, as when its own timeout ends the call."""
    return lambda reason: BackendUnreachable(backend, operation, reason)


def seconds_left(deadline: datetime | None) -> float | None:
    """What is left before the deadline, in seconds, never below zero; None
    for a call made with no deadline."""
    if deadline is None:
        return None
    return max(0.0, (deadline - utcnow()).total_seconds())


@asynccontextmanager
async def bounded(
    deadline: datetime | None, refused: Callable[[str], Exception]
) -> AsyncIterator[None]:
    """Runs the block by the deadline at most. A block that starts with no
    time left does not start. A block still waiting at the deadline, on an
    attempt, on the wait between two, or on a wait the provider asked for, is
    cancelled there. Either way the block ends in `refused(reason)`, the
    exception the call already raises when its provider does not answer, so a
    caller decides on a deadline the way it decides on a timeout.

    What the block raises before the deadline, a timeout of the client inside
    it among it, is left as it is."""
    left = seconds_left(deadline)
    if left is None:
        yield
        return
    if left <= 0:
        raise refused(PASSED)
    timer = asyncio.timeout(left)
    try:
        async with timer:
            yield
    except DeadlineReached as reached:
        raise refused(str(reached)) from None
    except Exception:
        # The cancellation arrives as a TimeoutError, or as whatever a
        # library inside made of it; either way the deadline ended the block.
        if not timer.expired():
            raise
        raise refused(PASSED) from None
