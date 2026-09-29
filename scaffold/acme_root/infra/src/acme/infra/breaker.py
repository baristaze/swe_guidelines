"""The breaker: the bound on what a dependency that is down costs the process
that calls it. A dependency that cannot be reached answers every call with its
whole timeout, and the timeouts alone are what exhaust the pool the calls are
made from. The breaker counts the calls that spend the timeout, refuses at once
for a cool-down once they pass the bound from settings, and then lets one call
through to decide whether to close again.

What it counts is the cost, not the error. A call that fails at once costs its
caller nothing and needs no breaker; a call that spends the timeout is the one
harm this exists to prevent, whatever the impl behind it then returned. So the
signal is the duration of the call against the timeout the client is built
with, which every impl has and none has to report.

The state machine lives here, free of any one capability's interface, and a
decoration over that interface holds it (`cache/breaker.py`). One breaker
stands for one dependency and every impl that talks to that dependency holds
the same instance, so a backend that is down opens it once for all of them
rather than once for each.

Four outcomes are counted under the breaker's subsystem: `opened` when it
opens, `refused` once per call it refuses, `probed` for the one call it lets
through after a cool-down, and `closed` when that call comes back in time.
"""

import asyncio
import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import timedelta

from acme.infra.observability import OUTCOMES

log = logging.getLogger(__name__)


class Breaker:
    """One dependency's breaker, shared by every impl that talks to it.

    Nothing here locks. Infra runs on one event loop per process, and each
    transition below is a run of plain assignments with no await inside it, so
    two calls in flight cannot interleave within one transition.
    """

    def __init__(
        self,
        subsystem: str,
        *,
        failures: int,
        cooldown: timedelta,
        slow: timedelta,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """`subsystem` is the counter label, and the values are written at the
        wire-up sites, so the label stays bounded. `slow` is the timeout the
        inner client is built with: a call that reaches it is a call that timed
        out, because this measurement brackets the driver's own."""
        self._subsystem = subsystem
        self._failures = failures
        self._cooldown = cooldown.total_seconds()
        self._slow = slow.total_seconds()
        self._clock = clock
        self._consecutive = 0
        self._opened_at: float | None = None
        self._probing = False

    @property
    def is_open(self) -> bool:
        return self._opened_at is not None

    def allows(self) -> bool:
        """Whether a call may go out, and the transition that answering makes.
        A refusal is counted here, so every refusal is counted once and in one
        place. The first call after the cool-down is the probe, and it goes out
        alone: everything else waits until it answers, so a dependency that is
        still down is asked by one call and not by all of them."""
        if self._opened_at is None:
            return True
        if self._probing or self._clock() - self._opened_at < self._cooldown:
            self._count("refused")
            return False
        self._probing = True
        self._count("probed")
        log.info("%s lets one call through after %gs", self._subsystem, self._cooldown)
        return True

    @contextmanager
    def measured(self) -> Iterator[None]:
        """Brackets a call that `allows()` let through and records what it
        cost. A call cancelled before it spent the timeout (its caller left,
        or an outer deadline passed) says nothing about the dependency: it is
        neither a success that closes the breaker nor a failure, and a probe
        cancelled that way frees the slot for the next call to probe. One
        cancelled after the timeout still counts as slow."""
        started = self._clock()
        try:
            yield
        except asyncio.CancelledError:
            if self._clock() - started >= self._slow:
                self._failed()
            else:
                self._probing = False
            raise
        except BaseException:
            self._record(started)
            raise
        self._record(started)

    def _record(self, started: float) -> None:
        """The outcome of a call that came back, read as what it cost."""
        if self._clock() - started >= self._slow:
            self._failed()
        else:
            self._succeeded()

    def describe(self) -> str:
        return f"breaker({self._failures}/{self._cooldown:g}s)"

    def _failed(self) -> None:
        self._probing = False
        self._consecutive += 1
        if self._opened_at is not None:
            self._opened_at = self._clock()
            log.warning("%s stays open: the call it let through spent the timeout", self._subsystem)
            return
        if self._consecutive < self._failures:
            return
        self._opened_at = self._clock()
        self._count("opened")
        log.warning(
            "%s is open after %d calls in a row that spent the timeout; refusing for %gs",
            self._subsystem,
            self._consecutive,
            self._cooldown,
        )

    def _succeeded(self) -> None:
        self._probing = False
        self._consecutive = 0
        if self._opened_at is None:
            return
        self._opened_at = None
        self._count("closed")
        log.info("%s is closed again", self._subsystem)

    def _count(self, outcome: str) -> None:
        OUTCOMES.labels(subsystem=self._subsystem, outcome=outcome).inc()
