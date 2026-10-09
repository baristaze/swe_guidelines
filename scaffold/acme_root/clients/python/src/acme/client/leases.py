"""The holder's side of a lease: its clock and the resource's fence.

The clock runs on the holder's monotonic clock, from the moment it sent the
ask or the renewal. The server's instant is on another clock, so the lease's
seconds count from the send, and the time the answer took only shortens
them. It renews at half of what is left. Once the seconds run out, or a
renewal is refused, the lease is lost, and the holder stops acting on the
resource. The server ends a lease only a skew margin after its expiry, so a
holder whose clock runs slow has stopped before anyone else is granted.

The fence sits on the resource's own side, in front of whatever acts on it.
It keeps the highest token it has admitted per resource. A lower token is a
stale holder's, and is refused. An equal one is the current holder's. A
higher one is a new holder's: the stop-and-reset the caller hands it runs
first, so the resource is at a known state before the new holder acts. A
stop-and-reset that raises admits nothing and keeps the old token, so the
next admit runs it again."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from uuid import UUID

MIN_RENEW_SECONDS = 1.0
"""The shortest wait before a renewal, so a lease near its end is not renewed
in a tight loop."""


@dataclass
class LeaseClock:
    """One lease's time on the holder's monotonic clock (`time.monotonic`)."""

    deadline: float
    renew_at: float
    refused: bool = False

    @classmethod
    def granted(cls, asked: float, expires_in_seconds: float) -> LeaseClock:
        """The clock of a lease granted to an ask sent at `asked`, with the
        seconds the answer gave it."""
        clock = cls(deadline=asked, renew_at=asked)
        clock.renewed(asked, expires_in_seconds)
        return clock

    def renewed(self, asked: float, expires_in_seconds: float) -> None:
        """A renewal sent at `asked` was answered with these seconds."""
        self.deadline = asked + expires_in_seconds
        self.renew_at = asked + max(MIN_RENEW_SECONDS, expires_in_seconds / 2)

    def unanswered(self, now: float) -> None:
        """A renewal got no answer: the deadline stays, and the next try comes
        at half of what is left."""
        self.renew_at = now + max(MIN_RENEW_SECONDS, (self.deadline - now) / 2)

    def refuse(self) -> None:
        """A renewal was refused: the lease is gone."""
        self.refused = True

    def due(self, now: float) -> bool:
        return not self.lost(now) and now >= self.renew_at

    def lost(self, now: float) -> bool:
        return self.refused or now >= self.deadline


StopAndReset = Callable[[], Awaitable[None]]
"""Stops whatever runs on the resource and brings it to a known state."""


class Fence:
    """The highest token admitted per resource. `highest` restores what a
    process kept, and `snapshot` hands it back to keep."""

    def __init__(self, highest: Mapping[UUID, int] | None = None) -> None:
        self._highest: dict[UUID, int] = dict(highest or {})
        self._locks: dict[UUID, asyncio.Lock] = {}

    def highest(self, resource_id: UUID) -> int:
        return self._highest.get(resource_id, 0)

    def snapshot(self) -> dict[UUID, int]:
        return dict(self._highest)

    async def admit(self, resource_id: UUID, token: int, stop_and_reset: StopAndReset) -> bool:
        """Whether a holder under `token` may act on the resource. Admits run
        one at a time per resource, so two new holders never reset it at once."""
        lock = self._locks.setdefault(resource_id, asyncio.Lock())
        async with lock:
            highest = self.highest(resource_id)
            if token < highest:
                return False
            if token > highest:
                await stop_and_reset()
                self._highest[resource_id] = token
            return True
