"""The signal readers: one interface, two impls. A signal nothing reads is a
claim, so every store the platform writes to is read back here by the request
id the response carried: the log line that names it, the counter that moved,
the trace that exists, the error event that carries it. The local impl reads
the devx profile's stores; the cloud impl reads the cloud's. One test drives
both."""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class TraceFound:
    trace_id: str
    span_names: tuple[str, ...]


@dataclass(frozen=True)
class ErrorEventFound:
    event_id: str
    issue_id: str
    title: str


@dataclass(frozen=True)
class Readback:
    """What `signals check` prints: one line per leg, found or not."""

    request_id: str
    log_lines: list[str] = field(default_factory=list)
    metric_delta: float | None = None
    trace: TraceFound | None = None
    error_event: ErrorEventFound | None = None
    error_events_read: bool = True
    """False when the environment names no error tracker. Not reading a leg
    is a different thing from reading it and finding nothing, and the report
    says which of the two happened."""


class SignalsInterface(ABC):
    @property
    @abstractmethod
    def reads_error_events(self) -> bool:
        """Whether there is an error tracker to read at all. An environment
        that names none reads the other legs and reports this one as not
        read."""

    @abstractmethod
    async def log_lines(self, request_id: str) -> list[str]:
        """The log lines that carry the request id, oldest first."""

    @abstractmethod
    async def metric_delta(
        self, name: str, labels: Mapping[str, str], since: datetime
    ) -> float | None:
        """How far the counter named moved since `since`, summed over the
        series that match the labels; None when no such series exists. A
        label value that starts with `~` is a pattern where the store can
        match one (Prometheus), and names no series where it cannot."""

    @abstractmethod
    async def trace(self, request_id: str) -> TraceFound | None:
        """The trace whose server span carries the request id."""

    @abstractmethod
    async def error_event(self, request_id: str) -> ErrorEventFound | None:
        """The error event tagged with the request id; None when there is
        none, and also None when `reads_error_events` is false."""

    @abstractmethod
    def describe(self) -> str:
        """Where each leg reads from, for the report."""


__all__ = ["ErrorEventFound", "Readback", "SignalsInterface", "TraceFound"]
