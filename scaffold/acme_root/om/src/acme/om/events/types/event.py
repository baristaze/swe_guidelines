"""Every push is also a record. An event names the record that changed and
carries the tenant's sequence number, so a client that reconnects asks for
everything after the last one it saw. The relay of the outbox appends it
under the row's id, so relaying twice appends once. An audit entry is the
same shape: the principal and the app are on every row."""

from datetime import datetime
from uuid import UUID

from pydantic import Field

from acme.om.base import FrozenMapping, Identifiable


class Event(Identifiable):
    org_id: UUID  # carried on the event: the relay appends it with no context
    seq: int = 0  # per tenant, gapless; 0 until storage assigns it on append
    kind: str  # "<namespace>.<entity>.<created|updated|deleted>", or an audit kind
    target_id: UUID  # the record that changed
    produced_at: datetime
    actor_id: UUID  # the user whose request produced it
    request_id: UUID  # the request that produced it, from the context
    app: str  # the AppType value of the app the request came from
    # Fixed per kind: a "<namespace>.<entity>.<action>" event carries the
    # entity's snapshot (the outbox row's payload); an audit kind carries the
    # facts its producer names.
    payload: FrozenMapping = Field(default_factory=dict, validate_default=True)
