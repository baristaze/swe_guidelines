"""The telemetry round trip: the API as a real process on a port this run
picks, the devx collector aimed at that port for as long as the run lasts,
the trace exporter and the error tracker set, one session of traffic through
the edge, and then every signal read back by request id through the local
reader: the log line that names it, the counter that moved, the trace that
exists, the error event that carries it.

The error leg is a second process of the same binary on a free port whose
database is unreachable: a route that raises past the gateway is answered
500 with the request id and reported to the tracker, which is the one path
that produces an ERROR without a code change and without harming the session
the other legs read.

Needs the devx profile and a migrated, seeded stack. What is missing is a
skip on a developer's machine, naming it, and a failure wherever
`ACME_TELEMETRY_REQUIRED` is set: a skipped check in CI is no check."""

import asyncio
import os
import socket
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NoReturn
from uuid import uuid4

import httpx
import pytest
import yaml

from acme.ops.environments import Environment, load_environment, parse_env_file
from acme.ops.profiles import LIGHT
from acme.ops.signals.local import SignalsLocalImpl
from acme.ops.traffic import run_traffic

pytestmark = pytest.mark.telemetry

REPO = Path(__file__).resolve().parents[2]
COLLECTOR_CONFIG = REPO / "deployment" / "local" / "otel-collector" / "collector.yml"
OTLP_ENDPOINT = "http://127.0.0.1:54318"
REQUESTS_COUNTER = "acme_http_requests_total"
DURATION_UNITS = {"ms": 0.001, "s": 1.0, "m": 60.0}

REQUIRED = "ACME_TELEMETRY_REQUIRED"
"""Where the round trip must run: the CI job sets it, a developer's machine
does not. A run that skips because the stack is not up is a kindness on a
laptop and a lie in CI, where the job goes green having proved nothing."""

BOOT_SECONDS = 30.0
"""How long an API process gets to answer /healthz. Nothing governs it: it is
a whole interpreter's imports and one container's construction, so it is a
bound on a boot that hangs, not a schedule."""

ERROR_WAIT_SECONDS = 60.0
"""How long an error event gets to become findable, and the one wait here
with nothing behind it. The client adds no schedule to derive from:
sentry_sdk's worker sends an event as soon as the report queues it, and
nothing batches on that side. What the wait is really for is the tracker's
own ingest, which turns an accepted envelope into an event the issue search
answers with, on the image's schedule and not on one this repository sets.
The poll returns the moment the event lands, so the number only bounds a
failure."""

TRACKER_READY_SECONDS = 60.0
"""How long the error tracker gets to become usable, which is longer than a
restart and shorter than a wait anyone would sit through. GlitchTip runs its
migrations at start, so a tracker whose database was recreated under it comes
back only when its container is restarted; past this the fixture says so."""

SPAN_BATCH_DELAY = timedelta(seconds=5)
OTEL_TIMEOUT = timedelta(seconds=10)
"""The trace exporter's schedule, handed to the API process below
(`OTEL_BSP_SCHEDULE_DELAY` and `ACME_OTEL_TIMEOUT_SECONDS`) so the wait for
a trace is derived from the settings the process runs under rather than from
a guess about them: the batch processor ships a batch every delay, and
`configure_tracing` bounds each shipment with the timeout."""


def duration_seconds(text: str) -> float:
    """A collector duration (`15s`, `500ms`, `1m`) in seconds."""
    for unit in sorted(DURATION_UNITS, key=len, reverse=True):
        if text.endswith(unit):
            return float(text.removesuffix(unit)) * DURATION_UNITS[unit]
    raise ValueError(f"not a duration: {text!r}")


def scrape_wait_seconds() -> float:
    """How long a counter takes to reach Prometheus, from the collector's own
    config: a scrape that sees it comes within one scrape interval, and the
    batch that holds it goes out within the batch timeout. The deadline is
    three intervals and one flush, so a missed or slow scrape and a retried
    send still land inside it."""
    config = yaml.safe_load(COLLECTOR_CONFIG.read_text())
    interval = duration_seconds(
        config["receivers"]["prometheus"]["config"]["global"]["scrape_interval"]
    )
    flush = duration_seconds(config["processors"]["batch"]["timeout"])
    return 3 * interval + flush


