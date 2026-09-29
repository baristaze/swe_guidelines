from datetime import datetime
from uuid import UUID

from sqlalchemy import Index
from sqlalchemy.orm import Mapped

from acme.om.storage.tables.base import Base, IdentifiableMixin, TrackableMixin


class Sessions(IdentifiableMixin, TrackableMixin, Base):
    __tablename__ = "sessions"
    # The purge reads a tenant's sessions by expiry; that index leads with
    # org_id, so org_id gets none of its own.
    __org_id_index__ = False
    __table_args__ = (
        Index("uq_sessions_token_hash", "token_hash", unique=True),
        # A tenant's sessions: a person's own, and the purge of a tenant past
        # its retention.
        Index("ix_sessions_org_id_expires_at", "org_id", "expires_at"),
        # The sweep's purge reads the expired ones across tenants, the
        # system scope's sign-ins among them.
        Index("ix_sessions_expires_at", "expires_at"),
    )
    identity_id: Mapped[UUID]
    user_id: Mapped[UUID]
    token_hash: Mapped[str]
    credential_kind: Mapped[str]
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
    last_seen_at: Mapped[datetime | None]
    second_factor_at: Mapped[datetime | None]
    operator_role: Mapped[str | None]
    provider_session_id: Mapped[str | None]
