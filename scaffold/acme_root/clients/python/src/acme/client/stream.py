"""Pure: where a push sits relative to the last stream position the client
saw. The socket is a hint; the stream in storage is the truth, so a gap is
closed by fetching after the cursor rather than by trusting the frame.
Mirrors the portal's `realtime/stream.ts`."""

from dataclasses import dataclass
from typing import Literal

Cursor = int | None
"""The last contiguous seq the client applied; None until the first push."""


@dataclass(frozen=True)
class Next:
    """In order: route it and advance to `cursor`."""

    kind: Literal["next"]
    cursor: int


@dataclass(frozen=True)
class Seen:
    """At or behind the cursor: already applied, drop it."""

    kind: Literal["seen"]


@dataclass(frozen=True)
class Gap:
    """Ahead of the cursor: replay after `after` first, never skip."""

    kind: Literal["gap"]
    after: int


Placement = Next | Seen | Gap


def place(cursor: Cursor, seq: int) -> Placement:
    if cursor is None:
        return Next("next", seq)
    if seq <= cursor:
        return Seen("seen")
    if seq == cursor + 1:
        return Next("next", seq)
    return Gap("gap", cursor)


def behind(cursor: Cursor, head: int) -> int | None:
    """Where to replay from when the head the server reports is past the
    cursor; None when the client is caught up or has no cursor yet."""
    if cursor is None or head <= cursor:
        return None
    return cursor


def is_last_page(received: int, limit: int) -> bool:
    """A page shorter than the limit is the last one."""
    return received < limit
