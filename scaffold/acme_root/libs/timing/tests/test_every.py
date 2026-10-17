"""The periodic loop on a clock that moves only when the loop sleeps or the
work runs: ticks start to start at the period, the first after its phase, a
run that outlasts its period followed by one tick, a stop at the next tick,
and the check that waits for nothing."""

import asyncio
import math
from collections.abc import Callable

import pytest

from acme.timing import Every


class Clock:
    """A clock that stands still until the loop sleeps or the work runs for a
    while. A hook set with `during_the_next_sleep` runs inside the next sleep,
    as another task would while the loop waits."""

    def __init__(self, now: float = 0.0) -> None:
        self.now = now
        self._hook: Callable[[], None] | None = None

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        assert seconds >= 0, "a wait is never negative"
        self.now += seconds
        hook, self._hook = self._hook, None
        if hook is not None:
            hook()

    def run_for(self, seconds: float) -> None:
        self.now += seconds

    def during_the_next_sleep(self, hook: Callable[[], None]) -> None:
        self._hook = hook


def every(clock: Clock, period: float, phase: float = 0.0) -> Every:
    return Every(period, phase=phase, clock=clock, sleep=clock.sleep)


async def ticks(loop: Every, clock: Clock, runs: list[float], count: int) -> list[float]:
    """The ticks of `loop` until `count` have come, each run taking the
    seconds `runs` names, in turn, and none past its end."""
    seen: list[float] = []
    async for at in loop:
        assert clock() == at, "the work starts at its tick"
        seen.append(at)
        if len(seen) == count:
            break
        clock.run_for(runs[len(seen) - 1] if len(seen) <= len(runs) else 0.0)
    return seen


async def test_ticks_fall_start_to_start_at_the_period_whatever_the_work_takes() -> None:
    clock = Clock(now=100.0)
    seen = await ticks(every(clock, 10.0), clock, [7.0, 0.0, 9.5, 3.0], 5)
    assert seen == [100.0, 110.0, 120.0, 130.0, 140.0]


async def test_the_first_tick_comes_after_its_phase_and_the_grid_runs_from_it() -> None:
    clock = Clock()
    assert await ticks(every(clock, 10.0, phase=4.0), clock, [6.0, 1.0], 3) == [4.0, 14.0, 24.0]
    clock = Clock()
    assert await ticks(every(clock, 10.0, phase=10.0), clock, [], 2) == [10.0, 20.0]


async def test_a_run_that_outlasts_its_period_is_followed_by_one_tick_at_the_next_point() -> None:
    clock = Clock()
    # The run at 10 takes 2.5 periods and ends at 35: the ticks at 20 and 30
    # are skipped, and the next falls at 40, on the grid.
    seen = await ticks(every(clock, 10.0), clock, [1.0, 25.0, 1.0], 4)
    assert seen == [0.0, 10.0, 40.0, 50.0]
    # A run that ends on a point of the grid is followed by the tick there.
    clock = Clock()
    assert await ticks(every(clock, 10.0), clock, [20.0], 3) == [0.0, 20.0, 30.0]


async def test_a_stop_ends_the_loop_at_the_next_tick_and_the_work_runs_no_more() -> None:
    clock = Clock()
    loop = every(clock, 10.0)
    seen: list[float] = []
    async for at in loop:
        seen.append(at)
        if len(seen) == 2:
            clock.during_the_next_sleep(loop.stop)
    assert seen == [0.0, 10.0]
    assert clock() == 20.0, "it ended at the tick it was waiting for"
    # A stop while the work runs ends the loop with no wait at all.
    loop = every(clock, 10.0)
    async for at in loop:
        seen.append(at)
        loop.stop()
    assert seen[2:] == [20.0] and clock() == 20.0
    async for _ in loop:
        raise AssertionError("a stopped loop does not tick again")


async def test_a_period_of_zero_runs_the_work_again_at_once() -> None:
    clock = Clock()
    assert await ticks(every(clock, 0.0), clock, [3.0, 4.0], 3) == [0.0, 3.0, 7.0]


async def test_the_loop_runs_on_the_event_loops_clock_by_default() -> None:
    loop = Every(0.0)
    async for at in loop:
        assert at <= asyncio.get_running_loop().time()
        loop.stop()


def test_due_waits_for_nothing_and_advance_skips_the_ticks_the_work_outlasted() -> None:
    clock = Clock(now=50.0)
    loop = every(clock, 300.0)
    assert loop.due(), "the first tick is due at once"
    assert loop.advance() == 350.0
    clock.now = 349.0
    assert not loop.due()
    clock.now = 350.0
    assert loop.due()
    # A failed run does not advance: the tick stays due.
    assert loop.due() and loop.upcoming() == 350.0
    clock.now = 1000.0
    assert loop.advance() == 1250.0, "the ticks at 650 and 950 are skipped"


def test_the_first_tick_is_due_at_once_on_a_clock_that_moves_on_every_read() -> None:
    reads = iter(range(100))
    loop = Every(300.0, clock=lambda: float(next(reads)))
    assert loop.due()
    assert loop.advance() == 300.0, "on the grid from the first read"


@pytest.mark.parametrize(
    ("period", "phase"),
    [(-1.0, 0.0), (math.nan, 0.0), (math.inf, 0.0), (10.0, -1.0), (10.0, 10.5), (10.0, math.nan)],
)
def test_a_period_or_phase_that_is_no_grid_is_refused(period: float, phase: float) -> None:
    with pytest.raises(ValueError):
        Every(period, phase=phase)
