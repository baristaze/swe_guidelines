"""The one traffic generator. It drives the edge through the Python client,
never a manager: a session is what a person does in the portal, and a run is
many sessions at once, for a bounded time, at a profile.

A person signs in once, at the start of the run: the local sign-in, by
address alone, then the exchange for a session token in the org. Every
session that person drives reuses that token, and the sign-out comes once,
at the end. The API counts every sign-in against a per-address rate limit,
and a run signs all its people in from one address; a session that signed in
for itself would measure that limit instead of the application. One sign-in
per person leaves little room for a refusal, so a sign-in the rate limit
turns away waits the window out and asks again, a bounded number of times,
and a run that still signs nobody in fails and says why.

The local sign-in is the local stack's alone: a deployed environment signs
people in through the identity provider, in a browser, and answers the
local sign-in with 404. So a run against a deployed environment is refused
before it provisions anything.

A session: open the socket, read the person (`GET /v1/me`), list the
members, write the person's display name (`PATCH /v1/me`), create an API key
under an idempotency key and revoke it, read the stream after where it stood
at the start, and see one of its own changes arrive on the socket. A person
thinks between steps. The display name is written as it stands, so the
write and its event are real and the person is left as they were. The key
lives a day at most, so one a cut session leaves behind ends on its own.
Any refusal fails the session: none of these writes races another person's.

The tenants a run needs come from the operator plane (`POST /v1/admin/orgs`
and its members) under the provisioner's operator token, a `write` entry and
never a sign-in. They are named for the run, `ops-<run id>-<n>`, so no real
tenant is touched and anything that counts tenants can leave them out, and
the run removes them (`DELETE /v1/admin/orgs/{org_id}`, which closes each at
once and leaves the rest to the worker) when it ends, a failure included; the
ones it could not remove are named in the report.
`orgs=0` drives the org and the two people `make seed` created instead, which
is how the local stack is exercised with nothing provisioned."""

import asyncio
import logging
import random
import re
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from itertools import zip_longest
from typing import Any
from uuid import UUID, uuid4

import httpx

from acme.client.client import REQUEST_ID_HEADER, ApiClient, ApiError, trust_store
from acme.client.envelopes import EntityChanged
from acme.client.realtime import Channel, Connect, State
from acme.client.types import ApiKeyView, IssuedApiKeyView, UserView
from acme.ops.environments import Environment
from acme.ops.profiles import Profile
from acme.ops.report import Report, Sample, Sessions

log = logging.getLogger(__name__)

SESSION_APP = "portal"
"""What a session says it is: the app a person uses. `X-App` must name an
app the wire accepts (`AppType`), and the operator plane is driven as `admin`."""
OPERATOR_APP = "admin"
OWN_CHANGE_TIMEOUT_SECONDS = 10.0
"""How long a session waits for the socket to show it one of its own writes."""
KEY_ROLE = "member"
"""The role of the key a session creates: at most any role a run signs in
with, and one that may create keys."""
KEY_TTL_DAYS = 1
"""The shortest life a key can have: a key a cut session did not revoke ends
within a day."""
CUT_GRACE_SECONDS = 5.0
"""Past the duration, how long a request already in flight may take to end
before the run stops waiting for it."""
FAILED_SESSION_PAUSE_SECONDS = 5.0
"""A person whose session failed does not start another at once. Without the
pause a refusal is a tight loop of sessions, which is a flood and not
traffic."""
LOGIN_WINDOW_SECONDS = 60.0
"""The login limiter's window, a minute, and so how long a sign-in refused
with a 429 waits before it asks again when the answer names no `Retry-After`.
Waiting the whole window is the one wait that is certain to clear it."""
LOGIN_WAIT_CAP_SECONDS = 120.0
"""However long an answer asks a sign-in to wait, no single wait is longer.
Past this the environment is closed to the run, and hanging is worse than
failing."""
MAX_LOGIN_WAITS = 2
"""How many times one run waits out a closed login window. Two covers the
window the step before the run filled; a run that needs more is not waiting
out a window, it is waiting on an environment that will not have it."""
NO_ONE_SIGNED_IN = (
    "no one signed in, so the run drove no session; the likeliest cause is the "
    "per-address rate limit on POST /v1/auth/dev-sign-in, and every refusal is named above"
)
"""What a run that signed nobody in says, in its notes and on stderr. It is
the one outcome where the report is empty for a reason worth naming."""

