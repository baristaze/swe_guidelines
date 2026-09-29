"""The realtime mode: one channel, and every change in the org told as one
line as it happens: when, who, what they did, and to which record. The push
carries all of it: its kind names the namespace, the entity, and the action,
and its target is the record's id. The actor's name comes from the org's
members, read once and again when a push names someone new. A read of the
members that fails leaves the actor as someone; only a dead credential (401)
ends the listener. When the stream is trimmed past where the listener stood,
the changes between are not told: stderr says so, and the listener goes on
from the stream's head."""

import sys
from collections.abc import AsyncIterable, Awaitable, Callable
from datetime import datetime
from typing import TextIO
from uuid import UUID

import httpx

from acme.apps.cli.model import describe
from acme.client.client import ApiClient, ApiError
from acme.client.envelopes import EntityChanged
from acme.client.realtime import Channel, State

Changes = AsyncIterable[EntityChanged]
OnResync = Callable[[], Awaitable[None]]
OpenChannel = Callable[[ApiClient, Callable[[State], None], OnResync], Changes]


def open_channel(
    client: ApiClient, on_state: Callable[[State], None], on_resync: OnResync
) -> Changes:
    return Channel(client, on_state=on_state, on_resync=on_resync)


ReadFailure = (ApiError, httpx.TransportError)
"""What the members read the listener makes on a miss can fail with."""


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

    def of(self, user_id: UUID) -> str:
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


async def listen(
    client: ApiClient,
    *,
    out: TextIO = sys.stdout,
    err: TextIO = sys.stderr,
    channel: OpenChannel = open_channel,
    clock: Callable[[], datetime] = datetime.now,
) -> None:
    me = await client.me()
    names = Names(client)
    await names.load()

    async def say_changes_are_gone() -> None:
        print("some changes are gone from the stream; going on from its head", file=err, flush=True)

    print(f"listening as {me.user.display_name} at {me.org.name}", file=out, flush=True)

    last: State = "closed"

    def on_state(state: State) -> None:
        nonlocal last
        if state == "reconnecting":
            print("connection lost; reconnecting", file=err, flush=True)
        elif state == "open" and last == "reconnecting":
            print("connected again; anything missed is replayed", file=err, flush=True)
        last = state

    async for change in channel(client, on_state, say_changes_are_gone):
        actor = await names.resolve(change.actor_id)
        line = describe(change.kind, change.target_id, actor)
        print(f"{clock():%H:%M:%S}  {line}", file=out, flush=True)
