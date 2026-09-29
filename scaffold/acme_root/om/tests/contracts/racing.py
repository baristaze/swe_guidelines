"""Two callers at one guard, and what a run of such a case proves.

The guard is the same in both impls, a conditional write: a claim token, a
unique key, a compare-and-set on an attempt, a per-tenant cursor. What a run
exercises is not.

Under the engine the callers overlap. Each one suspends on its socket inside
the statement, so both are in the guard at once and the case proves the guard
against a real race.

Under a memory impl they do not overlap. Every conditional write there reads
and writes under one lock with no await in between, which is the impl's whole
answer to concurrency, so the event loop runs one caller to its end before it
starts the other. A case that only counts winners would read as a race the
memory run never ran. What the memory run proves is the conditional write
itself: the caller that arrives second is refused against the state the first
left, and the case says so by asserting the refusal that follows the race.

`race` runs the callers and reports which of the two happened, so a case
states the difference instead of hiding it, and a contract case never reaches
for `asyncio.gather` itself.
"""

import asyncio
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Race[T]:
    outcomes: list[T]
    overlapped: bool
    """True when two callers were inside the guard at once, which is the
    engine; False when the impl answered each without an await, which is the
    memory twin and means the run proved the conditional write, not a race."""

    @property
    def admitted(self) -> list[T]:
        """The callers the guard let through. A guard admits exactly one."""
        return [outcome for outcome in self.outcomes if outcome is not None]

    def summary(self) -> str:
        """Which of the two runs this was, so a failure says it outright."""
        overlap = "overlapped" if self.overlapped else "never overlapped"
        return f"{len(self.outcomes)} callers, {overlap}, {len(self.admitted)} admitted"


async def race[T](*callers: Coroutine[Any, Any, T]) -> Race[T]:
    """Start every caller at once and report what the run was."""
    in_flight = 0
    overlapped = False

    async def counted(caller: Coroutine[Any, Any, T]) -> T:
        nonlocal in_flight, overlapped
        in_flight += 1
        overlapped = overlapped or in_flight > 1
        try:
            return await caller
        finally:
            in_flight -= 1

    outcomes = await asyncio.gather(*(counted(caller) for caller in callers))
    return Race(list(outcomes), overlapped)
