from datetime import datetime
from typing import ClassVar
from uuid import UUID

from acme.om.base import Identifiable, Named, SoftDeletable, Trackable
from acme.om.opcontext import Role


class ApiKey(Identifiable, Named, Trackable, SoftDeletable):
    """Membership-scoped, expiring, role-capped at its issuer's role. Revoking soft-deletes."""

    MANAGER_OWNED_FIELDS: ClassVar[tuple[str, ...]] = ("key_hash", "expires_at")
    """The secret's digest and its expiry are the manager's, set at the mint."""

    user_id: UUID
    key_hash: str
    role: Role
    expires_at: datetime
