"""The members duty of the tenancy manager: the people of an org, the
invitations and the single sign-on that let them in, and their roles."""

from abc import ABC, abstractmethod
from uuid import UUID

from acme.integrations.identity import PortalIntent
from acme.om.context import Role, TenantContext
from acme.om.idempotency.types.attempt import Attempt
from acme.om.tenancy.types.invitation import Invitation
from acme.om.tenancy.types.membership import Membership
from acme.om.tenancy.types.page import InvitationPage, MembershipPage, UserPage
from acme.om.tenancy.types.user import User


class TenancyMembersManagerInterface(ABC):
    """A delegate of `TenancyManagerInterface`, reached as `tenancy.members`.
    Every operation takes `TenantContext`."""

    @abstractmethod
    async def invite_member(
        self, ctx: TenantContext, email: str, role: Role, attempt: Attempt | None = None
    ) -> Invitation:
        """Asks a person to join the org, by email, with a role capped at the
        caller's (NotAuthorized above it). The identity provider sends the
        email with the sign-in link; the org's organization there is made the
        first time. A person who is a member already is Conflict, and so is an
        address with an open invitation (send that one again instead); an
        expired one is replaced. This is the one door into an org for a person
        of a deployed environment, so a limit on an org's members is checked
        here, before anything is sent. `attempt` as on
        `TenancyCredentialsManagerInterface.create_api_key`: the invitation is
        created on its id, and a rerun finds it."""
        ...

    @abstractmethod
    async def get_invitations(
        self, ctx: TenantContext, after: UUID | None, limit: int
    ) -> InvitationPage:
        """The org's pending invitations, newest first, a page at a time, for a
        member manager."""
        ...

    @abstractmethod
    async def resend_invitation(self, ctx: TenantContext, invitation_id: UUID) -> Invitation:
        """Sends a pending invitation's email again, with a fresh expiry.
        InvitationClosed for one accepted or revoked."""
        ...

    @abstractmethod
    async def revoke_invitation(self, ctx: TenantContext, invitation_id: UUID) -> Invitation:
        """Revokes a pending invitation: its link stops working.
        InvitationClosed for one accepted or revoked."""
        ...

    @abstractmethod
    async def sso_setup_link(
        self, ctx: TenantContext, intent: PortalIntent, return_url: str
    ) -> str:
        """A short-lived link to the identity provider's admin portal, where
        an owner or an admin of a team org sets up the org's single sign-on
        (`sso`) or proves its domain (`domain_verification`) themselves. A
        personal org has no single sign-on (ValidationFailed), and only a
        member manager opens it. The org's organization at the provider is
        made the first time."""
        ...

    @abstractmethod
    async def rename_user(self, ctx: TenantContext, user_id: UUID, display_name: str) -> User:
        """Sets a user's display name in this org: the caller's own, or
        another member's with `manage_members`. Email and identity belong to
        the identity. A blank name is ValidationFailed."""
        ...

    @abstractmethod
    async def get_users(self, ctx: TenantContext, after: UUID | None, limit: int) -> UserPage:
        """The tenant's members, by id, a page at a time: `after` is the id the
        previous page ended on, and `has_more` says another follows."""
        ...

    @abstractmethod
    async def get_user(self, ctx: TenantContext, user_id: UUID) -> User: ...

    # Memberships.

    @abstractmethod
    async def get_memberships(
        self, ctx: TenantContext, after: UUID | None, limit: int
    ) -> MembershipPage:
        """The tenant's memberships, by user id, a page at a time as `get_users`
        pages: a page ending on the same user id covers the same members, so
        the two lists pair page for page."""
        ...

    @abstractmethod
    async def update_membership_role(
        self, ctx: TenantContext, user_id: UUID, role: Role
    ) -> Membership:
        """Role-capped at the caller's role, for the target's old role and its
        new one. The person of a personal org keeps their role in it
        (PersonalOrgFixed)."""
        ...

    @abstractmethod
    async def remove_member(self, ctx: TenantContext, user_id: UUID) -> User:
        """Soft-deletes the member's user in this org, ends their membership,
        and revokes every live session and api key of theirs, in one
        transaction; no list shows them, no role change reaches them, and
        each revocation is announced, so their sockets close. The person of a
        personal org is never removed from it (PersonalOrgFixed)."""
        ...

    @abstractmethod
    async def count_members(self, ctx: TenantContext) -> int:
        """How many live members the org has."""
        ...
