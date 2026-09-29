from datetime import datetime

from sqlalchemy import Index
from sqlalchemy.orm import Mapped

from acme.om.storage.tables.base import Base, GlobalIdentifiableMixin


class SignInDelays(GlobalIdentifiableMixin, Base):
    """One row per email that failed a sign-in, known or not: a system table."""

    __tablename__ = "sign_in_delays"
    __table_args__ = (Index("uq_sign_in_delays_email_digest", "email_digest", unique=True),)
    email_digest: Mapped[str]
    failures: Mapped[int]
    last_failed_at: Mapped[datetime]