_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def app_version() -> str:
    try:
        return f"ops@{version('acme-ops')}"
    except PackageNotFoundError:
        return "ops@0"


def route_template(path: str) -> str:
    """`/v1/api-keys/<uuid>` reads `/v1/api-keys/{id}`: the report groups by
    route, and an id per line would be a line per request."""
    return _UUID.sub("{id}", path)


class RecordingTransport(httpx.AsyncBaseTransport):
    """Wraps the transport the client sends through and records every request
    as a sample: the route, the status, the time to the last byte, and the
    request id the answer carried. Retries the client makes are requests too,
    so they are recorded too."""

    def __init__(
        self,
        inner: httpx.AsyncBaseTransport,
        also: list[Sample] | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._inner = inner
        self.samples: list[Sample] = []
        self._also = also
        self._clock = clock

    def _record(self, sample: Sample) -> None:
        self.samples.append(sample)
        if self._also is not None:
            self._also.append(sample)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        started = self._clock()
        route = route_template(request.url.path)
        try:
            response = await self._inner.handle_async_request(request)
            await response.aread()
        except httpx.HTTPError as failure:
            elapsed = (self._clock() - started) * 1000
            self._record(Sample(route, request.method, 0, elapsed, failure=type(failure).__name__))
            raise
        elapsed = (self._clock() - started) * 1000
        self._record(
            Sample(
                route,
                request.method,
                response.status_code,
                elapsed,
                request_id=response.headers.get(REQUEST_ID_HEADER),
            )
        )
        return response

    async def aclose(self) -> None:
        # The inner transport is shared by every session of a run and closed by the run.
        return None


@dataclass(frozen=True)
class Person:
    """Someone a session runs as: the address they sign in with and the org to choose."""

    email: str
    org_slug: str


@dataclass(frozen=True)
class Seat:
    """A person signed in for the whole run: the session token every session
    that person drives reuses, and the user id whose changes the socket shows
    them as their own."""

    person: Person
    token: str
    user_id: UUID


class SessionFailed(Exception):
    """A step did not do what a person expects; the session is counted as failed."""


class SessionCut(Exception):
    """The duration ran out mid-session; neither a success nor a failure."""


@dataclass
class SessionOutcome:
    person: Person
    completed: bool = False
    cut: bool = False
    failure: str | None = None
    saw_own_change: bool = False
    write_request_ids: list[str] = field(default_factory=list)
    """The request ids of the creating calls (the API key's), first to last,
    for a caller that reads the signals back by one of them."""


class Clock:
    """The run's deadline and think time, shared by every session of it."""

    def __init__(
        self,
        deadline: float,
        think_seconds: tuple[float, float],
        now: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Any] = asyncio.sleep,
        uniform: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self.deadline = deadline
        self.think_seconds = think_seconds
        self.now = now
        self.sleep = sleep
        self.uniform = uniform

    @property
    def remaining(self) -> float:
        return self.deadline - self.now()

    def check(self) -> None:
        if self.remaining <= 0:
            raise SessionCut

    async def think(self) -> None:
        """A pause between steps, never past the deadline."""
        self.check()
        low, high = self.think_seconds
        await self.sleep(min(self.uniform(low, high), self.remaining))
        self.check()


class Session:
    """One person's session over one client, under the token their seat
    holds: a session opens the socket and does the work, and never signs in
    or out, because the run does that once per person. `recording` is the
    transport the client sends through, for the request ids of the writes;
    `connect` opens the socket and is injected by tests; the default is the
    client's own."""

    def __init__(
        self,
        client: ApiClient,
        seat: Seat,
        clock: Clock,
        *,
        recording: RecordingTransport | None = None,
        connect: Connect | None = None,
    ) -> None:
        self.client = client
        self.seat = seat
        self.person = seat.person
        self.clock = clock
        self.samples: list[Sample] = recording.samples if recording is not None else []
        self.connect = connect
        self.outcome = SessionOutcome(seat.person)
        self._changes: list[EntityChanged] = []
        self._own_change = asyncio.Event()
        self._opened = asyncio.Event()
        self._user_id: UUID = seat.user_id
        self._start_seq: int | None = None
        self._channel: Channel | None = None

    async def run(self) -> SessionOutcome:
        channel_task: asyncio.Task[None] | None = None
        try:
            self.clock.check()
            self.client.token = self.seat.token
            channel_task = asyncio.create_task(self._listen(), name=f"channel-{self.person.email}")
            await self._open_channel()
            await self._work()
            self.outcome.completed = True
        except SessionCut:
            self.outcome.cut = True
        except SessionFailed as failed:
            self.outcome.failure = str(failed)
        except ApiError as error:
            self.outcome.failure = f"{error.status} {error.code} on the API"
        except httpx.HTTPError as failure:
            self.outcome.failure = f"{type(failure).__name__} on the wire"
        finally:
            if channel_task is not None:
                channel_task.cancel()
                try:
                    await channel_task
                except asyncio.CancelledError, Exception:
                    pass
        return self.outcome

    # The steps

    async def _listen(self) -> None:
        def on_state(state: State) -> None:
            if state == "open":
                self._opened.set()

        channel = Channel(self.client, on_state=on_state, connect=self.connect)
        self._channel = channel
        async for change in channel:
            self._changes.append(change)
            if change.actor_id == self._user_id:
                self._own_change.set()

    async def _open_channel(self) -> None:
        """Waits for the hello: where the stream stands is where the session
        started, and the read of the stream at the end is from there."""
        try:
            await asyncio.wait_for(self._opened.wait(), max(self.clock.remaining, 0.01))
        except TimeoutError:
            if self.clock.remaining <= 0:
                raise SessionCut from None
            raise SessionFailed("the socket did not open") from None
        assert self._channel is not None
        self._start_seq = self._channel.cursor

    async def _work(self) -> None:
        client = self.client
        await self.clock.think()
        me = await client.me()

        await self.clock.think()
        await client.users()

        await self.clock.think()
        await self._rename(me.user.display_name or me.user.email.partition("@")[0])

        await self.clock.think()
        issued = await self._create_key(f"ops {uuid4().hex[:8]}")
        self.outcome.write_request_ids = [
            s.request_id
            for s in self.samples
            if s.method == "POST" and s.route == "/v1/api-keys" and s.status == 201 and s.request_id
        ]

        await self.clock.think()
        await self._revoke_key(issued.api_key.id)

        await self.clock.think()
        assert self._start_seq is not None
        events = await client.events_after(self._start_seq)
        if not any(e.actor_id == self._user_id for e in events):
            raise SessionFailed("the stream read back none of the session's own changes")

        try:
            await asyncio.wait_for(
                self._own_change.wait(),
                min(OWN_CHANGE_TIMEOUT_SECONDS, max(self.clock.remaining, 0.01)),
            )
        except TimeoutError:
            if self.clock.remaining <= 0:
                raise SessionCut from None
            raise SessionFailed("the socket showed none of the session's own changes") from None
        self.outcome.saw_own_change = True

    async def _rename(self, display_name: str) -> UserView:
        """The person's display name, written: an update whose event every
        socket of the org hears."""
        body = await self.client.request("PATCH", "/v1/me", json={"display_name": display_name})
        return UserView.model_validate(body)

    async def _create_key(self, name: str) -> IssuedApiKeyView:
        """A key for a script, under an idempotency key as every creating
        call is, so a retry the client makes lands one key."""
        body = await self.client.request(
            "POST",
            "/v1/api-keys",
            json={"name": name, "role": KEY_ROLE, "ttl_days": KEY_TTL_DAYS},
            idempotency_key=str(uuid4()),
        )
        return IssuedApiKeyView.model_validate(body)

    async def _revoke_key(self, key_id: UUID) -> ApiKeyView:
        body = await self.client.request("DELETE", f"/v1/api-keys/{key_id}")
        return ApiKeyView.model_validate(body)


# The sign-in and the sign-out of a run


def seat_order(people: list[Person]) -> list[Person]:
    """The people to sign in, in the order a run takes them: one from each
    org in turn. A run signs in fewer people than a profile provisions, and
    taking them in the order they were made would leave the last org with no
    traffic at all."""
    by_org: dict[str, list[Person]] = {}
    for person in people:
        by_org.setdefault(person.org_slug, []).append(person)
    ordered: list[Person] = []
    for row in zip_longest(*by_org.values()):
        ordered.extend(person for person in row if person is not None)
    return ordered


@dataclass(frozen=True)
class SignIns:
    """What a run's sign-ins came to: the seats, a note per refusal and per
    wait, and how long the run spent waiting out a closed login window. The
    wait is reported because it is time the run was not driving traffic."""

    seats: list[Seat]
    notes: list[str]
    waited_seconds: float = 0.0


async def sign_in(
    env: Environment,
    people: list[Person],
    *,
    transport: httpx.AsyncBaseTransport,
    samples: list[Sample],
    wait: Callable[[float], Any] = asyncio.sleep,
    window_seconds: float = LOGIN_WINDOW_SECONDS,
    max_waits: int = MAX_LOGIN_WAITS,
) -> SignIns:
    """Signs each person in once, one after another: a login, then the
    exchange for a session token in their org. The requests are the run's
    like any other and land in `samples`.

    A person the API refuses is named in a note and left out. A 429 is the
    one refusal the run does not take as final: the address's login window is
    closed, and a window opens again on its own. The run waits it out and
    asks for the same person again, for as long as the answer's `Retry-After`
    asks and otherwise the whole window, at most `max_waits` times in a run.
    After the last wait a 429 stands like any other refusal and stops the
    sign-ins there, since asking again would only add to the count that
    closed the window. `wait` is the pause, injected by tests."""
    recording = RecordingTransport(transport, also=samples)
    seats: list[Seat] = []
    notes: list[str] = []
    waits_left = max_waits
    waited = 0.0
    async with ApiClient(
        env.api_url, app=SESSION_APP, app_version=app_version(), transport=recording
    ) as client:
        queue = list(people)
        while queue:
            person = queue[0]
            try:
                login = await client.dev_sign_in(person.email)
                choices = [m for m in login.memberships if m.org.slug == person.org_slug]
                if not choices:
                    notes.append(f"{person.email} is not a member of {person.org_slug}")
                    queue.pop(0)
                    continue
                session = await client.exchange_session(login.token, choices[0].org.id)
            except ApiError as error:
                if error.status == 429 and waits_left > 0:
                    waits_left -= 1
                    pause = min(error.retry_after or window_seconds, LOGIN_WAIT_CAP_SECONDS)
                    notes.append(
                        f"{person.email} was refused at sign-in: 429 {error.code}; "
                        f"the run waited {pause:.0f} s for the login window and asked again"
                    )
                    waited += pause
                    await wait(pause)
                    continue
                notes.append(f"{person.email} was refused at sign-in: {error.status} {error.code}")
                if error.status == 429:
                    notes.append(
                        f"the address's login window is still closed after {max_waits} wait(s); "
                        "no one else signed in"
                    )
                    break
                queue.pop(0)
                continue
            except httpx.HTTPError as failure:
                notes.append(f"{person.email} was refused at sign-in: {type(failure).__name__}")
                queue.pop(0)
                continue
            seats.append(Seat(person, session.token, session.user.id))
            queue.pop(0)
    return SignIns(seats, notes, waited)


async def sign_out(
    env: Environment,
    seats: list[Seat],
    *,
    transport: httpx.AsyncBaseTransport,
    samples: list[Sample],
) -> list[str]:
    """One sign-out per person, once, when the run is over. A refusal is a
    note: the run is done, and the token expires on its own."""
    if not seats:
        return []
    recording = RecordingTransport(transport, also=samples)
    notes: list[str] = []
    async with ApiClient(
        env.api_url, app=SESSION_APP, app_version=app_version(), transport=recording
    ) as client:
        for seat in seats:
            client.token = seat.token
            try:
                await client.logout()
            except (ApiError, httpx.HTTPError) as error:
                reason = (
                    f"{error.status} {error.code}"
                    if isinstance(error, ApiError)
                    else type(error).__name__
                )
                notes.append(f"{seat.person.email} was refused at sign-out: {reason}")
    return notes


# Tenants


RUN_TENANT_PREFIX = "ops-"
"""Every tenant a run creates has a slug that starts with this, and no other
tenant does: `size` leaves these out of what it reports."""


def is_run_tenant(slug: str) -> bool:
    return slug.startswith(RUN_TENANT_PREFIX)


class DeployedSignIn(ValueError):
    """A run against a deployed environment, refused before it provisions
    anything: its people would sign in by the local sign-in, which only the
    local stack serves."""

    def __init__(self, env: Environment) -> None:
        super().__init__(
            f"{env.name!r} signs people in through the identity provider, in a browser; "
            "the traffic generator's people sign in by the local sign-in, which only the "
            "local stack serves, so no run starts there"
        )


class TokenRefused(ValueError):
    """The operator plane refused an operator token: it expired, or it was
    never minted. The message names the command that writes a fresh one."""

    def __init__(self, env: Environment, identity: str) -> None:
        super().__init__(
            f"the {identity} token of {env.name!r} was refused (expired, or never written); "
            "write a fresh one with "
            f"`uv run acme-ops token --env {env.name} --identity {identity}`"
        )


@dataclass(frozen=True)
class Tenants:
    people: list[Person]
    orgs_created: list[UUID]
    notes: list[str] = field(default_factory=list)


async def seeded_people(env: Environment) -> Tenants:
    if env.seed is None:
        raise ValueError(f"environment {env.name!r} names no seeded people; provision with --orgs")
    seed = env.seed
    people = [
        Person(seed.owner_email, seed.slug),
        Person(seed.member_email, seed.slug),
    ]
    return Tenants(people, [], [f"orgs 0: the seeded org {seed.slug!r} and its two people"])


def provisioner_client(
    env: Environment, transport: httpx.AsyncBaseTransport | None = None
) -> ApiClient:
    if not env.provisioner_token:
        raise ValueError(
            f"environment {env.name!r} holds no provisioner token; "
            f"write one with `uv run acme-ops token --env {env.name} --identity provisioner`"
        )
    return ApiClient(
        env.api_url,
        app=OPERATOR_APP,
        app_version=app_version(),
        token=env.provisioner_token,
        transport=transport,
    )


async def provision(
    env: Environment,
    profile: Profile,
    transport: httpx.AsyncBaseTransport | None = None,
    *,
    run_id: str | None = None,
) -> Tenants:
    """Tenants for the run through the operator plane, under the
    provisioner's token, named `ops-<run id>-<n>`. A creation that fails part
    way removes what it made before it raises, so a failed start leaves
    nothing behind."""
    run = run_id or datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    people: list[Person] = []
    org_ids: list[UUID] = []
    async with provisioner_client(env, transport) as client:
        try:
            for n in range(profile.orgs):
                slug = f"{RUN_TENANT_PREFIX}{run}-{n + 1}"
                owner = f"owner@{slug}.example.test"
                org = await client.admin_create_org(
                    f"Ops {run} {n + 1}",
                    slug,
                    owner_email=owner,
                    owner_name="Ops Owner",
                )
                org_ids.append(org.id)
                people.append(Person(owner, slug))
                for m in range(max(profile.members_per_org - 1, 0)):
                    email = f"member{m + 1}@{slug}.example.test"
                    await client.admin_add_member(
                        org.id,
                        email,
                        display_name=f"Member {m + 1}",
                    )
                    people.append(Person(email, slug))
        except ApiError as error:
            await remove_tenants(env, org_ids, transport)
            if error.status == 401:
                raise TokenRefused(env, "provisioner") from None
            raise
    return Tenants(
        people,
        org_ids,
        [
            f"provisioned {profile.orgs} org(s) named {RUN_TENANT_PREFIX}{run}-<n>, "
            f"{len(people)} people"
        ],
    )


async def remove_tenants(
    env: Environment, org_ids: list[UUID], transport: httpx.AsyncBaseTransport | None = None
) -> list[str]:
    """Deletes every org the run created, each on its own, so one refusal
    leaves the others removed. Answers a note per org it could not remove."""
    if not org_ids:
        return []
    left: list[str] = []
    async with provisioner_client(env, transport) as client:
        for org_id in org_ids:
            try:
                await client.request("DELETE", f"/v1/admin/orgs/{org_id}")
            except (ApiError, httpx.HTTPError) as error:
                reason = (
                    f"{error.status} {error.code}"
                    if isinstance(error, ApiError)
                    else type(error).__name__
                )
                left.append(
                    f"not removed: org {org_id} ({reason}); delete it on the operator plane"
                )
    return left


# The run


@dataclass
class RunResult:
    report: Report
    samples: list[Sample]
    outcomes: list[SessionOutcome]


def network_transport() -> httpx.AsyncHTTPTransport:
    return httpx.AsyncHTTPTransport(verify=trust_store())


async def run_traffic(
    env: Environment,
    profile: Profile,
    *,
    duration_seconds: float | None = None,
    orgs: int | None = None,
    ramp_seconds: float = 0.0,
    max_sessions: int | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    connect: Connect | None = None,
    people: list[Person] | None = None,
    pause_after_failure: float = FAILED_SESSION_PAUSE_SECONDS,
    login_wait: Callable[[float], Any] = asyncio.sleep,
) -> RunResult:
    """The run signs its people in, drives sessions at `profile.concurrency`
    at once over their seats in turn until the duration is up, and signs them
    out. The duration is a hard bound on the sessions: it starts before the
    sign-ins, so they come out of it, and the sign-outs follow it. A session
    past the bound is cut at its next step, and a request in flight is given
    a short grace and then abandoned. The concurrency is a semaphore. A linear ramp starts
    the workers one after another across `ramp_seconds`. `max_sessions`
    bounds the run by count instead, which is how one session is driven for a
    test. A worker whose session failed pauses before the next one, so a
    refusal never becomes a flood.

    The run signs in one person per worker at most, since that is as many as
    can drive at once, and one sign-in per person from one address is what a
    per-address rate limit has room for. A sign-in the limit refuses waits
    the window out and asks again, a bounded number of times; the deadline
    moves by what was waited, so the wait comes out of nobody's driving
    time. `login_wait` is that pause, injected by tests."""
    duration = profile.duration_seconds if duration_seconds is None else duration_seconds
    wanted_orgs = profile.orgs if orgs is None else orgs
    if env.is_cloud:
        raise DeployedSignIn(env)
    inner = transport or network_transport()
    notes: list[str] = []
    created: list[UUID] = []
    if people is None:
        try:
            tenants = (
                await seeded_people(env)
                if wanted_orgs == 0
                else await provision(
                    env,
                    profile
                    if orgs is None
                    else Profile(
                        profile.name,
                        wanted_orgs,
                        profile.members_per_org,
                        profile.concurrency,
                        profile.think_seconds,
                        profile.duration_seconds,
                    ),
                    inner,
                )
            )
        except BaseException:
            if transport is None:
                await inner.aclose()
            raise
        people = tenants.people
        created = tenants.orgs_created
        notes.extend(tenants.notes)

    samples: list[Sample] = []
    outcomes: list[SessionOutcome] = []
    started_at = datetime.now(UTC)
    started = time.monotonic()
    clock = Clock(started + duration, profile.think_seconds)
    gate = asyncio.Semaphore(profile.concurrency)
    budget = {"left": max_sessions}
    seats: list[Seat] = []

    async def worker(index: int, queue: AsyncIterator[Seat]) -> None:
        # The queue is one iterator over every seat, shared by the workers,
        # so each starts on a seat of its own.
        if ramp_seconds > 0 and profile.concurrency > 1:
            await asyncio.sleep(ramp_seconds * index / profile.concurrency)
        while clock.remaining > 0:
            if budget["left"] is not None:
                if budget["left"] <= 0:
                    return
                budget["left"] -= 1
            seat = await anext(queue)
            async with gate:
                recording = RecordingTransport(inner, also=samples)
                async with ApiClient(
                    env.api_url, app=SESSION_APP, app_version=app_version(), transport=recording
                ) as client:
                    outcome = await Session(
                        client, seat, clock, recording=recording, connect=connect
                    ).run()
                outcomes.append(outcome)
                if outcome.failure:
                    log.info("session as %s failed: %s", seat.person.email, outcome.failure)
            if budget["left"] is not None and budget["left"] <= 0:
                return
            if outcome.failure:
                await asyncio.sleep(min(pause_after_failure, max(clock.remaining, 0.0)))

    try:
        wanted = seat_order(people)[: profile.concurrency]
        signed = await sign_in(env, wanted, transport=inner, samples=samples, wait=login_wait)
        seats = signed.seats
        if signed.waited_seconds:
            # A wait for the login window is not traffic, so the duration
            # does not spend itself on it: the deadline moves by what was
            # waited and the run still drives for as long as it was asked to.
            clock.deadline += signed.waited_seconds
            notes.append(
                f"the run waited {signed.waited_seconds:.0f} s in all for the login window; "
                "the duration does not count it"
            )
        notes.append(
            f"signed in {len(seats)} of {len(wanted)} people, one sign-in each for the whole run"
        )
        notes.extend(signed.notes)
        if not seats:
            notes.append(NO_ONE_SIGNED_IN)
        queue = _cycle(seats)
        workers = [
            asyncio.create_task(worker(i, queue), name=f"traffic-{i}")
            for i in range(profile.concurrency if seats else 0)
        ]
        try:
            await asyncio.wait_for(
                asyncio.gather(*workers), max(clock.remaining, 0.0) + CUT_GRACE_SECONDS
            )
        except TimeoutError:
            notes.append("a session was still in flight past the duration and its grace; abandoned")
            for task in workers:
                task.cancel()
    finally:
        # The sign-outs and the run's tenants go when the run ends, however
        # it ended.
        try:
            notes.extend(await sign_out(env, seats, transport=inner, samples=samples))
            left = await remove_tenants(env, created, inner)
            if created:
                notes.append(
                    f"removed {len(created) - len(left)} of the run's {len(created)} org(s)"
                )
            notes.extend(left)
        finally:
            if transport is None:
                await inner.aclose()
    notes.append(
        f"profile {profile.name}: {profile.concurrency} at once, think time "
        f"{profile.think_seconds[0]:.1f} to {profile.think_seconds[1]:.1f} s"
    )
    sample = next((i for o in outcomes for i in o.write_request_ids), None)
    notes.append(
        f"sample request id: {sample} (a POST /v1/api-keys of the run; read the signals back by it)"
        if sample
        else "sample request id: none (no creating call of the run answered 201)"
    )
    sessions = Sessions(
        completed=sum(o.completed for o in outcomes),
        failed=sum(1 for o in outcomes if o.failure),
        cut=sum(o.cut for o in outcomes),
    )
    report = Report.of(
        samples,
        environment=env.name,
        profile=profile.name,
        started_at=started_at,
        duration_seconds=time.monotonic() - started,
        sessions=sessions,
        notes=notes,
    )
    return RunResult(report, samples, outcomes)


async def _cycle(seats: list[Seat]) -> AsyncIterator[Seat]:
    while True:
        for seat in seats:
            yield seat
