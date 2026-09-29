from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Index, text
from sqlalchemy.orm import Mapped

from acme.om.storage.tables.base import Base, IdentifiableMixin, TrackableMixin


class WorkItems(IdentifiableMixin, TrackableMixin, Base):
    __tablename__ = "work_items"
    # org_id leads the unique index on the idempotency key, so it gets no
    # index of its own.
    __org_id_index__ = False
    __table_args__ = (
        # The key collides within its tenant, so the index leads with it, and
        # two tenants may hold one key.
        Index("uq_work_items_org_id_idempotency_key", "org_id", "idempotency_key", unique=True),
        # The purge reads settled items across tenants by their last change.
        Index("ix_work_items_status_updated_at", "status", "updated_at"),
        # The claim walks a lane's queued items in the order they became
        # ready and stops at the first one no other worker holds.
        Index("ix_work_items_lane_status_available_at_id", "lane", "status", "available_at", "id"),
        # The sweep requeues expired leases across tenants.
        Index("ix_work_items_status_lease_expires_at", "status", "lease_expires_at"),
        # The sweep reads the item ready longest on any lane, for the backlog
        # alarm: the first entry here. The claim's index leads with the lane.
        Index(
            "ix_work_items_available_at_queued",
            "available_at",
            postgresql_where=text("status = 'queued'"),
        ),
    )
    kind: Mapped[str]
    target_id: Mapped[UUID]
    idempotency_key: Mapped[UUID]
    request_id: Mapped[UUID]
    traceparent: Mapped[str | None]
    payload: Mapped[dict[str, Any]]
    lane: Mapped[str]
    status: Mapped[str]
    available_at: Mapped[datetime]
    claimed_by: Mapped[str | None]
    claim_token: Mapped[UUID | None]
    lease_expires_at: Mapped[datetime | None]
    attempts: Mapped[int]
    max_attempts: Mapped[int]
    last_error: Mapped[str | None]
