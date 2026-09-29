from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, Index
from sqlalchemy.orm import Mapped, mapped_column

from acme.om.storage.tables.base import Base, IdentifiableMixin


class Events(IdentifiableMixin, Base):
    __tablename__ = "events"
    # A stream read by seq: org_id leads the one compound index, so it gets no
    # index of its own, and the read by id is served by the primary key. The
    # unique (org_id, seq) is a guard on the cursor row's invariant; the append
    # takes its number from the cursor, never from this index. The trim reads
    # the bottom of a tenant's stream through it too. `produced_at` serves the
    # count across every tenant that the operator's size reads; its entries
    # land at the right edge, so an append pays little for it.
    __org_id_index__ = False
    __table_args__ = (
        Index("uq_events_org_id_seq", "org_id", "seq", unique=True),
        Index("ix_events_produced_at", "produced_at"),
    )
    # The same number as the cursor row's head, and the same width.
    seq: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str]
    target_id: Mapped[UUID]
    produced_at: Mapped[datetime]
    actor_id: Mapped[UUID]
    request_id: Mapped[UUID]
    app: Mapped[str]
    payload: Mapped[dict[str, Any]]
