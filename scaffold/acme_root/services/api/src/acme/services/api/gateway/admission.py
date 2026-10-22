"""Admission: the bounds on what this process has in flight, and the refusal
past them. It is not the rate limit beside it, and the two fail in opposite
directions. A rate limit is fairness between subjects, counted in the shared
cache, and fails open. Admission is this process defending itself, counted
here in its own memory, and fails closed: past a bound a request is refused
at once, so a saturated process answers and says why instead of queueing work
it cannot start and dying with every caller still waiting.

The bound is three bounds, one lane each for reads, for writes, and for a
holder's calls, so that a storm in one lane cannot take every slot from
another. A client back from an outage replays its backlog with reads, and the
commands behind it are still admitted. A slowdown that fills the write lane
still admits the renewal that keeps a lease. Admitted, the renewal runs marked
a holder's call (`acme.om.storage.reserve`), so a pool the writes have filled
serves it from its reserve.

A request admitted also gets its deadline: the instant, counted from when it
takes its slot, that every call it makes outside the process shares (ADR
0069). The count bounds how many requests hold a slot; the deadline bounds how
long a provider that hangs keeps one."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import Depends
from fastapi.routing import iter_route_contexts
from starlette.routing import BaseRoute

from acme.infra.base import utcnow
from acme.infra.observability import OUTCOMES
from acme.om.exceptions import Unavailable
from acme.om.storage.reserve import as_holder_call
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
which admission never refuses, in any lane: a saturated process must still
be able to say that it is saturated, and the collector must still be able to
read the counter that says by how much. None of them does tenant work,
liveness does no I/O at all, and readiness carries a deadline of its own. The
paths are matched as they arrive, because routing has not run this far out."""

DEADLINE_KEY = "deadline"
"""Where the admitted request's deadline rides in the scope's state, beside
its request id, until the gateway mints the request stage from both."""

LANE_KEY = "lane"
"""Where the name of the lane that admitted the request rides in the scope's
state. Its credential spends the budget of the same name (`ratelimit.py`), so
the one decision that put a request in a lane also picks what it spends."""


def deadline_of(scope: Scope) -> datetime | None:
    """The deadline admission gave the request; None for what admission lets
    through uncounted: a socket, and the operational routes."""
    return scope.get("state", {}).get(DEADLINE_KEY)


READ_METHODS = frozenset({"GET", "HEAD"})
"""The read lane's methods. Everything else is a write, the method a caller
invents included: a method this process does not know is not the one to give
the cheaper budget to. The method is read from the scope, because routing has
not run this far out either."""


def lane_name_of(scope: Scope) -> str:
    """The lane admission counted the request in, and so the budget its
    credential spends; the method's lane for what admission did not count."""
    lane = scope.get("state", {}).get(LANE_KEY)
    return lane or ("reads" if scope["method"] in READ_METHODS else "writes")


async def holder_call() -> None:
    """The mark of a holder's call, in a route's dependencies:
    `dependencies=[HOLDER_CALL]`. A holder keeps what it holds only while
    these calls get through: a lease's renewal and its release, a heartbeat,
    a work item's result, a report on the work. Admission counts a route that
    carries the mark in the holds lane, its credential spends the holds
    budget, and every connection it checks out may come from the pool's
    reserve, so neither a burst of the holder's own writes, nor a full write
    lane, nor a pool the writes have filled refuses it. A system marks its
    own routes the same way, and nothing here changes. The mark does nothing
    when the route runs: it is read where the app is built
    (`HolderCalls.of`)."""


HOLDER_CALL = Depends(holder_call)


@dataclass(frozen=True)
class HolderCalls:
    """The routes that carry `HOLDER_CALL`, each as its methods and the
    pattern its path is routed by. Admission matches a request against them
    before routing runs, with the route's own pattern, so the mark on the
    route is the only declaration there is. The routes are read as FastAPI
    routes them, with the prefixes and the dependencies of every router that
    includes them."""

    routes: tuple[tuple[frozenset[str], re.Pattern[str]], ...] = ()

    @classmethod
    def of(cls, routes: Sequence[BaseRoute]) -> HolderCalls:
        return cls(
            tuple(
                (frozenset(route.methods or ()), route.path_regex)
                for route in iter_route_contexts(routes)
                if any(
                    depends.dependency is holder_call
                    for depends in getattr(route, "dependencies", ())
                )
            )
        )

    def match(self, scope: Scope) -> bool:
        method, path = scope["method"], scope["path"]
        return any(method in methods and pattern.match(path) for methods, pattern in self.routes)


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
    """Counts the HTTP requests in flight, reads, writes, and a holder's calls
    apart, and refuses past any bound with the unavailable shape and a
    `Retry-After`. A request it admits carries its deadline and its lane from
    there on, and one it admits in the holds lane runs marked a holder's call,
    for the pool's reserve.
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
        limit_holds: int,
        holder_calls: HolderCalls,
        retry_after: timedelta,
        deadline: timedelta,
    ) -> None:
        self.app = app
        self.reads = Lane("reads", limit_reads)
        self.writes = Lane("writes", limit_writes)
        self.holds = Lane("holds", limit_holds)
        self.holder_calls = holder_calls
        self.retry_after = str(max(1, int(retry_after.total_seconds())))
        self.deadline = deadline

    def lane_of(self, scope: Scope) -> Lane:
        if self.holder_calls.match(scope):
            return self.holds
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
        state = scope.setdefault("state", {})
        state[DEADLINE_KEY] = utcnow() + self.deadline
        state[LANE_KEY] = lane.name
        lane.in_flight += 1
        try:
            if lane is self.holds:
                with as_holder_call():
                    await self.app(scope, receive, send)
            else:
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
