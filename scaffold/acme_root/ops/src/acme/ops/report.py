"""What an operator reads after a run: requests by route and status, the
p50, p95, and p99 by route and over two groups of requests, the error ratio,
the sessions completed, and how long it all took. A sample is one request as
the transport saw it; the report is pure arithmetic over the samples.

The two groups are the working requests and the sign-in and sign-out that
carry them. They are split because they measure different things: a run
makes one sign-in, one exchange, and one sign-out per person, while it makes
hundreds of the rest, and the sign-in writes a credential every time.
A p95 over both is a p95 of the mix, and the mix moves with the profile."""

import json
import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

AUTH_ROUTES = frozenset({"/v1/auth/dev-sign-in", "/v1/auth/sessions", "/v1/auth/logout"})
"""Sign-in and sign-out. Every other route a run calls is a working request:
the person, the members, the API keys, the event stream, and the socket's
ticket."""


@dataclass(frozen=True)
class Sample:
    """One request: the route template (ids replaced by `{id}`), the method,
    the status (0 when the wire failed before the API answered), the elapsed
    milliseconds, the request id the answer carried, and the failure's name
    when there was no answer."""

    route: str
    method: str
    status: int
    elapsed_ms: float
    request_id: str | None = None
    failure: str | None = None

    @property
    def is_error(self) -> bool:
        """A wire failure or a 5xx. A 4xx is a decision the API made about
        the request and is reported by status, not as an error."""
        return self.status == 0 or self.status >= 500

    @property
    def is_auth(self) -> bool:
        """A sign-in or a sign-out, which the report keeps beside the working
        requests instead of in them."""
        return self.route in AUTH_ROUTES


def percentile(values: Sequence[float], p: float) -> float:
    """Nearest rank: the value at rank ceil(p/100 * n), so a percentile is
    always one of the observed values. Zero over nothing."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


@dataclass(frozen=True)
class RouteLine:
    method: str
    route: str
    status: int
    count: int
    p50_ms: float
    p95_ms: float
    p99_ms: float


@dataclass(frozen=True)
class Group:
    """One set of requests totalled on its own: how many there were, how many
    failed, and the percentiles over them."""

    requests: int
    errors: int
    error_ratio: float
    p50_ms: float
    p95_ms: float
    p99_ms: float

    @classmethod
    def of(cls, samples: Iterable[Sample]) -> Group:
        times: list[float] = []
        errors = 0
        for sample in samples:
            times.append(sample.elapsed_ms)
            errors += sample.is_error
        return cls(
            requests=len(times),
            errors=errors,
            error_ratio=errors / len(times) if times else 0.0,
            p50_ms=percentile(times, 50),
            p95_ms=percentile(times, 95),
            p99_ms=percentile(times, 99),
        )

    def line(self, name: str) -> str:
        return (
            f"{name}: {self.requests} requests, {self.errors} errors "
            f"({self.error_ratio * 100:.2f}%), p50 {self.p50_ms:.1f} ms, "
            f"p95 {self.p95_ms:.1f} ms, p99 {self.p99_ms:.1f} ms"
        )


@dataclass(frozen=True)
class Sessions:
    completed: int = 0
    failed: int = 0
    cut: int = 0
    """Ended by the duration bound mid-session; neither a success nor a failure."""

    @property
    def started(self) -> int:
        return self.completed + self.failed + self.cut


@dataclass(frozen=True)
class Report:
    environment: str
    profile: str
    started_at: datetime
    duration_seconds: float
    sessions: Sessions
    routes: list[RouteLine]
    requests: int
    errors: int
    error_ratio: float
    working: Group
    """Every request but the sign-ins and sign-outs: the requests a target's
    p95 judges."""
    auth: Group
    """The sign-ins and sign-outs, one of each per person for the whole run,
    reported beside the working requests and never mixed into them."""
    notes: list[str] = field(default_factory=list)

    @classmethod
    def of(
        cls,
        samples: Iterable[Sample],
        *,
        environment: str,
        profile: str,
        started_at: datetime,
        duration_seconds: float,
        sessions: Sessions,
        notes: Sequence[str] = (),
    ) -> Report:
        by_line: dict[tuple[str, str, int], list[float]] = defaultdict(list)
        every: list[Sample] = []
        working: list[Sample] = []
        auth: list[Sample] = []
        errors = 0
        for sample in samples:
            by_line[(sample.method, sample.route, sample.status)].append(sample.elapsed_ms)
            every.append(sample)
            (auth if sample.is_auth else working).append(sample)
            errors += sample.is_error
        routes = [
            RouteLine(
                method,
                route,
                status,
                len(times),
                percentile(times, 50),
                percentile(times, 95),
                percentile(times, 99),
            )
            for (method, route, status), times in sorted(by_line.items())
        ]
        return cls(
            environment=environment,
            profile=profile,
            started_at=started_at,
            duration_seconds=duration_seconds,
            sessions=sessions,
            routes=routes,
            requests=len(every),
            errors=errors,
            error_ratio=errors / len(every) if every else 0.0,
            working=Group.of(working),
            auth=Group.of(auth),
            notes=list(notes),
        )

    def to_json(self) -> str:
        data: dict[str, Any] = asdict(self)
        data["started_at"] = self.started_at.isoformat()
        return json.dumps(data, indent=2) + "\n"

    def table(self) -> str:
        """The stdout view: one line per route and status, then the totals,
        the working requests a target judges, and the sign-in and sign-out
        beside them."""
        header = (
            f"{'method':<7} {'route':<34} {'status':>6} {'count':>6} "
            f"{'p50 ms':>8} {'p95 ms':>8} {'p99 ms':>8}"
        )
        s = self.sessions
        lines = [
            f"acme-ops traffic  env={self.environment}  profile={self.profile}  "
            f"duration={self.duration_seconds:.1f}s  "
            f"sessions: {s.completed} completed, {s.failed} failed, {s.cut} cut",
            header,
            "-" * len(header),
        ]
        for line in self.routes:
            lines.append(
                f"{line.method:<7} {line.route:<34} {line.status:>6} {line.count:>6} "
                f"{line.p50_ms:>8.1f} {line.p95_ms:>8.1f} {line.p99_ms:>8.1f}"
            )
        lines.append("-" * len(header))
        lines.append(
            f"requests {self.requests}, errors {self.errors} ({self.error_ratio * 100:.2f}%)"
        )
        lines.append(self.working.line("working, the requests a target judges"))
        lines.append(self.auth.line("sign-in and sign-out, reported beside them"))
        lines.extend(f"note: {note}" for note in self.notes)
        return "\n".join(lines) + "\n"
