from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, Index, text
from sqlalchemy.orm import Mapped, mapped_column

from acme.om.storage.tables.base import (
    Base,
    IdentifiableMixin,
    NamedMixin,
    SoftDeletableMixin,
    TrackableMixin,
)


class Orgs(IdentifiableMixin, NamedMixin, TrackableMixin, SoftDeletableMixin, Base):
    __tablename__ = "orgs"
    # An org's org_id is its own id, which the primary key already serves.
    __org_id_index__ = False
    __table_args__ = (
        # A slug is unique among the living: a deleted org frees it.
        Index("uq_orgs_slug", "slug", unique=True, postgresql_where=text("deleted_at IS NULL")),
        # One personal org per person, among the living.
        Index(
            "uq_orgs_personal_identity_id",
            "personal_identity_id",
            unique=True,
            postgresql_where=text("kind = 'personal' AND deleted_at IS NULL"),
        ),
        # One org per organization at the identity provider.
        Index(
            "uq_orgs_provider_org_id",
            "provider_org_id",
            unique=True,
            postgresql_where=text("provider_org_id IS NOT NULL AND deleted_at IS NULL"),
        ),
        # A personal org names its person, and a team org names none.
        CheckConstraint(
            "(kind = 'personal') = (personal_identity_id IS NOT NULL)",
            name="ck_orgs_personal_identity",
        ),
    )
    slug: Mapped[str]
    # The default is the release before this one's: it writes no kind, and
    # every org it makes is a team org.
    kind: Mapped[str] = mapped_column(server_default=text("'team'"))
    personal_identity_id: Mapped[UUID | None]
    provider_org_id: Mapped[str | None]
    purged_at: Mapped[datetime | None]
