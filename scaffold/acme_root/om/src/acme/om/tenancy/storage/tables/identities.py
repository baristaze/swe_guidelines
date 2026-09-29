from datetime import datetime

from sqlalchemy import BigInteger, Computed, Index, text
from sqlalchemy.orm import Mapped, mapped_column

from acme.om.storage.tables.base import Base, GlobalIdentifiableMixin, TrackableMixin

EMAIL_DIGEST = "encode(sha256(decode(lower(email COLLATE pg_unicode_fast), 'escape')), 'hex')"
"""The digest a sign-in looks an identity up by (`rules.email_digest`),
computed by the database from the stored address, folded, so every writer,
an older release included, leaves a row the lookup finds, and two spellings
of one address meet the unique index (ADR 0072). A generated column takes
only immutable functions. `lower` under the builtin `pg_unicode_fast`
collation is Unicode's full mapping whatever the database's locale, the one
`rules.fold_email` applies. `decode(..., 'escape')` is the immutable
function that yields the address's UTF-8 bytes; it reads a backslash as an
escape, which is why an address holds none (`rules.check_email`)."""


class Identities(GlobalIdentifiableMixin, TrackableMixin, Base):
    __tablename__ = "identities"
    __table_args__ = (
        # One identity per address. The digest is computed from the address,
        # so its index holds that rule, and it is the one the sign-in reads by.
        Index("uq_identities_email_digest", "email_digest", unique=True),
        # One identity per subject of an issuer: the provider's sign-in lookup.
        Index(
            "uq_identities_issuer_subject",
            "issuer",
            "subject",
            unique=True,
            postgresql_where=text("subject IS NOT NULL"),
        ),
    )
    email: Mapped[str]
    email_digest: Mapped[str] = mapped_column(Computed(EMAIL_DIGEST, persisted=True))
    issuer: Mapped[str | None]
    subject: Mapped[str | None]
    operator_role: Mapped[str | None]
    totp_secret: Mapped[str | None]
    totp_confirmed_at: Mapped[datetime | None]
    totp_last_step: Mapped[int | None] = mapped_column(BigInteger())
    time_zone: Mapped[str | None]
