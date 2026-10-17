"""Work on a fixed period, start to start: one grid of ticks from the first,
a run that outlasts its period followed by the next tick after it ends, and
a stop that ends the loop at the next tick. The clock and the sleep are
handed in where a test needs them fixed."""

import asyncio
import math
from collections.abc import Awaitable, Callable
from typing import Self

Clock = Callable[[], float]
"""A monotonic clock: a function of no arguments answering in seconds."""

Sleep = Callable[[float], Awaitable[object]]
"""A wait of the seconds handed in."""


def loop_time() -> float:
    """The running event loop's clock: the one `asyncio.sleep` waits on."""
    return asyncio.get_running_loop().time()


class Every:
    """Ticks every `period` seconds, start to start, on one grid from the
    first tick, which comes `phase` seconds after the loop starts: 0 at
    once, or a value drawn within the period for loops that start together
    and should not run together. The time the work takes moves no tick, so
    a heartbeat of 10 s whose write takes 7 s still beats every 10 s. A run
    that outlasts one or more periods is followed by the first tick of the
    grid not before it ends: the ticks it missed are skipped, never run in a
    burst to catch up. A period of 0 runs the work again at once.

        async for _ in Every(10.0):
            await beat()

    Work that runs inside another loop, as a step of its pass, asks `due()`
    instead, and calls `advance()` once it has run."""

    def __init__(
        self,
        period: float,
        *,
        phase: float = 0.0,
        clock: Clock = loop_time,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if not (math.isfinite(period) and math.isfinite(phase)):
            raise ValueError(f"a period and its phase are finite: {period!r}, {phase!r}")
        if period < 0:
            raise ValueError(f"a period is never negative: {period!r}")
        if not 0 <= phase <= period:
            raise ValueError(f"the first tick comes within one period: {phase!r} of {period!r}")
        self.period = period
        self.phase = phase
        self._clock = clock
        self._sleep = sleep
        # The first tick on the clock, set when the loop starts, and the
        # place of the upcoming one on the grid from it.
        self._origin: float | None = None
        self._index = 0
        self._ticked = False
        self._stopped = False

    def upcoming(self) -> float:
        """The upcoming tick on the clock. The first call starts the loop."""
        if self._origin is None:
            self._origin = self._clock() + self.phase
        return self._origin + self._index * self.period

    def due(self) -> bool:
        """True once the clock has reached the upcoming tick. It waits for
        nothing."""
        upcoming = self.upcoming()
        return self._clock() >= upcoming

    def advance(self) -> float:
        """Moves past the tick that came, once its work has run, to the first
        tick of the grid not before now, and gives it."""
        self.upcoming()
        self._index += 1
        now = self._clock()
        behind = now - self.upcoming()
        if behind > 0 and self.period > 0:
            self._index += math.ceil(behind / self.period)
        elif behind > 0:
            # A grid of no period is one point: it moves to now.
            self._origin = now
        return self.upcoming()

    def stop(self) -> None:
        """Ends the loop at the next tick, or at once when it is not waiting
        for one: the work does not run again."""
        self._stopped = True

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> float:
        """Waits for the next tick and gives its time on the clock."""
        if self._stopped:
            raise StopAsyncIteration
        tick = self.advance() if self._ticked else self.upcoming()
        self._ticked = True
        await self._sleep(max(0.0, tick - self._clock()))
        if self._stopped:
            raise StopAsyncIteration
        return tick
