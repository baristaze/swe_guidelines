from datetime import datetime

from sqlalchemy import Index
from sqlalchemy.orm import Mapped

from acme.om.storage.tables.base import Base, IdentifiableMixin, TrackableMixin


class BillingAccounts(IdentifiableMixin, TrackableMixin, Base):
    __tablename__ = "billing_accounts"
    # One account per org: org_id leads the unique index, so it gets no index
    # of its own.
    __org_id_index__ = False
    __table_args__ = (Index("uq_billing_accounts_org_id", "org_id", unique=True),)
    customer_id: Mapped[str | None]
    subscription_id: Mapped[str | None]
    price_lookup_key: Mapped[str | None]
    status: Mapped[str | None]
    current_period_end: Mapped[datetime | None]
    cancel_at_period_end: Mapped[bool]
    quantity: Mapped[int]
    comped_plan: Mapped[str | None]
    synced_at: Mapped[datetime | None]
