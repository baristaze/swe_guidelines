from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, Index, text
from sqlalchemy.orm import Mapped, mapped_column

from acme.om.storage.tables.base import Base, IdentifiableMixin, TrackableMixin


class Leases(IdentifiableMixin, TrackableMixin, Base):
    __tablename__ = "leases"
    __org_id_index__ = False
    __table_args__ = (
        # The second fence: one active lease per resource, whatever the
        # anchor's lock let through.
        Index(
            "uq_leases_org_id_resource_id_active",
            "org_id",
            "resource_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        # One lease per request.
        Index("uq_leases_org_id_request_id", "org_id", "request_id", unique=True),
        # The org's lapsed leases, and every tenant's for the sweep.
        Index(
            "ix_leases_org_id_expires_at_active",
            "org_id",
            "expires_at",
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "ix_leases_expires_at_active", "expires_at", postgresql_where=text("status = 'active'")
        ),
        # The ended ones the purge takes, across tenants, by their end.
        Index("ix_leases_ended_at", "ended_at", postgresql_where=text("ended_at IS NOT NULL")),
    )
    resource_id: Mapped[UUID]
    request_id: Mapped[UUID]
    holder_id: Mapped[UUID]
    token: Mapped[int] = mapped_column(BigInteger)
    term_seconds: Mapped[int]
    expires_at: Mapped[datetime]
    status: Mapped[str]
    ended_at: Mapped[datetime | None]
