from sqlalchemy import Index
from sqlalchemy.orm import Mapped

from acme.om.storage.tables.base import Base, CreatedMixin, IdentifiableMixin


class BillingDeliveries(IdentifiableMixin, CreatedMixin, Base):
    __tablename__ = "billing_deliveries"
    # The purge of a tenant past its retention reads its marks, so org_id
    # leads that index and gets none of its own.
    __org_id_index__ = False
    __table_args__ = (
        # A tenant's marks, for the purge of a tenant past its retention.
        Index("ix_billing_deliveries_org_id_created_at", "org_id", "created_at"),
        # The sweep's purge reads the marks by their birth, across tenants.
        Index("ix_billing_deliveries_created_at", "created_at"),
    )
    event_id: Mapped[str]
    event_type: Mapped[str]
