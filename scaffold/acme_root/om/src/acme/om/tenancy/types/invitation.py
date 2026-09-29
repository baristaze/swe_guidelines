from datetime import datetime
from enum import StrEnum
from typing import ClassVar
from uuid import UUID

from acme.om.base import Identifiable, Trackable
from acme.om.context import Role


class InvitationState(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"


class Invitation(Identifiable, Trackable):
    """A person asked to join an org, by email, with a role. The identity
    provider sends the email and holds the link; this row holds what Acme
    decides: who asked, the role the person gets, and where it stands. The
    person's membership is made when they sign in through the link. An
    invitation past `expires_at` is expired whatever its state says."""

    MANAGER_OWNED_FIELDS: ClassVar[tuple[str, ...]] = (
        "provider_invitation_id",
        "state",
        "expires_at",
        "accepted_user_id",
    )
    """The provider's id, the state its transitions own, the expiry the
    provider set, and who accepted: never copied from a caller."""

    email: str
    role: Role
    provider_invitation_id: str
    state: InvitationState = InvitationState.PENDING
    expires_at: datetime
    accepted_user_id: UUID | None = None

    def open_at(self, now: datetime) -> bool:
        """Pending and not yet expired: the one state a link still works in."""
        return self.state is InvitationState.PENDING and self.expires_at > now
