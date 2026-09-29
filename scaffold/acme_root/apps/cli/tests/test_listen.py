"""The listener over the in-process API and a scripted channel: each change is
told as one line naming who did what to which record, connection states
reach stderr, a members read that fails every attempt leaves the actor as
someone without ending the stream, a dead credential ends it, and a stream
trimmed past the listener is said on stderr."""

import asyncio
import io
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime

import httpx
import pytest
from api_support import OWNER, add_member
from cli_support import BOB, Hop, Stack

from acme.apps.cli.listen import listen
from acme.client.client import DEFAULT_RETRIES, ApiClient, ApiError
from acme.client.envelopes import EntityChanged
from acme.client.realtime import State
from acme.om.opcontext import Role

CLOCK = datetime(2026, 9, 18, 9, 30, 0)
CAROL = {"email": "carol@example.test"}
PDF = ("spec.pdf", "application/pdf", 17)

Failure = tuple[Callable[[httpx.Request], bool], Exception | int]
OnState = Callable[[State], None]
OnResync = Callable[[], Awaitable[None]]


class Flaky(Hop):
    """The in-process API, with scripted failures once armed: the first
    request each predicate matches gets the exception raised or the status
    answered instead, in order."""

    def __init__(self, stack: Stack) -> None:
        super().__init__(stack.tc)
        self.failures: list[Failure] = []

    def arm(self, *failures: Failure) -> None:
        self.failures = list(failures)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self.failures and self.failures[0][0](request):
            _, failure = self.failures.pop(0)
            if isinstance(failure, Exception):
                raise failure
            return httpx.Response(
                failure, json={"error": {"code": "unavailable", "message": "later"}}
            )
        return await super().handle_async_request(request)


def is_users_read(request: httpx.Request) -> bool:
    return request.method == "GET" and request.url.path == "/v1/users"


class Stream:
    """The pushes the socket would carry, read back from the event stream as
    each change lands."""

    def __init__(self, reader: ApiClient) -> None:
        self._reader = reader
        self._seq = 0

    async def latest(self) -> EntityChanged:
        event = (await self._reader.events_after(self._seq))[-1]
        self._seq = event.seq
        return EntityChanged.of_event(event)


def test_every_change_becomes_one_line(stack: Stack) -> None:
    owner = stack.session_token(OWNER["email"])
    bob = stack.session_token(BOB["email"])
    out, err = io.StringIO(), io.StringIO()
    targets: list[str] = []

    async def scripted(
        client: ApiClient, on_state: OnState, on_resync: OnResync
    ) -> AsyncIterator[EntityChanged]:
        """Bob and the owner act while the owner listens."""
        on_state("connecting")
        on_state("open")
        async with stack.client(bob) as as_bob, stack.client(owner) as as_owner:
            stream = Stream(as_owner)
            invited = await as_owner.invite_member("dee@example.test")
            targets.append(str(invited.id))
            yield await stream.latest()
            await as_owner.revoke_invitation(invited.id)
            on_state("reconnecting")  # as if the socket dropped here
            on_state("open")
            yield await stream.latest()
            started = await as_bob.start_upload(*PDF)
            targets.append(str(started.id))
            yield await stream.latest()

    async def drive() -> None:
        async with stack.client(owner) as client:
            await listen(client, out=out, err=err, channel=scripted, clock=lambda: CLOCK)

    asyncio.run(drive())
    invitation, file = targets
    assert out.getvalue().splitlines() == [
        "listening as Ann at Ajax",
        f"09:30:00  Ann created tenancy.invitation {invitation}",
        f"09:30:00  Ann updated tenancy.invitation {invitation}",
        f"09:30:00  Bob created media.file {file}",
    ]
    assert err.getvalue().splitlines() == [
        "connection lost; reconnecting",
        "connected again; anything missed is replayed",
    ]