def trace_wait_seconds() -> float:
    """How long a span takes to reach Jaeger, from the exporter settings the
    fixture hands the process: two batch delays and one bounded export, so a
    batch that closed just before the last span of the session, and a send
    that had to be retried, both land inside it."""
    return 2 * SPAN_BATCH_DELAY.total_seconds() + OTEL_TIMEOUT.total_seconds()


SCRAPE_WAIT_SECONDS = scrape_wait_seconds()
TRACE_WAIT_SECONDS = trace_wait_seconds()


def unmet(what: str) -> NoReturn:
    """A piece the round trip needs and has not got: a skip where a developer
    may not have the stack up, a failure where the run was required to prove
    something. Either way it names what was missing."""
    if os.environ.get(REQUIRED, "").strip().lower() in ("1", "true", "yes"):
        pytest.fail(f"{what}; the round trip is required here ({REQUIRED} is set)")
    pytest.skip(f"{what}; set {REQUIRED}=1 to make this a failure instead of a skip")


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def reachable(url: str) -> bool:
    try:
        httpx.get(url, timeout=2.0)
    except httpx.HTTPError:
        return False
    return True


@dataclass
class Served:
    port: int
    log: Path
    process: subprocess.Popen[bytes]

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def lines(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.is_file() else []

    def stop(self) -> None:
        """Ends the process, whatever state it is in, and can be called
        twice: a teardown that has to be conditional is a port left held."""
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(5)


def compose_knobs() -> dict[str, str]:
    """`.env.example`, and the developer's `.env` over it where one exists."""
    knobs: dict[str, str] = {}
    for name in (".env.example", ".env"):
        if (REPO / name).is_file():
            knobs.update(parse_env_file((REPO / name).read_text()))
    return knobs


def serve(port: int, log: Path, overrides: Mapping[str, str]) -> Served:
    """The API binary as a process, over the compose knobs and the overrides,
    its stderr (the log) to a file the reader is handed. A process that never
    answers is stopped before this raises, so no path out of here leaves one
    running."""
    env = {**os.environ, **compose_knobs(), **overrides}
    with log.open("wb") as handle:
        process = subprocess.Popen(
            [sys.executable, "-m", "acme.services.api.entry", "serve", "--port", str(port)],
            cwd=REPO,
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
        )
    served = Served(port, log, process)
    try:
        deadline = time.monotonic() + BOOT_SECONDS
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            if reachable(f"{served.base_url}/healthz"):
                return served
            time.sleep(0.5)
    except BaseException:
        served.stop()
        raise
    served.stop()
    pytest.fail(f"the API did not come up on {port}:\n" + "\n".join(served.lines()[-20:]))


@contextmanager
def serving(port: int, log: Path, overrides: Mapping[str, str]) -> Iterator[Served]:
    """A served process for the length of a `with`, stopped on every way out
    of it: a failing test, a fixture that raises after the yield, an
    interrupt. Two of these run at once, and a leaked one holds a port."""
    served = serve(port, log, overrides)
    try:
        yield served
    finally:
        served.stop()


def scrape(port: int | None) -> None:
    """Point the devx collector's api target at a port on the host, or at the
    `.env` default when the port is None, and recreate the container, which
    is the only way a collector reads a changed config. `make
    collector-scrape` owns the compose command, including the file Linux
    needs, so this test does not have a second copy of it."""
    argument = [] if port is None else [f"SCRAPE_PORT={port}"]
    done = subprocess.run(
        ["make", "collector-scrape", *argument],
        cwd=REPO,
        capture_output=True,
        text=True,
        # A nested make would otherwise inherit the outer one's jobserver.
        env={**os.environ, "MAKEFLAGS": ""},
    )
    if done.returncode != 0:
        where = "the default port" if port is None else f"port {port}"
        pytest.fail(f"the collector would not scrape {where}:\n{done.stdout}\n{done.stderr}")


def tracker_detail(env: Environment) -> str | None:
    """None when the error tracker is usable, and what is wrong otherwise.

    Usable is not the same as answering. GlitchTip runs its migrations when
    it starts, so a tracker whose database was recreated under it — a
    `make reset` while its container stayed up — still answers its root route
    with nothing behind it, and the first thing to fail is a read or the
    seed, on a missing table. So the check is a read of the seeded project's
    issues under the read-only token: the one call that needs the schema, the
    seed, and the token, all three."""
    project = f"{env.error_tracker_org}/{env.error_tracker_project}"
    url = f"{env.error_tracker_url}/api/0/projects/{project}/issues/"
    try:
        response = httpx.get(
            url,
            params={"limit": 1},
            headers={"Authorization": f"Bearer {env.error_tracker_token or ''}"},
            timeout=10.0,
        )
    except httpx.HTTPError as error:
        return f"{url} does not answer ({error})"
    if response.status_code != 200:
        return f"{url} answered {response.status_code}: {response.text[:200]}"
    return None


@pytest.fixture(scope="module")
def env() -> Environment:
    return load_environment("local", root=REPO)


@pytest.fixture(scope="module")
def stores(env: Environment) -> None:
    for name, url in (
        ("Prometheus", f"{env.prometheus_url}/-/ready"),
        ("Jaeger", f"{env.jaeger_url}/api/v3/services"),
    ):
        if not reachable(url):
            unmet(f"{name} is not reachable at {url}: the round trip needs the devx profile")
    # The tracker is waited for rather than probed once: it is the slowest of
    # the three to become usable, and the state it can be in — up, answering,
    # and holding no schema — is the one that broke a run.
    detail = tracker_detail(env)
    deadline = time.monotonic() + TRACKER_READY_SECONDS
    while detail is not None and time.monotonic() < deadline:
        time.sleep(2.0)
        detail = tracker_detail(env)
    if detail is not None:
        unmet(
            f"the error tracker's project does not read back after {TRACKER_READY_SECONDS:.0f}s: "
            f"{detail}. It needs the devx profile, `make migrate seed`, and a tracker whose "
            "database it migrated: one recreated under a running container answers with no "
            "schema until `docker compose restart glitchtip` runs its migrations again"
        )


@pytest.fixture(scope="module")
def api(stores: None, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Served]:
    """The round trip's API: a real process on a free port, with the devx
    collector aimed at that port while it runs. The collector's target
    follows the process, so the round trip runs beside whatever holds 8000
    and puts the collector back when it is done."""
    knobs = compose_knobs()
    with serving(
        free_port(),
        tmp_path_factory.mktemp("api") / "api.log",
        {
            "ACME_ENVIRONMENT": "local",
            "ACME_OTEL_ENDPOINT": OTLP_ENDPOINT,
            "ACME_OTEL_TIMEOUT_SECONDS": str(OTEL_TIMEOUT.total_seconds()),
            "OTEL_BSP_SCHEDULE_DELAY": str(int(SPAN_BATCH_DELAY.total_seconds() * 1000)),
            "ACME_SENTRY_DSN": knobs["ACME_SENTRY_DSN"],
            "ACME_LOG_JSON": "true",
        },
    ) as served:
        scrape(served.port)
        try:
            yield served
        finally:
            scrape(None)


@pytest.fixture(scope="module")
def api_env(env: Environment, api: Served) -> Environment:
    """The environment the traffic run drives: this one, at the port this
    run's API took."""
    return replace(env, api_url=api.base_url)


@pytest.fixture(scope="module")
def broken_api(stores: None, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Served]:
    """The same binary with no database behind it: every tenant route raises."""
    knobs = compose_knobs()
    with serving(
        free_port(),
        tmp_path_factory.mktemp("broken") / "api.log",
        {
            "ACME_ENVIRONMENT": "local",
            # Both logins point at the closed port: the session lookup runs on
            # the system login, and a route that reached a live database there
            # would answer 401 instead of raising. So does every role's own
            # URL, which the knobs set to each role's instance.
            "ACME_DATABASE_URL": "postgresql+asyncpg://acme_runtime:acme_runtime@127.0.0.1:1/acme",
            "ACME_DATABASE_SYSTEM_URL": "postgresql+asyncpg://acme_system:acme_system@127.0.0.1:1/acme",
            **{
                f"ACME_DATABASE_URL_{role}": (
                    "postgresql+asyncpg://acme_runtime:acme_runtime@127.0.0.1:1/acme"
                )
                for role in ("CORE", "ACTIVITY", "QUEUE", "ADMIN")
            },
            "ACME_CACHE_BACKEND": "memory",
            "ACME_TOPICS_BACKEND": "memory",
            "ACME_BUCKETS_BACKEND": "local",
            "ACME_QUEUES_BACKEND": "memory",
            "ACME_OTEL_ENDPOINT": OTLP_ENDPOINT,
            "ACME_SENTRY_DSN": knobs["ACME_SENTRY_DSN"],
            "ACME_LOG_JSON": "true",
        },
    ) as served:
        yield served


def reader(env: Environment, served: Served) -> SignalsLocalImpl:
    assert env.prometheus_url and env.jaeger_url and env.error_tracker_url
    return SignalsLocalImpl(
        prometheus_url=env.prometheus_url,
        jaeger_url=env.jaeger_url,
        error_tracker_url=env.error_tracker_url,
        error_tracker_token=env.error_tracker_token or "",
        error_tracker_org=env.error_tracker_org,
        error_tracker_project=env.error_tracker_project,
        environment=env.name,
        logs=served.lines,
    )


async def poll[T](
    read: Callable[[], Awaitable[T | None]], seconds: float, every: float = 2.0
) -> T | None:
    deadline = time.monotonic() + seconds
    while True:
        found = await read()
        if found or time.monotonic() >= deadline:
            return found
        await asyncio.sleep(every)


async def test_every_signal_reads_back_by_the_request_id_of_a_write(
    api_env: Environment, api: Served
) -> None:
    since = datetime.now(UTC) - timedelta(seconds=5)
    result = await run_traffic(api_env, LIGHT, duration_seconds=90, orgs=0, max_sessions=1)
    outcome = result.outcomes[0]
    assert outcome.completed, outcome.failure or result.report.table()
    assert outcome.saw_own_change
    request_id = outcome.write_request_ids[0]
    signals = reader(api_env, api)
    writes = len(outcome.write_request_ids)

    # The log line that names it.
    lines = await signals.log_lines(request_id)
    assert lines, f"no log line carries {request_id}"
    assert any("POST /v1/api-keys 201" in line for line in lines), lines

    # The counter that moved: the collector's scrape and its batch lag the run.
    labels = {"route": "/v1/api-keys", "method": "POST", "status": "201"}

    async def counted() -> float | None:
        delta = await signals.metric_delta(REQUESTS_COUNTER, labels, since)
        return delta if delta and delta >= writes else None

    delta = await poll(counted, SCRAPE_WAIT_SECONDS, every=5.0)
    assert delta is not None, f"Prometheus did not count the {writes} creating calls"

    # The trace that exists: the batch exporter ships on the schedule the
    # fixture set, and TRACE_WAIT_SECONDS is two of those and one export.
    trace = await poll(lambda: signals.trace(request_id), TRACE_WAIT_SECONDS)
    assert trace is not None, f"Jaeger holds no trace with acme.request_id={request_id}"
    assert "POST /v1/api-keys" in trace.span_names, trace


async def test_an_error_event_carries_the_request_id(env: Environment, broken_api: Served) -> None:
    request_id = str(uuid4())
    async with httpx.AsyncClient(base_url=broken_api.base_url, timeout=15.0) as http:
        response = await http.get(
            "/v1/users",
            headers={
                "x-request-id": request_id,
                "Authorization": "Bearer ses_none",
                "X-App": "cli",
            },
        )
    assert response.status_code == 500, response.text
    assert response.headers["x-request-id"] == request_id
    signals = reader(env, broken_api)
    lines = await signals.log_lines(request_id)
    assert any('"level": "ERROR"' in line for line in lines), lines
    # The tag is the proof; the title is the exception's, as the tracker names it.
    event = await poll(lambda: signals.error_event(request_id), ERROR_WAIT_SECONDS, every=5.0)
    assert event is not None, f"GlitchTip holds no event tagged request_id={request_id}"
    assert event.title, event
