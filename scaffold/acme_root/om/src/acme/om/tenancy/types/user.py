from typing import ClassVar
from uuid import UUID

from acme.om.base import Identifiable, SoftDeletable, Trackable

PERSONAL_FIELDS = frozenset({"email", "display_name"})
"""The fields that say who a person is. The user row holds them and the purge
that erases a person removes them; the event stream, which outlives a removed
member, never carries them."""


class User(Identifiable, Trackable, SoftDeletable):
    MANAGER_OWNED_FIELDS: ClassVar[tuple[str, ...]] = ("identity_id", "email")
    """The person behind the user and their address belong to the identity; an
    update of a user copies the display name and nothing else."""

    identity_id: UUID
    email: str
    display_name: str
