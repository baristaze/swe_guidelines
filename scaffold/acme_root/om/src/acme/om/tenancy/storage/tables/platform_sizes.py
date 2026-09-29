from datetime import datetime

from sqlalchemy import BigInteger
from sqlalchemy.orm import Mapped, mapped_column

from acme.om.storage.tables.base import Base, GlobalIdentifiableMixin


class PlatformSizes(GlobalIdentifiableMixin, Base):
    """The platform's size as the sweep last counted it: one row, the
    platform's own, whose id is `EMPTY_UUID`. It lives in the `admin` role,
    which only this count writes, so the operator plane's read of it never
    counts a role the application writes to. Derived: the next count
    rebuilds it."""

    __tablename__ = "platform_sizes"
    tenants: Mapped[int] = mapped_column(BigInteger)
    users: Mapped[int] = mapped_column(BigInteger)
    tasks_last_24h: Mapped[int] = mapped_column(BigInteger)
    events_last_24h: Mapped[int] = mapped_column(BigInteger)
    since: Mapped[datetime]
    counted_at: Mapped[datetime]
