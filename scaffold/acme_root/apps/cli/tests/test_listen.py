"""The listener over the in-process API and a scripted channel: each change
is fetched and told as one line, deletes are told from memory, `--mine`
filters, connection states reach stderr, a read that fails every attempt
skips the change without ending the stream, and a stream trimmed past the
listener is said on stderr and read afresh."""

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
from acme.client.types import TaskStatus, TaskView
from acme.om.opcontext import Role

CLOCK = datetime(2026, 9, 18, 9, 30, 0)
CAROL = {"email": "carol@example.test"}

Failure = tuple[Callable[[httpx.Request], bool], Exception | int]


class Flaky(Hop):
    """The in-process API, with scripted failures: the first request each
    predicate matches gets the exception raised or the status answered
    instead, in order."""

    def __init__(self, stack: Stack, failures: list[Failure]) -> None:
        super().__init__(stack.tc)
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


def is_task_read(request: httpx.Request) -> bool:
    return request.method == "GET" and request.url.path.startswith("/v1/tasks/")


def is_users_read(request: httpx.Request) -> bool:
    return request.method == "GET" and request.url.path == "/v1/users"


def test_every_change_becomes_one_line(stack: Stack) -> None:
    owner = stack.session_token(OWNER["email"])
    bob = stack.session_token(BOB["email"])
    out, err = io.StringIO(), io.StringIO()

    async def scripted(
        client: ApiClient,
        on_state: Callable[[State], None],
        on_first_open: Callable[[], Awaitable[None]],
        on_resync: Callable[[], Awaitable[None]],
    ) -> AsyncIterator[EntityChanged]:
        """Bob and the owner act while the owner listens; the changes are the
        pushes the socket would carry, read back from the event stream."""
        await on_first_open()
        on_state("connecting")
        on_state("open")
        async with stack.client(bob) as as_bob, stack.client(owner) as as_owner:
            seq = 0  # a fresh org has no events yet
            ann, bobs = (await as_owner.me()).user.id, (await as_bob.me()).user.id

            async def latest() -> EntityChanged:
                nonlocal seq
                event = (await as_owner.events_after(seq))[-1]
                seq = event.seq
                return EntityChanged.of_event(event)

            task = await as_bob.create_task("Migrate DB")
            yield await latest()
            done = await as_owner.update_task(task.id, version=task.version, status=TaskStatus.done)
            yield await latest()
            given = await as_bob.update_task(task.id, version=done.version, assignee_id=ann)
            yield await latest()
            await as_bob.delete_task(task.id, given.version)
            on_state("reconnecting")  # as if the socket dropped here
            on_state("open")
            yield await latest()
            await as_owner.create_task("Write changelog", assignee_id=bobs)
            yield await latest()

    async def drive(mine: bool) -> None:
        async with stack.client(owner) as client:
            await listen(client, mine=mine, out=out, err=err, channel=scripted, clock=lambda: CLOCK)

    asyncio.run(drive(mine=False))
    assert out.getvalue().splitlines() == [
        "listening as Ann at Ajax: the team's tasks",
        "09:30:00  Bob created a task: Migrate DB",
        "09:30:00  Ann completed a task: Migrate DB",
        "09:30:00  Bob assigned a task to Ann: Migrate DB",
        "09:30:00  Bob deleted a task: Migrate DB",
        "09:30:00  Ann created a task: Write changelog",
    ]
    assert err.getvalue().splitlines() == [
        "connection lost; reconnecting",
        "connected again; anything missed is replayed",
    ]

    out.truncate(0)
    out.seek(0)
    asyncio.run(drive(mine=True))
    lines = out.getvalue().splitlines()
    assert lines[0] == "listening as Ann at Ajax: my tasks"
    # Bob's unassigned task is not Ann's, not even when she completes it, until
    # it is assigned to her; the changelog is Bob's.
    assert [line.split("  ", 1)[1] for line in lines[1:]] == [
        "Bob assigned a task to Ann: Migrate DB",
        "Bob deleted a task: Migrate DB",
    ]


