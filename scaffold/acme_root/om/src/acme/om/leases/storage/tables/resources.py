from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, Index, text
from sqlalchemy.orm import Mapped, mapped_column

from acme.om.storage.tables.base import Base, IdentifiableMixin, TrackableMixin


class Resources(IdentifiableMixin, TrackableMixin, Base):
    __tablename__ = "resources"
    # org_id leads every read of a tenant's resources, so it gets no index of
    # its own.
    __org_id_index__ = False
    __table_args__ = (
        # One resource per org, kind, and the row it stands for.
        Index("uq_resources_org_id_kind_ref_id", "org_id", "kind", "ref_id", unique=True),
        # The org's resources of a kind, by id.
        Index("ix_resources_org_id_kind_id", "org_id", "kind", "id"),
        # The free ones, which the sweep offers to their lines.
        Index(
            "ix_resources_org_id_kind_free",
            "org_id",
            "kind",
            postgresql_where=text("lease_id IS NULL AND available AND retired_at IS NULL"),
        ),
        # The retired ones the purge takes, across tenants, by their retirement.
        Index(
            "ix_resources_retired_at",
            "retired_at",
            postgresql_where=text("retired_at IS NOT NULL"),
        ),
    )
    kind: Mapped[str]
    ref_id: Mapped[UUID]
    labels: Mapped[list[str]]
    max_term_seconds: Mapped[int]
    available: Mapped[bool]
    retired_at: Mapped[datetime | None]
    token: Mapped[int] = mapped_column(BigInteger)
    lease_id: Mapped[UUID | None]
    held_until: Mapped[datetime | None]
    mean_hold_seconds: Mapped[float | None]
