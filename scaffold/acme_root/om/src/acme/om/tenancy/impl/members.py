import logging
from collections.abc import Awaitable
from urllib.parse import urlsplit
from uuid import UUID

from acme.integrations.exceptions import ProviderConflict, ProviderRefused, ProviderUnavailable
from acme.integrations.identity import IdentityProviderInterface, PortalIntent
from acme.om.base import new_id, utcnow
from acme.om.context import Permission, Role, TenantContext
from acme.om.exceptions import (
    Conflict,
    InvitationClosed,
    NotAuthorized,
    NotFound,
    PersonalOrgFixed,
    Unavailable,
    ValidationFailed,
)
from acme.om.idempotency.types.attempt import Attempt
from acme.om.outbox import OutboxRelayInterface
from acme.om.outbox.types.row import OutboxRow, outbox_row
from acme.om.tenancy.impl.creates import user_payload, users_of
from acme.om.tenancy.impl.manager import TenancyOptions
from acme.om.tenancy.impl.shared import clamp, live_user
from acme.om.tenancy.members import TenancyMembersManagerInterface
from acme.om.tenancy.org import TenancyOrgManagerInterface
from acme.om.tenancy.rules import (
    check_email,
    email_digest,
    fold_email,
    is_platform_email,
    role_at_most,
)
from acme.om.tenancy.storage import TenancyStorageInterface
from acme.om.tenancy.types.invitation import Invitation, InvitationState
from acme.om.tenancy.types.membership import Membership
from acme.om.tenancy.types.org import Org
from acme.om.tenancy.types.page import InvitationPage, MembershipPage, UserPage
from acme.om.tenancy.types.user import User

log = logging.getLogger(__name__)


