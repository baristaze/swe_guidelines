from datetime import datetime
from typing import Any

from sqlalchemy import Index, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from acme.om.storage.tables.base import Base, IdentifiableMixin, TrackableMixin


class Orchestrations(IdentifiableMixin, TrackableMixin, Base):
    __tablename__ = "orchestrations"
    # org_id leads every index of a tenant's records, so it gets none of its
    # own.
    __org_id_index__ = False
    __table_args__ = (
        # A record kept per period is one per org, kind, and period: the
        # sweep that opens a day's record twice meets this key the second time.
        Index(
            "uq_orchestrations_org_id_kind_period",
            "org_id",
            "kind",
            "period",
            unique=True,
            postgresql_where=text("period IS NOT NULL"),
        ),
        # The org's records of a kind, newest first.
        Index("ix_orchestrations_org_id_kind_id", "org_id", "kind", "id"),
        # The wake reads the parked ones.
        Index("ix_orchestrations_org_id_status_updated_at", "org_id", "status", "updated_at"),
        # The sweep's purge reads the settled ones by their last change,
        # across tenants: one range per settled status.
        Index("ix_orchestrations_status_updated_at", "status", "updated_at"),
    )
    kind: Mapped[str]
    input: Mapped[dict[str, Any]]
    period: Mapped[str | None]
    status: Mapped[str]
    cursor: Mapped[int]
    total: Mapped[int | None]
    applied: Mapped[int]
    skipped: Mapped[int]
    row_errors: Mapped[list[dict[str, Any]]] = mapped_column(JSONB())
    park_reason: Mapped[str | None]
    fail_reason: Mapped[str | None]
    fail_detail: Mapped[str | None]
    finished_at: Mapped[datetime | None]
    version: Mapped[int]