def test_a_deleted_task_seen_before_the_listener_started_still_has_a_title(stack: Stack) -> None:
    owner = stack.session_token(OWNER["email"])
    out = io.StringIO()

    async def scripted(
        client: ApiClient,
        on_state: Callable[[State], None],
        on_first_open: Callable[[], Awaitable[None]],
        on_resync: Callable[[], Awaitable[None]],
    ) -> AsyncIterator[EntityChanged]:
        await on_first_open()
        async with stack.client(owner) as as_owner:
            await as_owner.delete_task(old.id, old.version)
            deleted = (await as_owner.events_after(0))[-1]
            # Created and deleted before the listener could read it: no title to tell.
            gone = await as_owner.create_task("Gone at once")
            await as_owner.delete_task(gone.id, gone.version)
            created, deleted_again = (await as_owner.events_after(deleted.seq))[-2:]
        for event in (deleted, created, deleted_again):
            yield EntityChanged.of_event(event)

    async def drive() -> None:
        async with stack.client(owner) as client:
            # "Old one" exists when the listener preloads, so its title is remembered.
            await listen(client, mine=False, out=out, channel=scripted, clock=lambda: CLOCK)

    async def prepare() -> TaskView:
        async with stack.client(owner) as as_owner:
            return await as_owner.create_task("Old one")

    old = asyncio.run(prepare())
    asyncio.run(drive())
    assert out.getvalue().splitlines()[1:] == [
        "09:30:00  Ann deleted a task: Old one",
        "09:30:00  Ann created a task: a task",
        "09:30:00  Ann deleted a task: a task",
    ]


def test_a_stream_trimmed_past_the_listener_is_said_and_every_task_read_again(
    stack: Stack,
) -> None:
    """What happened in the trimmed stretch is not told, and stderr says so.
    The tasks are read again there, so a task made in that stretch and
    deleted after it still has its title."""
    owner = stack.session_token(OWNER["email"])
    out, err = io.StringIO(), io.StringIO()

    async def scripted(
        client: ApiClient,
        on_state: Callable[[State], None],
        on_first_open: Callable[[], Awaitable[None]],
        on_resync: Callable[[], Awaitable[None]],
    ) -> AsyncIterator[EntityChanged]:
        await on_first_open()
        async with stack.client(owner) as as_owner:
            unseen = await as_owner.create_task("Made while trimmed")
            await on_resync()
            await as_owner.delete_task(unseen.id, unseen.version)
            deleted = (await as_owner.events_after(0))[-1]
        yield EntityChanged.of_event(deleted)

    async def drive() -> None:
        async with stack.client(owner) as client:
            await listen(
                client, mine=False, out=out, err=err, channel=scripted, clock=lambda: CLOCK
            )

    asyncio.run(drive())
    assert out.getvalue().splitlines()[1:] == ["09:30:00  Ann deleted a task: Made while trimmed"]
    assert err.getvalue().splitlines() == [
        "some changes are gone from the stream; reading every task again"
    ]


def test_a_read_that_fails_every_attempt_skips_the_change_and_keeps_listening(
    stack: Stack,
) -> None:
    """The channel reconnects on its own; a task read that fails at the wire
    or with a 5xx on every attempt the client makes is this change's failure,
    told on stderr, and the next change to the task shows its state. An actor
    who cannot be looked up is told as someone."""
    owner = stack.session_token(OWNER["email"])
    bob = stack.session_token(BOB["email"])
    out, err = io.StringIO(), io.StringIO()
    # The client sends a read again on a wire failure and on an unavailable
    # answer, so a change is skipped only once every attempt has failed.
    attempts = DEFAULT_RETRIES + 1
    flaky = Flaky(
        stack,
        [
            *[(is_task_read, httpx.ConnectError("refused"))] * attempts,
            *[(is_task_read, 503)] * attempts,
            *[(is_users_read, httpx.ReadTimeout("slow"))] * attempts,
        ],
    )

    async def scripted(
        client: ApiClient,
        on_state: Callable[[State], None],
        on_first_open: Callable[[], Awaitable[None]],
        on_resync: Callable[[], Awaitable[None]],
    ) -> AsyncIterator[EntityChanged]:
        await on_first_open()
        async with stack.client(bob) as as_bob, stack.client(owner) as as_owner:
            seq = 0

            async def latest() -> EntityChanged:
                nonlocal seq
                event = (await as_owner.events_after(seq))[-1]
                seq = event.seq
                return EntityChanged.of_event(event)

            first = await as_bob.create_task("Migrate DB")
            yield await latest()  # the read fails at the wire
            await as_bob.create_task("Write changelog")
            yield await latest()  # the read answers 503
            await as_owner.update_task(first.id, version=first.version, status=TaskStatus.done)
            yield await latest()  # read fine, told
            await add_member(stack.container, stack.org_id, CAROL["email"], Role.MEMBER)
            async with stack.client(stack.session_token(**CAROL)) as as_carol:
                await as_carol.create_task("Onboard")
            yield await latest()  # the members read for the new actor times out

    async def drive() -> None:
        async with ApiClient(
            "http://test",
            app="cli",
            app_version="cli@test",
            token=owner,
            transport=flaky,
            backoff_seconds=0.0,  # the attempts are the point here, not the wait
        ) as client:
            await listen(
                client, mine=False, out=out, err=err, channel=scripted, clock=lambda: CLOCK
            )

    asyncio.run(drive())
    assert out.getvalue().splitlines() == [
        "listening as Ann at Ajax: the team's tasks",
        "09:30:00  Ann updated a task: Migrate DB",
        "09:30:00  someone created a task: Onboard",
    ]
    assert err.getvalue().splitlines() == [
        "09:30:00  a change was not shown, the task could not be read: refused",
        "09:30:00  a change was not shown, the task could not be read: unavailable: later",
    ]
    assert flaky.failures == []  # every scripted failure was met


