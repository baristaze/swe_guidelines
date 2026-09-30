"""A request body read as bytes and bounded while it is read: the largest file
any purpose accepts, and not a byte more. A route that moves a file's bytes
through the API (for a store that cannot presign) binds it; the manager then
holds the bytes to the file's own size."""

from typing import Annotated

from fastapi import Depends, Request

from acme.om.exceptions import ValidationFailed
from acme.om.media.rules import BOUNDS

MAX_BODY_BYTES = max(bounds.max_bytes for bounds in BOUNDS.values())


async def bounded_body(request: Request) -> bytes:
    """The body, refused as soon as it passes the bound: a declared length
    past it before a byte is read, and a stream that runs past it as it runs.
    A length is ASCII digits alone: `isdigit` admits the digits of every
    script, some of which `int` refuses, and one written otherwise declares
    nothing. One with more digits than `int` reads is past the bound."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isascii() and declared.isdigit():
        try:
            past = int(declared) > MAX_BODY_BYTES
        except ValueError:
            past = True
        if past:
            raise ValidationFailed(f"a body is at most {MAX_BODY_BYTES} bytes")
    chunks: list[bytes] = []
    read = 0
    async for chunk in request.stream():
        read += len(chunk)
        if read > MAX_BODY_BYTES:
            raise ValidationFailed(f"a body is at most {MAX_BODY_BYTES} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


BoundedBody = Annotated[bytes, Depends(bounded_body)]
