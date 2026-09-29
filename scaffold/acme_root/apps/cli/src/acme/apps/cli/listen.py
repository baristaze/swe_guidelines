"""The realtime mode: one channel, every task change told as a line as it
happens. The push says which task changed and who did it; the task itself
is fetched, since a push is a hint and the record is the truth. A deleted
task cannot be fetched, so the listener remembers every task it has seen.
A read that fails is that change's failure, not the stream's: it is told on
stderr, the change is skipped, and the task's next change shows its state.
Only a dead credential (401) ends the listener. When the stream is trimmed
past where the listener stood, the changes between are not told: stderr says
so, the listener reads every task again, and goes on from the stream's head."""

import sys
from collections.abc import AsyncIterable, Awaitable, Callable
from datetime import datetime
from typing import TextIO
from uuid import UUID

import httpx

from acme.apps.cli.model import describe, is_mine
from acme.client.client import ApiClient, ApiError
from acme.client.envelopes import EntityChanged
from acme.client.realtime import Channel, State
from acme.client.types import TaskScope, TaskStatus, TaskView

Changes = AsyncIterable[EntityChanged]
ReadState = Callable[[], Awaitable[None]]
OpenChannel = Callable[[ApiClient, Callable[[State], None], ReadState, ReadState], Changes]


def open_channel(
    client: ApiClient,
    on_state: Callable[[State], None],
    on_first_open: ReadState,
    on_resync: ReadState,
) -> Changes:
    return Channel(client, on_state=on_state, on_first_open=on_first_open, on_resync=on_resync)


ReadFailure = (ApiError, httpx.TransportError)
"""What a read the listener makes per change can fail with."""


def ends_the_listener(error: Exception) -> bool:
    """A 401 says the credential is dead; nothing read from here on would
    succeed, and the channel is about to be refused the same way."""
    return isinstance(error, ApiError) and error.status == 401


class Names:
    """Display names by user id, refreshed once on a miss (a member who joined
    after the listener started)."""

    def __init__(self, client: ApiClient) -> None:
        self._client = client
        self._names: dict[UUID, str] = {}

    async def load(self) -> None:
        self._names = {u.id: u.display_name for u in await self._client.every_user()}

    def of(self, user_id: UUID | None) -> str:
        if user_id is None:
            return "nobody"
        return self._names.get(user_id, "someone")

    async def resolve(self, user_id: UUID) -> str:
        if user_id not in self._names:
            try:
                await self.load()
            except ReadFailure as error:
                if ends_the_listener(error):
                    raise
                # The change is still told; the actor stays someone until the
                # next miss refreshes the names.
        return self.of(user_id)


async def known_tasks(client: ApiClient) -> dict[UUID, TaskView]:
    """Every team task the caller can see now, so a later delete has a title."""
    known: dict[UUID, TaskView] = {}
    for status in (TaskStatus.open, TaskStatus.done):
        cursor: str | None = None
        while True:
            page = await client.tasks(status, TaskScope.team, cursor=cursor, limit=200)
            known.update({t.id: t for t in page.items})
            cursor = page.next_cursor
            if cursor is None:
                break
    return known


async def listen(
    client: ApiClient,
    *,
    mine: bool,
    out: TextIO = sys.stdout,
    err: TextIO = sys.stderr,
    channel: OpenChannel = open_channel,
    clock: Callable[[], datetime] = datetime.now,
) -> None:
    me = await client.me()
    names = Names(client)
    await names.load()
    # Read once the channel has its first hello, so a change committed
    # between this read and the head the channel starts from is still told.
    known: dict[UUID, TaskView] = {}

    async def remember_every_task() -> None:
        known.update(await known_tasks(client))

    async def read_again() -> None:
        # What the listener remembers is kept: a task deleted in the lost
        # stretch still has the title a later line may need.
        print(
            "some changes are gone from the stream; reading every task again", file=err, flush=True
        )
        await remember_every_task()

    scope = "my tasks" if mine else "the team's tasks"
    print(f"listening as {me.user.display_name} at {me.org.name}: {scope}", file=out, flush=True)

    last: State = "closed"

    def on_state(state: State) -> None:
        nonlocal last
        if state == "reconnecting":
            print("connection lost; reconnecting", file=err, flush=True)
        elif state == "open" and last == "reconnecting":
            print("connected again; anything missed is replayed", file=err, flush=True)
        last = state

    async for change in channel(client, on_state, remember_every_task, read_again):
        if change.entity != "task":
            continue
        before = known.get(change.target_id)
        try:
            after = await _fetch(client, change.target_id) if change.action != "deleted" else None
        except ReadFailure as error:
            if ends_the_listener(error):
                raise
            line = f"a change was not shown, the task could not be read: {error}"
            print(f"{clock():%H:%M:%S}  {line}", file=err, flush=True)
            continue
        if change.action == "deleted":
            # Only a delete says the task is over. A read that answers 404 on
            # any other change means the task went in that read's shadow and
            # the delete is still to come: what the listener remembers is what
            # gives that line its title, and under `--mine` what decides it is
            # the caller's at all, so it is kept until the delete forgets it.
            known.pop(change.target_id, None)
        elif after is not None:
            known[change.target_id] = after
        if mine and not (is_mine(before, me.user.id) or is_mine(after, me.user.id)):
            continue
        actor = await names.resolve(change.actor_id)
        line = describe(change.action, before, after, actor, names.of)
        print(f"{clock():%H:%M:%S}  {line}", file=out, flush=True)


async def _fetch(client: ApiClient, task_id: UUID) -> TaskView | None:
    """The task, or None when it is gone: deleted between the push and the
    read, which is told from what the listener remembers. Any other failure
    is the caller's to weigh."""
    try:
        return await client.task(task_id)
    except ApiError as error:
        if error.status == 404:
            return None
        raise
