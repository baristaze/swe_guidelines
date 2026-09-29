"""Following an import to its end. The import runs in the worker; the CLI
opens the one channel, reads the import once the channel is open (so a
change made before that read is in it, and one made after is pushed), and
reads it again on every push about it. A push is a hint and the import is
the truth. It stops when the import succeeded, parked, or failed."""

import sys
from collections.abc import AsyncIterable, Awaitable, Callable
from typing import TextIO
from uuid import UUID

from acme.client.client import ApiClient
from acme.client.envelopes import EntityChanged
from acme.client.realtime import Channel, State
from acme.client.types import ImportView

ENTITY = "orchestration"
"""The entity an import's pushes name: `orchestrations.orchestration.updated`."""

OpenChannel = Callable[
    [ApiClient, Callable[[State], None], Callable[[], Awaitable[None]]],
    AsyncIterable[EntityChanged],
]


def open_channel(
    client: ApiClient,
    on_state: Callable[[State], None],
    on_first_open: Callable[[], Awaitable[None]],
) -> AsyncIterable[EntityChanged]:
    return Channel(client, on_state=on_state, on_first_open=on_first_open)


class Stopped(Exception):
    """The import is no longer running; the view is how it stopped."""

    def __init__(self, view: ImportView) -> None:
        super().__init__(view.status.value)
        self.view = view


def is_running(view: ImportView) -> bool:
    return view.status.value == "running"


def progress(view: ImportView) -> str:
    """One line of how far an import is: created N of M, skipped K."""
    total = "?" if view.total is None else str(view.total)
    return f"created {view.created} of {total}, skipped {view.skipped}"


def outcome(view: ImportView) -> str:
    """The last line: how the import stopped, and what a person does next."""
    match view.status.value, view.park_reason, view.fail_reason:
        case "succeeded", _, _:
            return f"imported: {progress(view)}"
        case "parked", reason, _ if reason is not None and reason.value == "plan_limit":
            return (
                f"parked at row {view.cursor + 1}: the plan's active tasks are full "
                f"({progress(view)}). An owner or an admin can change the plan in the "
                "portal, under Settings, Billing, and the import goes on by itself; "
                "or finish some tasks and press Resume on the tasks page"
            )
        case "failed", _, reason if reason is not None:
            return f"failed: {reason.value.replace('_', ' ')} ({progress(view)})"
        case status, _, _:
            return f"{status}: {progress(view)}"


async def follow(
    client: ApiClient,
    import_id: UUID,
    *,
    out: TextIO | None = None,
    err: TextIO | None = None,
    channel: OpenChannel = open_channel,
) -> ImportView:
    """Prints a progress line each time the import moves; returns it stopped.
    The streams are the process's at the call, unless named."""
    out = out or sys.stdout
    err = err or sys.stderr
    shown: str | None = None

    def show(view: ImportView) -> None:
        nonlocal shown
        line = progress(view)
        if line != shown:
            print(line, file=out, flush=True)
            shown = line

    async def read() -> None:
        view = await client.task_import(import_id)
        if not is_running(view):
            raise Stopped(view)
        show(view)

    def on_state(state: State) -> None:
        if state == "reconnecting":
            print("connection lost; reconnecting", file=err, flush=True)

    changes = aiter(channel(client, on_state, read))
    try:
        async for change in changes:
            if change.entity == ENTITY and change.target_id == import_id:
                await read()
    except Stopped as stopped:
        return stopped.view
    finally:
        # The socket closes here, not whenever the loop's shutdown gets to it.
        close = getattr(changes, "aclose", None)
        if close is not None:
            await close()
    raise RuntimeError("the channel ended before the import stopped")
