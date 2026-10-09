from sqlalchemy import Index
from sqlalchemy.orm import Mapped

from acme.om.storage.tables.base import Base, IdentifiableMixin, TrackableMixin


class TenantCaps(IdentifiableMixin, TrackableMixin, Base):
    """One row per tenant and lane: the tenant's own cap there, which the
    claim joins to its count of the lane's claimed items."""

    __tablename__ = "tenant_caps"
    # org_id leads the one index, so it gets no index of its own.
    __org_id_index__ = False
    __table_args__ = (
        # One cap per tenant and lane: the operator's write and read name
        # both, and the claim's join meets each counted tenant here.
        Index("uq_tenant_caps_org_id_lane", "org_id", "lane", unique=True),
    )
    lane: Mapped[str]
    cap: Mapped[int]
