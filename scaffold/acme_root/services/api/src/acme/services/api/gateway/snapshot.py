"""A snapshot a client keeps and asks for again: the body with a strong
entity tag over its bytes, and `304` with no body when the caller's
`If-None-Match` names the tag of the body it holds. The snapshot is its
audience's alone, so no shared cache keeps it, and the caller's own cache
asks again every time."""

import hashlib
from collections.abc import Awaitable
from typing import Annotated, Any

from fastapi import Depends, Header, Response
from pydantic import BaseModel

IfNoneMatchHeader = Annotated[
    str | None,
    Header(
        alias="If-None-Match",
        description="The entity tag of the snapshot the caller holds. 304, with no "
        "body, when it is still current.",
    ),
]

NOT_MODIFIED: dict[int | str, dict[str, Any]] = {
    304: {"description": "The snapshot `If-None-Match` names is current."}
}
"""The extra response a snapshot route documents."""


def entity_tag(body: bytes) -> str:
    """Equal bodies, equal tags; any change to a value changes the tag."""
    return '"' + hashlib.sha256(body).hexdigest()[:32] + '"'


def holds(if_none_match: str | None, tag: str) -> bool:
    """Whether the header names `tag`: one of a list of tags, weak or
    strong, compared weakly, as `If-None-Match` is; or `*`."""
    if if_none_match is None:
        return False
    for candidate in if_none_match.split(","):
        candidate = candidate.strip()
        if candidate == "*" or candidate.removeprefix("W/") == tag:
            return True
    return False


class Snapshot:
    """The answer of a route that serves a snapshot, given the tag the
    caller holds."""

    def __init__(self, if_none_match: str | None) -> None:
        self._if_none_match = if_none_match

    async def answer(self, view: Awaitable[BaseModel]) -> Response:
        body = (await view).model_dump_json().encode()
        tag = entity_tag(body)
        headers = {"ETag": tag, "Cache-Control": "private, no-cache"}
        if holds(self._if_none_match, tag):
            return Response(status_code=304, headers=headers)
        return Response(body, media_type="application/json", headers=headers)


def snapshot_of(if_none_match: IfNoneMatchHeader = None) -> Snapshot:
    return Snapshot(if_none_match)


Snap = Annotated[Snapshot, Depends(snapshot_of)]
