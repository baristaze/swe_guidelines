"""The stress test: the traffic generator with a scenario. A scenario file
names a profile, a duration, a ramp, and a target (a p95 and an error ratio)
stated before the run. The run ramps the workers up linearly, drives the
profile for the duration, then reads the signals back for the window and
passes or fails against the target. The numbers a system is held to are the
system's, and they live in the scenario file, not here.

The caller may state the target instead, one number or both, and then the
scenario's is a default and not a judgement. Either way the run says which
target it judged and where that number came from: a verdict without that is
a number without a claim."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from acme.ops.profiles import Profile, profile_named
from acme.ops.report import Report
from acme.ops.signals import SignalsInterface

REQUESTS_COUNTER = "acme_http_requests_total"
POLL_SECONDS = 5.0
READBACK_WAIT_SECONDS = 45.0
"""Long enough for the local scrape interval (15 s) twice over and the
collector's batch (5 s) that writes a host process's samples into
Prometheus (deployment/local/otel-collector/collector.yml)."""


FROM_SCENARIO = "from the scenario"
FROM_THE_RUN = "set for this run"


@dataclass(frozen=True)
class Target:
    """The pass mark, and where each half of it came from. A number the
    caller stated carries `FROM_THE_RUN`, a number the file stated carries
    `FROM_SCENARIO`, and the run prints both beside the verdict."""

    p95_ms: float
    error_ratio: float
    p95_source: str = FROM_SCENARIO
    error_ratio_source: str = FROM_SCENARIO


@dataclass(frozen=True)
class Scenario:
    name: str
    profile: Profile
    duration_seconds: float
    ramp_seconds: float
    target: Target
    weights: Mapping[str, float]


def parse_scenario(data: Mapping[str, Any]) -> Scenario:
    for key in ("name", "profile", "duration_seconds", "ramp_seconds", "target"):
        if key not in data:
            raise ValueError(f"scenario is missing {key!r}")
    target = data["target"]
    if not isinstance(target, Mapping) or "p95_ms" not in target or "error_ratio" not in target:
        raise ValueError("scenario target needs p95_ms and error_ratio")
    weights = data.get("weights") or {}
    if not isinstance(weights, Mapping):
        raise ValueError("scenario weights must be a mapping")
    duration = float(data["duration_seconds"])
    ramp = float(data["ramp_seconds"])
    if duration <= 0 or ramp < 0 or ramp > duration:
        raise ValueError("duration_seconds must be positive and ramp_seconds within it")
    return Scenario(
        name=str(data["name"]),
        profile=profile_named(str(data["profile"])),
        duration_seconds=duration,
        ramp_seconds=ramp,
        target=Target(p95_ms=float(target["p95_ms"]), error_ratio=float(target["error_ratio"])),
        weights={str(k): float(v) for k, v in weights.items()},
    )


def with_duration(scenario: Scenario, seconds: float) -> Scenario:
    """The same scenario over another window, which is what a caller that
    names a duration asks for: a shorter run of the shape the file states.
    A ramp longer than the run is cut to it, since a ramp that never reaches
    the concurrency is not a ramp. The target does not move."""
    if seconds <= 0:
        raise ValueError("the duration must be positive")
    return replace(
        scenario, duration_seconds=seconds, ramp_seconds=min(scenario.ramp_seconds, seconds)
    )


def with_target(
    scenario: Scenario, p95_ms: float | None = None, error_ratio: float | None = None
) -> Scenario:
    """The same scenario judged against a target the caller states: the
    working requests' p95, the error ratio, or both. A number left out keeps
    the scenario's, and keeps the scenario as its source, so the run can say
    where each half of the pass mark came from. Nothing else moves: the
    profile, the duration, and the ramp are the file's."""
    if p95_ms is None and error_ratio is None:
        return scenario
    if p95_ms is not None and p95_ms <= 0:
        raise ValueError("the target p95 is a positive number of milliseconds")
    if error_ratio is not None and not 0.0 <= error_ratio <= 1.0:
        raise ValueError("the target error ratio is a fraction between 0 and 1")
    current = scenario.target
    return replace(
        scenario,
        target=Target(
            p95_ms=current.p95_ms if p95_ms is None else p95_ms,
            error_ratio=current.error_ratio if error_ratio is None else error_ratio,
            p95_source=current.p95_source if p95_ms is None else FROM_THE_RUN,
            error_ratio_source=(
                current.error_ratio_source if error_ratio is None else FROM_THE_RUN
            ),
        ),
    )


def load_scenario(path: Path) -> Scenario:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, Mapping):
        raise ValueError(f"{path} is not a scenario mapping")
    return parse_scenario(data)


