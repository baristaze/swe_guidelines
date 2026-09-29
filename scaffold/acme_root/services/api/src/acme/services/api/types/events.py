"""Wire types of the event stream: the record a client replays after the
last sequence it saw, and the projection a push carries on the channel."""

from datetime import datetime
from uuid import UUID

from pydantic import Field

from acme.services.api.types.common import View


class EventView(View):
    """One record of the tenant's append-only stream, paged by `after_seq`.
    `kind` is "<namespace>.<entity>.<action>", or an audit kind; the payload
    stays inside, an event is a record and not a second read path. The rename
    from `entity`/`entity_id`/`action` stayed under `/v1` (ADR 0006)."""

    seq: int
    kind: str
    target_id: UUID
    produced_at: datetime
    actor_id: UUID


class OperatorEventView(EventView):
    """The same record as an operator reads it, with the two provenance fields
    a tenant's own feed leaves out: the request that produced it and the app
    it came from, which is what a support investigation correlates on."""

    request_id: UUID
    app: str


class EntityChangedView(View):
    """What a push says: which record changed, how, who changed it, and where
    it sits in the stream. Every push has a record, so `seq` is always present.
    `version` is the version the change wrote, on a record that carries one
    (ADR 0061): a client that holds the record at that version reads nothing.
    Left out of the frame when the change names none, and the client reads
    the record."""

    kind: str
    target_id: UUID
    seq: int
    actor_id: UUID
    version: int | None = Field(default=None, exclude_if=lambda version: version is None)
