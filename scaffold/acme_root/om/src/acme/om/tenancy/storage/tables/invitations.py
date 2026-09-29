from datetime import datetime
from uuid import UUID

from sqlalchemy import Index, text
from sqlalchemy.orm import Mapped

from acme.om.storage.tables.base import Base, IdentifiableMixin, TrackableMixin


class Invitations(IdentifiableMixin, TrackableMixin, Base):
    __tablename__ = "invitations"
    # The tenant leads the pending-address index, which serves the tenant's
    # list of pending invitations, and the expiry index, which serves the
    # sweep's reads of one tenant's invitations.
    __org_id_index__ = False
    __table_args__ = (
        # The provider's id names one invitation of one org.
        Index(
            "uq_invitations_provider_invitation_id",
            "provider_invitation_id",
            unique=True,
        ),
        # One pending invitation per address in an org.
        Index(
            "uq_invitations_pending_email",
            "org_id",
            "email",
            unique=True,
            postgresql_where=text("state = 'pending'"),
        ),
        # A tenant's invitations, for the purge of a tenant past its retention.
        Index("ix_invitations_org_id_expires_at", "org_id", "expires_at"),
        # The sweep's purge reads, across tenants, the expired ones and the
        # closed ones by their last change.
        Index("ix_invitations_expires_at", "expires_at"),
        Index("ix_invitations_updated_at", "updated_at"),
    )
    email: Mapped[str]
    role: Mapped[str]
    provider_invitation_id: Mapped[str]
    state: Mapped[str]
    expires_at: Mapped[datetime]
    accepted_user_id: Mapped[UUID | None]
