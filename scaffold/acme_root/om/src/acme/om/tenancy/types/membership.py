from uuid import UUID

from acme.om.base import Identifiable, SoftDeletable, Trackable
from acme.om.opcontext import Role


class Membership(Identifiable, Trackable, SoftDeletable):
    """A user's place in the tenant. Removing the member ends it: the
    membership is soft-deleted beside the user, and no read lists it."""

    user_id: UUID
    role: Role
    teams: tuple[UUID, ...] = ()
