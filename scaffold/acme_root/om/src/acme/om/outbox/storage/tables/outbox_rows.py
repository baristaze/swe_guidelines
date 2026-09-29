from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Index, text
from sqlalchemy.orm import Mapped

from acme.om.storage.tables.base import Base, CreatedMixin, IdentifiableMixin


class OutboxRows(IdentifiableMixin, CreatedMixin, Base):
    __tablename__ = "outbox_rows"
    # The sweep claims pending rows across tenants, oldest first, and purges
    # done ones by time; both walk (done_at, id). The dead letters are purged
    # by their own time, on a partial index that holds only them. No statement
    # reads the outbox by tenant, so org_id gets no index.
    __org_id_index__ = False
    __table_args__ = (
        Index("ix_outbox_rows_done_at_id", "done_at", "id"),
        Index(
            "ix_outbox_rows_failed_at", "failed_at", postgresql_where=text("failed_at IS NOT NULL")
        ),
    )
    kind: Mapped[str]
    target_id: Mapped[UUID]
    payload: Mapped[dict[str, Any]]
    actor_id: Mapped[UUID]
    request_id: Mapped[UUID]
    traceparent: Mapped[str | None]
    app: Mapped[str]
    done_at: Mapped[datetime | None]
    attempts: Mapped[int]
    next_attempt_at: Mapped[datetime | None]
    last_error: Mapped[str | None]
    failed_at: Mapped[datetime | None]
