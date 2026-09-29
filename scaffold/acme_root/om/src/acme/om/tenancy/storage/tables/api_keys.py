from datetime import datetime
from uuid import UUID

from sqlalchemy import Index, text
from sqlalchemy.orm import Mapped

from acme.om.storage.tables.base import (
    Base,
    IdentifiableMixin,
    NamedMixin,
    SoftDeletableMixin,
    TrackableMixin,
)


class ApiKeys(IdentifiableMixin, NamedMixin, TrackableMixin, SoftDeletableMixin, Base):
    __tablename__ = "api_keys"
    # Full, not among the living (ADR 0019): the hash digests a fresh random
    # secret, so a revoked key's hash is never created again and stays refused.
    __table_args__ = (
        Index("uq_api_keys_key_hash", "key_hash", unique=True),
        # The sweep's purge reads the revoked keys and the expired ones, across
        # tenants.
        Index(
            "ix_api_keys_deleted_at", "deleted_at", postgresql_where=text("deleted_at IS NOT NULL")
        ),
        Index("ix_api_keys_expires_at", "expires_at"),
    )
    user_id: Mapped[UUID]
    key_hash: Mapped[str]
    role: Mapped[str]
    expires_at: Mapped[datetime]
