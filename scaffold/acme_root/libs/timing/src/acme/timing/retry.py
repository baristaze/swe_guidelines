"""The wait between attempts: one growing, capped curve as a value, one
loop's count of failures over it, and the reader of a server's
`Retry-After`. Values in, values out; the randomness and the clock are
handed in where a test needs them fixed."""

import math
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

Draw = Callable[[], float]
"""A source of jitter: a function of no arguments answering in [0, 1]."""


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """A wait that starts at `base` seconds, grows by `growth` per attempt,
    and stops at `cap`. `jitter` is the share of each wait drawn at random:
    0 draws none, 0.5 half, 1 all of it. Callers that failed together for a
    shared reason do not return together when part of the wait is drawn,
    and with half of it drawn the shortest wait of one attempt is still the
    longest of the one before it wherever the curve doubles."""

    base: float
    cap: float
    growth: float = 2.0
    jitter: float = 0.0

    def __post_init__(self) -> None:
        values = (self.base, self.cap, self.growth, self.jitter)
        if not all(math.isfinite(value) for value in values):
            raise ValueError(f"a retry policy is finite: {self!r}")
        if self.base < 0 or self.cap < 0:
            raise ValueError(f"a wait is never negative: {self!r}")
        if self.growth < 1:
            raise ValueError(f"a wait never shrinks between attempts: {self!r}")
        if not 0 <= self.jitter <= 1:
            raise ValueError(f"the jitter is a share, between 0 and 1: {self!r}")

    def steps_to_cap(self) -> int:
        """How many times the wait grows before it reaches the cap; from that
        step on, every wait is the cap. The curve raises `growth` to no power
        past it, so no attempt count overflows, and a store that spells the
        curve in its own query bounds its exponent by it the same way."""
        if self.base == 0 or self.base >= self.cap or self.growth == 1:
            return 0
        ratio = (math.log(self.cap) - math.log(self.base)) / math.log(self.growth)
        steps = max(0, math.ceil(ratio))
        # The logarithms are approximate; the powers decide the step exactly.
        while steps > 0 and self.base * self.growth ** (steps - 1) >= self.cap:
            steps -= 1
        while self.base * self.growth**steps < self.cap:
            steps += 1
        return steps

    def wait(
        self, attempt: int, *, retry_after: float | None = None, draw: Draw = random.random
    ) -> float:
        """The wait in seconds before retry `attempt` (1 is the first; less
        is read as 1): `base * growth ** (attempt - 1)` up to `cap`, with the
        `jitter` share of it drawn from `draw`, and a server's `retry_after`
        as its floor."""
        exponent = min(max(0, attempt - 1), self.steps_to_cap())
        curve = min(self.base * self.growth**exponent, self.cap)
        drawn = curve * self.jitter
        return self.bound(curve - drawn + drawn * draw(), retry_after)

    def bound(self, wait: float, retry_after: float | None = None) -> float:
        """`wait`, or longer when the server asked for longer, and never past
        the cap: an ask for minutes does not hold a caller that long."""
        return min(max(wait, retry_after or 0.0), self.cap)


class Retries:
    """One loop's count of failures over a policy: each failure counts one
    more and gives the wait before the next try, and a success starts the
    count over."""

    def __init__(self, policy: RetryPolicy, *, draw: Draw = random.random) -> None:
        self.policy = policy
        self._draw = draw
        self._failures = 0

    @property
    def failures(self) -> int:
        """The failures since the last reset; 0 before the first."""
        return self._failures

    def failed(self, retry_after: float | None = None) -> float:
        """Counts one more failure and gives the wait before the next try."""
        self._failures += 1
        return self.policy.wait(self._failures, retry_after=retry_after, draw=self._draw)

    def reset(self) -> None:
        """Starts the count over, after a success."""
        self._failures = 0


def retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """A server's `Retry-After` in seconds: a count of whole seconds, or an
    HTTP date read against `now`, an aware time, the current one unless
    handed in. A date already past asks for no wait. A value it cannot read
    is no ask at all, `None`, never an error: the header is the server's to
    get wrong."""
    if value is None:
        return None
    text = value.strip()
    if text.isascii() and text.isdigit():
        return float(text)
    try:
        when = parsedate_to_datetime(text)
    except TypeError, ValueError, OverflowError:
        return None
    if when.tzinfo is None:
        # RFC 5322's `-0000`: a time in UTC whose source zone is unknown.
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - (now or datetime.now(UTC))).total_seconds())
