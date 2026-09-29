from datetime import datetime
from uuid import UUID

from sqlalchemy import Index
from sqlalchemy.orm import Mapped

from acme.om.storage.tables.base import Base, CreatedMixin, IdentifiableMixin


class SocketTickets(IdentifiableMixin, CreatedMixin, Base):
    __tablename__ = "socket_tickets"
    # The purge reads a tenant's tickets by expiry; that index leads with
    # org_id, so org_id gets none of its own.
    __org_id_index__ = False
    __table_args__ = (
        Index("uq_socket_tickets_ticket_hash", "ticket_hash", unique=True),
        # A tenant's tickets, for the purge of a tenant past its retention.
        Index("ix_socket_tickets_org_id_expires_at", "org_id", "expires_at"),
        # The sweep's purge reads the expired ones across tenants.
        Index("ix_socket_tickets_expires_at", "expires_at"),
    )
    user_id: Mapped[UUID]
    ticket_hash: Mapped[str]
    credential_kind: Mapped[str]
    credential_id: Mapped[UUID]
    expires_at: Mapped[datetime]
    redeemed_at: Mapped[datetime | None]
