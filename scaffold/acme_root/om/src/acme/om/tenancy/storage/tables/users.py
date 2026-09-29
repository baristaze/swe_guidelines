from uuid import UUID

from sqlalchemy import Index, text
from sqlalchemy.orm import Mapped

from acme.om.storage.tables.base import (
    Base,
    IdentifiableMixin,
    SoftDeletableMixin,
    TrackableMixin,
)


class Users(IdentifiableMixin, TrackableMixin, SoftDeletableMixin, Base):
    __tablename__ = "users"
    # org_id leads the sweep's index, so it gets no single-column one.
    __org_id_index__ = False
    __table_args__ = (
        # One live user per identity in a tenant: the rule add_member reads for,
        # held by the database so two concurrent adds cannot both win.
        Index(
            "uq_users_org_id_identity_id_live",
            "org_id",
            "identity_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # A tenant's users, living or removed: the member list, and the purge
        # of a tenant past its retention. The unique one above is among the
        # living, so it serves neither.
        Index("ix_users_org_id_deleted_at", "org_id", "deleted_at"),
        # The sweep's purge reads the removed ones by their removal, across
        # tenants; only they are in it.
        Index("ix_users_deleted_at", "deleted_at", postgresql_where=text("deleted_at IS NOT NULL")),
        Index("ix_users_identity_id", "identity_id"),
    )
    identity_id: Mapped[UUID]
    email: Mapped[str]
    display_name: Mapped[str]