def test_a_stream_trimmed_past_the_listener_is_said_and_the_listener_goes_on(
    stack: Stack,
) -> None:
    owner = stack.session_token(OWNER["email"])
    out, err = io.StringIO(), io.StringIO()

    async def scripted(
        client: ApiClient, on_state: OnState, on_resync: OnResync
    ) -> AsyncIterator[EntityChanged]:
        async with stack.client(owner) as as_owner:
            await as_owner.invite_member("dee@example.test")  # in the trimmed stretch: never told
            await on_resync()
            await as_owner.invite_member("eve@example.test")
            yield await Stream(as_owner).latest()

    async def drive() -> None:
        async with stack.client(owner) as client:
            await listen(client, out=out, err=err, channel=scripted, clock=lambda: CLOCK)

    asyncio.run(drive())
    [told] = out.getvalue().splitlines()[1:]
    assert told.startswith("09:30:00  Ann created tenancy.invitation ")
    assert err.getvalue().splitlines() == [
        "some changes are gone from the stream; going on from its head"
    ]


def test_an_actor_whose_name_cannot_be_read_is_someone_and_the_stream_goes_on(
    stack: Stack,
) -> None:
    """A member who joined after the listener started (an admin, who may
    invite) is looked up on their first change. A members read that fails at
    the wire on every attempt the client makes tells that change with someone
    as its actor; the next change looks again and names them."""
    owner = stack.session_token(OWNER["email"])
    out = io.StringIO()
    flaky = Flaky(stack)

    async def scripted(
        client: ApiClient, on_state: OnState, on_resync: OnResync
    ) -> AsyncIterator[EntityChanged]:
        await add_member(stack.container, stack.org_id, CAROL["email"], Role.ADMIN)
        async with (
            stack.client(owner) as as_owner,
            stack.client(stack.session_token(**CAROL)) as as_carol,
        ):
            stream = Stream(as_owner)
            await as_carol.invite_member("dee@example.test")
            # Armed after the listener's first read of the members: the
            # client sends a read again on a wire failure, so every attempt fails.
            attempts = DEFAULT_RETRIES + 1
            flaky.arm(*[(is_users_read, httpx.ReadTimeout("slow"))] * attempts)
            yield await stream.latest()
            await as_carol.invite_member("eve@example.test")
            yield await stream.latest()

    async def drive() -> None:
        async with ApiClient(
            "http://test",
            app="cli",
            app_version="cli@test",
            token=owner,
            transport=flaky,
            backoff_seconds=0.0,  # the attempts are the point here, not the wait
        ) as client:
            await listen(client, out=out, channel=scripted, clock=lambda: CLOCK)

    asyncio.run(drive())
    lines = [line.split("  ", 1)[1] for line in out.getvalue().splitlines()[1:]]
    assert [line.rsplit(" ", 1)[0] for line in lines] == [
        "someone created tenancy.invitation",
        "Carol created tenancy.invitation",
    ]
    assert flaky.failures == []  # every scripted failure was met


def test_a_members_read_refused_with_401_ends_the_listener(stack: Stack) -> None:
    """A dead credential is not a change's failure: it ends the listener,
    which the command turns into exit 3."""
    owner = stack.session_token(OWNER["email"])
    flaky = Flaky(stack)

    async def scripted(
        client: ApiClient, on_state: OnState, on_resync: OnResync
    ) -> AsyncIterator[EntityChanged]:
        await add_member(stack.container, stack.org_id, CAROL["email"], Role.ADMIN)
        async with stack.client(stack.session_token(**CAROL)) as as_carol:
            await as_carol.invite_member("dee@example.test")
        async with stack.client(owner) as as_owner:
            change = await Stream(as_owner).latest()
        flaky.arm((is_users_read, 401))
        yield change

    async def drive() -> None:
        async with ApiClient(
            "http://test", app="cli", app_version="cli@test", token=owner, transport=flaky
        ) as client:
            await listen(client, out=io.StringIO(), channel=scripted)

    with pytest.raises(ApiError) as refused:
        asyncio.run(drive())
    assert refused.value.status == 401
