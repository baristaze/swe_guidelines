"""The two bases every wire type uses, the error envelope, and the list limit."""

from typing import ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict

LIMIT_DEFAULT = 50
LIMIT_MAX = 200


class View(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    secret_fields: ClassVar[frozenset[str]] = frozenset()
    """The fields that carry a secret in the clear, shown once: the gateway
    stores the idempotent outcome with each of them absent, so a replay answers
    with the row and no secret. Every such field is optional on the wire."""


class RequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StreamTruncatedDetail(View):
    """What a `stream_truncated` refusal carries: the floor, the highest seq
    trimmed from the stream, and the head. A client drops its cursor, reads
    afresh what it shows, and goes on from the head."""

    floor: int
    head: int


class OwnedOrgRef(View):
    """An org named in a refusal: enough to find it and to say which."""

    id: UUID
    name: str
    slug: str


class LastOwnerDetail(View):
    """What a `last_owner` refusal carries: every team org the person is the
    last owner of, which they hand on or delete before their account goes."""

    orgs: list[OwnedOrgRef]


class ErrorBody(View):
    code: str
    message: str
    request_id: UUID
    stream: StreamTruncatedDetail | None = None
    last_owner: LastOwnerDetail | None = None


class ErrorResponse(View):
    error: ErrorBody


def clamp_limit(limit: int) -> int:
    """Lists return a bare list with a server-clamped limit."""
    return max(1, min(limit, LIMIT_MAX))
