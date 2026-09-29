from datetime import datetime

from acme.om.base import Platform


class SignInDelay(Platform):
    """The run of failed sign-ins for one email, keyed on the email's digest,
    so an address that holds no identity is delayed like one that does and
    the delay says nothing about which exist. A system row: it belongs to no
    tenant and no identity."""

    email_digest: str
    failures: int
    last_failed_at: datetime