@dataclass(frozen=True)
class Readback:
    """What the signals said about the window: how many requests the
    platform counted and how many of them it answered with a 5xx. None when
    the store had nothing for the window."""

    requests: float | None
    server_errors: float | None
    error_events_read: bool = True
    """False when the environment names no error tracker. The verdict holds
    the run to the request counter and the 5xx count and never to the
    tracker, so the run still passes or fails; the text says the leg was
    not read rather than leaving it out."""

    @property
    def error_ratio(self) -> float | None:
        if not self.requests:
            return None
        return (self.server_errors or 0.0) / self.requests


@dataclass(frozen=True)
class Verdict:
    passed: bool
    reasons: tuple[str, ...]

    def text(self, scenario: Scenario, report: Report, readback: Readback) -> str:
        lines = [
            f"stress {scenario.name}: profile {scenario.profile.name}, "
            f"{scenario.duration_seconds:.0f}s with a {scenario.ramp_seconds:.0f}s ramp",
            f"target: p95 <= {scenario.target.p95_ms:.0f} ms over the working requests "
            f"({scenario.target.p95_source}), "
            f"error ratio <= {scenario.target.error_ratio:.4f} over every request "
            f"({scenario.target.error_ratio_source})",
            f"measured: p95 {report.working.p95_ms:.1f} ms over {report.working.requests} "
            f"working requests, error ratio {report.error_ratio:.4f} over {report.requests} "
            f"requests, {report.sessions.completed} sessions completed",
            f"conflicts: {report.sessions.conflicts} answered by a fresh read, "
            f"{report.sessions.gone} on a task found gone; counted, not judged",
            f"beside it: {report.auth.requests} sign-ins and sign-outs, "
            f"p50 {report.auth.p50_ms:.1f} ms, p95 {report.auth.p95_ms:.1f} ms, "
            "reported and not judged",
            "signals: "
            + (
                f"{readback.requests:.0f} requests counted, {readback.server_errors or 0:.0f} "
                f"server errors (ratio {readback.error_ratio or 0:.4f})"
                if readback.requests
                else "nothing counted for the window"
            ),
        ]
        if not readback.error_events_read:
            lines.append("error events: not read, the environment names no error tracker")
        lines.append(
            ("PASS" if self.passed else "FAIL")
            + (": " + "; ".join(self.reasons) if self.reasons else "")
        )
        return "\n".join(lines) + "\n"


def verdict(scenario: Scenario, report: Report, readback: Readback) -> Verdict:
    """Pass when the generator's p95 and error ratio meet the target, the
    platform counted the window, and its own 5xx ratio meets the target too.
    A run that made no requests fails: it proved nothing. The error tracker
    is not one of the signals the verdict holds to, so an environment that
    names none still passes or fails on the counter it did read.

    The p95 is the working requests': the task and event routes, and the
    socket's ticket. A run makes one sign-in and one sign-out per person, so
    holding a target to a p95 over both would judge how often the generator
    signs in. They are reported
    beside the verdict, with their own p95. The error ratio is over every
    request, sign-in and sign-out included: a refused sign-in is a refusal
    whoever made it."""
    reasons: list[str] = []
    if report.requests == 0:
        reasons.append("no requests were made")
    if report.working.p95_ms > scenario.target.p95_ms:
        reasons.append(f"p95 {report.working.p95_ms:.1f} ms over {scenario.target.p95_ms:.0f} ms")
    if report.error_ratio > scenario.target.error_ratio:
        reasons.append(
            f"error ratio {report.error_ratio:.4f} over {scenario.target.error_ratio:.4f}"
        )
    if not readback.requests:
        reasons.append("the platform counted no requests for the window")
    elif (readback.error_ratio or 0.0) > scenario.target.error_ratio:
        reasons.append(
            f"the platform's 5xx ratio {readback.error_ratio:.4f} over "
            f"{scenario.target.error_ratio:.4f}"
        )
    return Verdict(passed=not reasons, reasons=tuple(reasons))


async def read_back(signals: SignalsInterface, since: datetime) -> Readback:
    requests = await signals.metric_delta(REQUESTS_COUNTER, {}, since)
    errors = await signals.metric_delta(REQUESTS_COUNTER, {"status": "~5.."}, since)
    return Readback(
        requests=requests,
        server_errors=errors,
        error_events_read=signals.reads_error_events,
    )


def window_start(report: Report) -> datetime:
    return report.started_at if report.started_at.tzinfo else report.started_at.replace(tzinfo=UTC)