def test_a_read_refused_with_401_ends_the_listener(stack: Stack) -> None:
    """A dead credential is not a change's failure: it ends the listener,
    which the command turns into exit 3."""
    owner = stack.session_token(OWNER["email"])

    async def scripted(
        client: ApiClient,
        on_state: Callable[[State], None],
        on_first_open: Callable[[], Awaitable[None]],
        on_resync: Callable[[], Awaitable[None]],
    ) -> AsyncIterator[EntityChanged]:
        await on_first_open()
        async with stack.client(owner) as as_owner:
            await as_owner.create_task("Migrate DB")
            yield EntityChanged.of_event((await as_owner.events_after(0))[-1])

    async def drive() -> None:
        async with ApiClient(
            "http://test",
            app="cli",
            app_version="cli@test",
            token=owner,
            transport=Flaky(stack, [(is_task_read, 401)]),
        ) as client:
            await listen(client, mine=False, out=io.StringIO(), channel=scripted)

    with pytest.raises(ApiError) as refused:
        asyncio.run(drive())
    assert refused.value.status == 401


def test_a_read_that_answers_404_keeps_the_title_for_the_delete_that_follows(
    stack: Stack,
) -> None:
    """A task edited and deleted inside one read's latency: the read of the
    edit answers 404, and the delete that follows still has the title and is
    still Ann's, so `--mine` prints it."""
    owner = stack.session_token(OWNER["email"])
    bob = stack.session_token(BOB["email"])
    out = io.StringIO()
    flaky = Flaky(stack, [(is_task_read, 404)])

    async def prepare() -> TaskView:
        async with stack.client(owner) as as_owner, stack.client(bob) as as_bob:
            ann = (await as_owner.me()).user.id
            return await as_bob.create_task("Migrate DB", assignee_id=ann)

    async def scripted(
        client: ApiClient,
        on_state: Callable[[State], None],
        on_first_open: Callable[[], Awaitable[None]],
        on_resync: Callable[[], Awaitable[None]],
    ) -> AsyncIterator[EntityChanged]:
        await on_first_open()
        async with stack.client(bob) as as_bob, stack.client(owner) as as_owner:
            seq = (await as_owner.events_after(0))[-1].seq

            async def latest() -> EntityChanged:
                nonlocal seq
                event = (await as_owner.events_after(seq))[-1]
                seq = event.seq
                return EntityChanged.of_event(event)

            edited = await as_bob.update_task(task.id, version=task.version, notes="later")
            yield await latest()  # the read answers 404: the task is already gone
            await as_bob.delete_task(task.id, edited.version)
            yield await latest()

    async def drive() -> None:
        async with ApiClient(
            "http://test", app="cli", app_version="cli@test", token=owner, transport=flaky
        ) as client:
            await listen(client, mine=True, out=out, channel=scripted, clock=lambda: CLOCK)

    task = asyncio.run(prepare())
    asyncio.run(drive())
    assert out.getvalue().splitlines() == [
        "listening as Ann at Ajax: my tasks",
        "09:30:00  Bob updated a task: Migrate DB",
        "09:30:00  Bob deleted a task: Migrate DB",
    ]
    assert flaky.failures == []