def origin_of(url: str) -> str:
    """The scheme and the host of an address, which is what makes it this
    environment's portal or not."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower()


class TenancyMembersManagerImpl(TenancyMembersManagerInterface):
    def __init__(
        self,
        storage: TenancyStorageInterface,
        relay: OutboxRelayInterface,
        options: TenancyOptions,
        *,
        org: TenancyOrgManagerInterface,
        identity_provider: IdentityProviderInterface,
    ) -> None:
        self._storage = storage
        self._relay = relay
        self._options = options
        self._org = org
        self._provider = identity_provider

    # Invitations and single sign-on.

    async def invite_member(
        self, ctx: TenantContext, email: str, role: Role, attempt: Attempt | None = None
    ) -> Invitation:
        ctx.require(Permission.MANAGE_MEMBERS)
        if role is Role.SERVICE:
            raise ValidationFailed("service is not a membership role")
        if not role_at_most(role, ctx.security.role):
            raise NotAuthorized(f"cannot invite as {role.value}, above {ctx.security.role.value}")
        email = fold_email(email.strip())
        if is_platform_email(email):
            raise ValidationFailed("that address belongs to the platform")
        try:
            check_email(email)
        except ValueError as error:
            raise ValidationFailed(str(error)) from None
        invitation_id = new_id() if attempt is None else attempt.target_id
        if attempt is not None:
            rerun = await self._storage.read_invitation(ctx.org_id, invitation_id)
            if rerun is not None:
                return rerun
        await self._refuse_member(ctx, email)
        now = utcnow()
        pending = await self._storage.read_pending_invitation(ctx.org_id, email)
        if pending is not None and pending.open_at(now):
            raise Conflict("an invitation for this address is pending; send it again instead")
        org = await self._provider_org(ctx)
        assert org.provider_org_id is not None
        if pending is not None:
            # Expired: it closes, and the new one takes its place.
            await self._close_invitation(ctx, pending, InvitationState.REVOKED)
        try:
            sent = await self._provider.send_invitation(
                email=email,
                organization_id=org.provider_org_id,
                expires_in_days=self._options.invitation_ttl_days,
                deadline=ctx.deadline,
            )
        except ProviderConflict:
            # Pending at the provider already (an earlier attempt sent it and
            # lost its answer): that one is adopted.
            found = await self._provider_call(
                self._provider.find_pending_invitation(
                    email=email, organization_id=org.provider_org_id, deadline=ctx.deadline
                )
            )
            if found is None:
                raise Conflict("the invitation could not be sent; try again") from None
            sent = found
        except ProviderRefused as error:
            raise ValidationFailed(f"the invitation was not sent: {error.message}") from None
        except ProviderUnavailable as error:
            raise Unavailable(f"invitations are not available: {error.message}") from None
        invitation = Invitation(
            id=invitation_id,
            created_at=now,
            updated_at=now,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
            email=email,
            role=role,
            provider_invitation_id=sent.id,
            expires_at=sent.expires_at,
        )
        row = outbox_row(ctx, "tenancy.invitation.created", invitation.id, {})
        await self._storage.write_invitation(ctx.org_id, invitation, (row,))
        await self._relay.relay(ctx.org_id, row)
        return invitation

    async def get_invitations(
        self, ctx: TenantContext, after: UUID | None, limit: int
    ) -> InvitationPage:
        ctx.require(Permission.MANAGE_MEMBERS)
        limit = clamp(limit, self._options.max_limit)
        rows = await self._storage.read_invitations(ctx.org_id, after, limit + 1)
        return InvitationPage(items=tuple(rows[:limit]), has_more=len(rows) > limit)

    async def resend_invitation(self, ctx: TenantContext, invitation_id: UUID) -> Invitation:
        ctx.require(Permission.MANAGE_MEMBERS)
        invitation = await self._pending_invitation(ctx, invitation_id)
        sent = await self._provider_call(
            self._provider.resend_invitation(
                invitation.provider_invitation_id, deadline=ctx.deadline
            )
        )
        now = utcnow()
        resent = invitation.model_copy(
            update={
                "provider_invitation_id": sent.id,
                "expires_at": sent.expires_at,
                "updated_at": now,
                "updated_by": ctx.user_id,
            }
        )
        row = outbox_row(ctx, "tenancy.invitation.updated", resent.id, {})
        await self._storage.write_invitation(ctx.org_id, resent, (row,))
        await self._relay.relay(ctx.org_id, row)
        return resent

    async def revoke_invitation(self, ctx: TenantContext, invitation_id: UUID) -> Invitation:
        ctx.require(Permission.MANAGE_MEMBERS)
        invitation = await self._pending_invitation(ctx, invitation_id)
        if invitation.open_at(utcnow()):
            try:
                await self._provider.revoke_invitation(
                    invitation.provider_invitation_id, deadline=ctx.deadline
                )
            except ProviderRefused as error:
                # Accepted or expired at the provider meanwhile: closed here too.
                log.info("the provider refused the revocation: %s", error.message)
            except ProviderUnavailable as error:
                raise Unavailable(f"invitations are not available: {error.message}") from None
        return await self._close_invitation(ctx, invitation, InvitationState.REVOKED)

    async def sso_setup_link(
        self, ctx: TenantContext, intent: PortalIntent, return_url: str
    ) -> str:
        ctx.require(Permission.MANAGE_MEMBERS)
        if origin_of(return_url) not in {origin_of(u) for u in self._options.sign_in_redirect_uris}:
            raise ValidationFailed("the link comes back to this environment's portal only")
        current = await self._org.get_org(ctx)
        if current.personal:
            raise ValidationFailed("single sign-on is a team org's; a personal org has none")
        org = await self._provider_org(ctx)
        assert org.provider_org_id is not None
        return await self._provider_call(
            self._provider.portal_link(
                organization_id=org.provider_org_id,
                intent=intent,
                return_url=return_url,
                deadline=ctx.deadline,
            )
        )

    async def _provider_org(self, ctx: TenantContext) -> Org:
        """The org, with its organization at the identity provider, made the
        first time and kept: the provider finds it by the org's id when a
        write of the link was lost, so a rerun never makes a second one."""
        org = await self._org.get_org(ctx)
        if org.provider_org_id is not None:
            return org
        provided = await self._provider_call(
            self._provider.ensure_organization(
                external_id=str(org.id), name=org.name, deadline=ctx.deadline
            )
        )
        linked = org.model_copy(
            update={
                "provider_org_id": provided.id,
                "updated_at": utcnow(),
                "updated_by": ctx.user_id,
            }
        )
        await self._storage.write_org(ctx.org_id, linked)
        return linked

    async def _provider_call[T](self, call: Awaitable[T]) -> T:
        """A provider call of the principal's operations, its refusals said as
        the platform's own."""
        try:
            return await call
        except ProviderConflict as error:
            raise Conflict(error.message) from None
        except ProviderRefused as error:
            raise ValidationFailed(error.message) from None
        except ProviderUnavailable as error:
            raise Unavailable(f"the identity provider is not available: {error.message}") from None

    async def _refuse_member(self, ctx: TenantContext, email: str) -> None:
        """An address whose person is a member of the org already is Conflict."""
        identity = await self._storage.read_identity_by_email_digest(email_digest(email))
        if identity is None:
            return
        most = self._options.max_orgs_per_identity
        for member_org_id, user in await users_of(self._storage, identity.id, most):
            if member_org_id == ctx.org_id and user.deleted_at is None:
                raise Conflict("that person is a member already")

    async def _pending_invitation(self, ctx: TenantContext, invitation_id: UUID) -> Invitation:
        invitation = await self._storage.read_invitation(ctx.org_id, invitation_id)
        if invitation is None:
            raise NotFound(f"invitation {invitation_id} not found")
        if invitation.state is not InvitationState.PENDING:
            raise InvitationClosed(f"the invitation is {invitation.state.value}")
        return invitation

    async def _close_invitation(
        self, ctx: TenantContext, invitation: Invitation, state: InvitationState
    ) -> Invitation:
        closed = invitation.model_copy(
            update={"state": state, "updated_at": utcnow(), "updated_by": ctx.user_id}
        )
        row = outbox_row(ctx, "tenancy.invitation.updated", closed.id, {})
        await self._storage.write_invitation(ctx.org_id, closed, (row,))
        await self._relay.relay(ctx.org_id, row)
        return closed

    async def rename_user(self, ctx: TenantContext, user_id: UUID, display_name: str) -> User:
        ctx.require(Permission.READ)
        if user_id != ctx.user_id:
            ctx.require(Permission.MANAGE_MEMBERS)
        existing = await live_user(self._storage, ctx, user_id)
        if not display_name.strip():
            raise ValidationFailed("display name is required")
        # model_copy does not validate; the copy carries caller input, so it does.
        updated = User.model_validate(
            {
                **existing.model_dump(),
                "display_name": display_name,
                "updated_at": utcnow(),
                "updated_by": ctx.user_id,
            }
        )
        await self._write_user(ctx, updated, "updated")
        return updated

    async def get_users(self, ctx: TenantContext, after: UUID | None, limit: int) -> UserPage:
        ctx.require(Permission.READ)
        limit = clamp(limit, self._options.max_limit)
        rows = await self._storage.read_users(ctx.org_id, after, limit + 1)
        return UserPage(items=tuple(rows[:limit]), has_more=len(rows) > limit)

    async def get_user(self, ctx: TenantContext, user_id: UUID) -> User:
        ctx.require(Permission.READ)
        user = await self._storage.read_user(ctx.org_id, user_id)
        if user is None or user.deleted_at is not None:
            raise NotFound(f"user {user_id} not found")
        return user

    # Memberships.

    async def get_memberships(
        self, ctx: TenantContext, after: UUID | None, limit: int
    ) -> MembershipPage:
        ctx.require(Permission.READ)
        limit = clamp(limit, self._options.max_limit)
        rows = await self._storage.read_memberships(ctx.org_id, limit + 1, after)
        return MembershipPage(items=tuple(rows[:limit]), has_more=len(rows) > limit)

    async def update_membership_role(
        self, ctx: TenantContext, user_id: UUID, role: Role
    ) -> Membership:
        ctx.require(Permission.MANAGE_MEMBERS)
        if role is Role.SERVICE:
            raise ValidationFailed("service is not a membership role")
        if user_id == ctx.user_id:
            raise ValidationFailed("a member cannot change their own role")
        membership = await self._live_membership(ctx, user_id)
        await self._refuse_personal_owner(ctx, user_id, "keeps its owner")
        if not role_at_most(membership.role, ctx.security.role):
            raise NotAuthorized("cannot change the role of a member above your own")
        if not role_at_most(role, ctx.security.role):
            raise NotAuthorized(f"cannot grant role {role.value} above {ctx.security.role.value}")
        updated = membership.model_copy(
            update={"role": role, "updated_at": utcnow(), "updated_by": ctx.user_id}
        )
        row = outbox_row(
            ctx, "tenancy.membership.updated", updated.id, {"user_id": str(updated.user_id)}
        )
        await self._storage.write_membership(ctx.org_id, updated, (row,))
        await self._relay.relay(ctx.org_id, row)
        return updated

    async def remove_member(self, ctx: TenantContext, user_id: UUID) -> User:
        ctx.require(Permission.MANAGE_MEMBERS)
        if user_id == ctx.user_id:
            raise ValidationFailed("a member cannot remove themselves")
        membership = await self._live_membership(ctx, user_id)
        user = await live_user(self._storage, ctx, user_id)
        await self._refuse_personal_owner(ctx, user_id, "keeps its owner")
        if not role_at_most(membership.role, ctx.security.role):
            raise NotAuthorized("cannot remove a member above your own role")
        now = utcnow()
        removed = user.model_copy(
            update={
                "deleted_at": now,
                "deleted_by": ctx.user_id,
                "updated_at": now,
                "updated_by": ctx.user_id,
            }
        )
        ended = membership.model_copy(
            update={
                "deleted_at": now,
                "deleted_by": ctx.user_id,
                "updated_at": now,
                "updated_by": ctx.user_id,
            }
        )
        row = outbox_row(ctx, "tenancy.user.deleted", removed.id, user_payload(removed))
        rows = (row,)

        def revocation(kind: str, credential_id: UUID) -> OutboxRow:
            return outbox_row(ctx, kind, credential_id, {"user_id": str(user_id)})

        # One commit: the user, the membership, every live session and api
        # key of theirs, and a row for each. A failure leaves the member whole
        # with every credential; a success leaves no credential of theirs, so
        # no key stays listed and no session stays live.
        revocations = await self._storage.remove_member(
            ctx.org_id, removed, ended, rows, revocation
        )
        # The removal is announced first, so a socket of theirs closes because
        # their membership ended, not because a credential was revoked; then
        # each revocation, as the record it is. Every row is durable already:
        # whatever a crash leaves unrelayed, the sweep relays.
        await self._relay.relay_all(ctx.org_id, (*rows, *revocations))
        return removed

    async def count_members(self, ctx: TenantContext) -> int:
        ctx.require(Permission.READ)
        return await self._storage.count_members(ctx.org_id)

    # The core row and its outbox row land in one storage call; the relay then
    # appends the event and pushes at once, and the sweep catches what a crash
    # left behind.

    async def _write_user(self, ctx: TenantContext, user: User, action: str) -> None:
        row = outbox_row(ctx, f"tenancy.user.{action}", user.id, user_payload(user))
        await self._storage.write_user(ctx.org_id, user, (row,))
        await self._relay.relay(ctx.org_id, row)

    async def _live_membership(self, ctx: TenantContext, user_id: UUID) -> Membership:
        """The membership of a live user, or NotFound: a removed member has no
        membership to read or change, whatever the row says."""
        await live_user(self._storage, ctx, user_id)
        membership = await self._storage.read_membership_for_user(ctx.org_id, user_id)
        if membership is None or membership.deleted_at is not None:
            raise NotFound(f"membership of user {user_id} not found")
        return membership

    async def _refuse_personal_owner(self, ctx: TenantContext, user_id: UUID, rule: str) -> None:
        """A personal org belongs to its person for good: nobody removes them
        from it or changes their role, so it never changes hands. Anyone else
        in it is an ordinary member."""
        org = await self._storage.read_org(ctx.org_id)
        if org is None or not org.personal:
            return
        user = await live_user(self._storage, ctx, user_id)
        if user.identity_id == org.personal_identity_id:
            raise PersonalOrgFixed(f"a personal org {rule}")
