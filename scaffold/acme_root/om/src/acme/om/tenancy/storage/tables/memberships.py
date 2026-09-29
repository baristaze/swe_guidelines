from uuid import UUID

from sqlalchemy import Index, text
from sqlalchemy.orm import Mapped

from acme.om.storage.tables.base import (
    Base,
    IdentifiableMixin,
    SoftDeletableMixin,
    TrackableMixin,
)


class Memberships(IdentifiableMixin, TrackableMixin, SoftDeletableMixin, Base):
    __tablename__ = "memberships"
    # org_id leads the sweep's index, so it gets no single-column one.
    __org_id_index__ = False
    __table_args__ = (
        # One live membership per user in a tenant: an ended one frees the key.
        Index(
            "uq_memberships_org_id_user_id",
            "org_id",
            "user_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # A tenant's memberships, as on users: the unique one is among the
        # living, and the purge of a tenant past its retention, and of the
        # memberships of the users the sweep removes, read every row.
        Index("ix_memberships_org_id_deleted_at", "org_id", "deleted_at"),
        # The sweep's purge reads the ended ones by their end, across tenants.
        Index(
            "ix_memberships_deleted_at",
            "deleted_at",
            postgresql_where=text("deleted_at IS NOT NULL"),
        ),
    )
    user_id: Mapped[UUID]
    role: Mapped[str]
    teams: Mapped[list[str]]
