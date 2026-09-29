from uuid import UUID

from sqlalchemy import BigInteger
from sqlalchemy.orm import Mapped, mapped_column

from acme.om.storage.tables.base import Base


class EventCursors(Base):
    """One row per tenant: `head` is the last seq the append assigned. The
    append takes `head + n` for its n new events under this row's lock, in the
    statement that writes them, and holds the lock to its commit, so two
    appends to one tenant queue here, commit in the order of their numbers,
    and a rollback returns the numbers.
    The same row is the tenant's head seq, the number every pong carries.

    `floor` is the highest seq the trim removed, 0 while it removed none. The
    trim moves it in the transaction that deletes up to it, under this row's
    lock, so every event in `(floor, head]` is stored whenever a reader looks."""

    __tablename__ = "event_cursors"
    org_id: Mapped[UUID] = mapped_column(primary_key=True)
    head: Mapped[int] = mapped_column(BigInteger)
    floor: Mapped[int] = mapped_column(BigInteger, server_default="0")
