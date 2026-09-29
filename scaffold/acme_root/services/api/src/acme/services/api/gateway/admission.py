"""Admission: the bounds on what this process has in flight, and the refusal
past them. It is not the rate limit beside it, and the two fail in opposite
directions. A rate limit is fairness between subjects, counted in the shared
cache, and fails open. Admission is this process defending itself, counted
here in its own memory, and fails closed: past a bound a request is refused
at once, so a saturated process answers and says why instead of queueing work
it cannot start and dying with every caller still waiting.

The bound is two bounds, one lane for reads and one for writes, so that a
storm in one lane cannot take every slot from the other: a client back from
an outage replays its backlog with reads, and the commands behind it are
still admitted.

A request admitted also gets its deadline: the instant, counted from when it
takes its slot, that every call it makes outside the process shares (ADR
0069). The count bounds how many requests hold a slot; the deadline bounds how
long a provider that hangs keeps one."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from acme.infra.base import utcnow
from acme.infra.observability import OUTCOMES
from acme.om.exceptions import Unavailable
from acme.services.api.gateway.envelope import error_response
from acme.services.api.gateway.observability import (
    ASGIApp,
    Receive,
    Scope,
    Send,
    request_id_of,
)

UNBOUNDED_PATHS = frozenset({"/healthz", "/readyz", "/metrics"})
"""The three operational routes `app.py` declares outside the versioned API,
which admission never refuses, in either lane: a saturated process must still
be able to say that it is saturated, and the collector must still be able to
read the counter that says by how much. None of them does tenant work,
liveness does no I/O at all, and readiness carries a deadline of its own. The
paths are matched as they arrive, because routing has not run this far out."""

DEADLINE_KEY = "deadline"
"""Where the admitted request's deadline rides in the scope's state, beside
its request id, until the gateway mints the request stage from both."""


def deadline_of(scope: Scope) -> datetime | None:
    """The deadline admission gave the request; None for what admission lets
    through uncounted: a socket, and the operational routes."""
    return scope.get("state", {}).get(DEADLINE_KEY)


READ_METHODS = frozenset({"GET", "HEAD"})
"""The read lane's methods. Everything else is a write, the method a caller
invents included: a method this process does not know is not the one to give
the cheaper budget to. The method is read from the scope, because routing has
not run this far out either."""


@dataclass
class Lane:
    """One budget: the traffic it counts, its bound, and what it has in
    flight. The name is what the refusal says, and under `admission_` it is
    the subsystem the counter carries: `OUTCOMES` has the two labels every
    subsystem in the repository shares, so a lane is a subsystem of its own
    rather than a third label on a counter the whole repository reads."""

    name: str
    limit: int
    in_flight: int = 0

    @property
    def subsystem(self) -> str:
        return f"admission_{self.name}"


class AdmissionMiddleware:
    """Counts the HTTP requests in flight, reads apart from writes, and
    refuses past either bound with the unavailable shape and a `Retry-After`.
    A request it admits carries its deadline from there on.
    A count is a plain integer: one event loop owns it, and it is read and
    written with no await in between, so nothing interleaves. A socket is not
    counted: it is held open for as long as its subscriber wants it, and a
    bound on requests in flight that a long-lived connection can fill is not
    a bound on requests."""

    def __init__(
        self,
        app: ASGIApp,
        limit_reads: int,
        limit_writes: int,
        retry_after: timedelta,
        deadline: timedelta,
    ) -> None:
        self.app = app
        self.reads = Lane("reads", limit_reads)
        self.writes = Lane("writes", limit_writes)
        self.retry_after = str(max(1, int(retry_after.total_seconds())))
        self.deadline = deadline

    def lane_of(self, scope: Scope) -> Lane:
        return self.reads if scope["method"] in READ_METHODS else self.writes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in UNBOUNDED_PATHS:
            await self.app(scope, receive, send)
            return
        lane = self.lane_of(scope)
        if lane.in_flight >= lane.limit:
            OUTCOMES.labels(subsystem=lane.subsystem, outcome="refused").inc()
            await self.refuse(lane, scope, receive, send)
            return
        OUTCOMES.labels(subsystem=lane.subsystem, outcome="admitted").inc()
        scope.setdefault("state", {})[DEADLINE_KEY] = utcnow() + self.deadline
        lane.in_flight += 1
        try:
            await self.app(scope, receive, send)
        finally:
            lane.in_flight -= 1

    async def refuse(self, lane: Lane, scope: Scope, receive: Receive, send: Send) -> None:
        """The refusal is `Unavailable`, presented in the one error envelope,
        and it names the lane that is full, so an operator reading it knows
        which half of the process is saturated. A middleware sits outside the
        handler that translates an exception raised in a route, so the refusal
        is rendered here from the same exception and the same envelope the
        handler would have used, and the status and the code are read off the
        shape rather than written again. The `Retry-After` is the one
        `RateLimited` gets, for the same reason: a client that is told to come
        back is told when."""
        refusal = Unavailable(f"the process is at its bound of {lane.name} in flight")
        response = error_response(
            request_id_of(scope),
            refusal.http_status,
            refusal.code,
            refusal.message,
            {"Retry-After": self.retry_after},
        )
        await response(scope, receive, send)
