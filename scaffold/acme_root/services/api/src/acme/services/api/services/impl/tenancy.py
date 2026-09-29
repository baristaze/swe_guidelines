import base64
from datetime import timedelta
from uuid import UUID

from acme.om.context import IdentityContext, RequestContext, TenantContext
from acme.om.exceptions import ValidationFailed
from acme.om.idempotency.types.attempt import Attempt
from acme.om.tenancy import TenancyManagerInterface
from acme.om.tenancy.types.issued import IssuedLogin
from acme.services.api.services.tenancy import TenancyServiceInterface
from acme.services.api.types.common import clamp_limit
from acme.services.api.types.tenancy import (
    AccountDeletedView,
    AddApiKeyRequest,
    ApiKeyPageView,
    ApiKeyView,
    CreateTeamOrgRequest,
    DeleteAccountRequest,
    DeleteOrgRequest,
    DeviceSignInView,
    DeviceTokenRequest,
    DevSignInRequest,
    ExchangeSessionRequest,
    IdentityView,
    InvitationPageView,
    InvitationView,
    InviteMemberRequest,
    IssuedApiKeyView,
    IssuedLoginView,
    IssuedSessionView,
    LogoutRequest,
    MembershipChoicePageView,
    MembershipChoiceView,
    MembershipPageView,
    MembershipView,
    MeView,
    OrgDeletedView,
    OrgView,
    SecondFactorRequest,
    SessionView,
    SignedOutView,
    SignInCallbackRequest,
    SignInStartRequest,
    SignInStartView,
    SsoLinkRequest,
    SsoLinkView,
    UpdateIdentityRequest,
    UpdateMembershipRequest,
    UpdateMeRequest,
    UserPageView,
    UserView,
)


def encode_cursor(listed: str, entity_id: UUID) -> str:
    """Opaque on the wire: the list a cursor belongs to and the id its page
    ended on. Each list that pages this way is ordered by one unique id -
    members, orgs, and files ascending, memberships by user id ascending,
    keys newest first - so the id is the whole mark."""
    return base64.urlsafe_b64encode(f"{listed}|{entity_id}".encode()).decode().rstrip("=")


def decode_cursor(listed: str, cursor: str) -> UUID:
    """The cursor of the list asked for; one from another list, or from
    nowhere, is refused rather than read as an id."""
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        issued_for, entity_id = raw.split("|")
        if issued_for != listed:
            raise ValueError(issued_for)
        return UUID(entity_id)
    except ValueError:
        raise ValidationFailed("the cursor is not one this list issued") from None


def login_view(issued: IssuedLogin) -> IssuedLoginView:
    return IssuedLoginView(
        token=issued.token,
        expires_at=issued.expires_at,
        memberships=[MembershipChoiceView.model_validate(m) for m in issued.memberships],
    )


class TenancyServiceImpl(TenancyServiceInterface):
    def __init__(self, tenancy: TenancyManagerInterface) -> None:
        self._tenancy = tenancy

    async def start_sign_in(
        self, rctx: RequestContext, body: SignInStartRequest
    ) -> SignInStartView:
        started = await self._tenancy.sign_in_url(
            rctx,
            body.redirect_uri,
            body.state,
            invitation_token=body.invitation_token,
            sign_up=body.sign_up,
        )
        return SignInStartView(
            authorization_url=started.authorization_url, code_verifier=started.code_verifier
        )

    async def finish_sign_in(
        self, rctx: RequestContext, body: SignInCallbackRequest
    ) -> IssuedLoginView:
        issued = await self._tenancy.sign_in_with_code(
            rctx, body.code, body.invitation_token, code_verifier=body.code_verifier
        )
        return login_view(issued)

    async def start_device_sign_in(self, rctx: RequestContext) -> DeviceSignInView:
        started = await self._tenancy.start_device_sign_in(rctx)
        return DeviceSignInView.model_validate(started, from_attributes=True)

    async def finish_device_sign_in(
        self, rctx: RequestContext, body: DeviceTokenRequest
    ) -> IssuedLoginView:
        return login_view(await self._tenancy.finish_device_sign_in(rctx, body.device_code))

    async def dev_sign_in(self, rctx: RequestContext, body: DevSignInRequest) -> IssuedLoginView:
        return login_view(await self._tenancy.dev_sign_in(rctx, body.email, body.display_name))

    async def verify_second_factor(
        self, ictx: IdentityContext, body: SecondFactorRequest
    ) -> IssuedLoginView:
        return login_view(await self._tenancy.verify_second_factor(ictx, body.totp_code))

    async def exchange_session(
        self, ictx: IdentityContext, body: ExchangeSessionRequest
    ) -> IssuedSessionView:
        issued = await self._tenancy.exchange_login(ictx, body.org_id)
        return IssuedSessionView.model_validate(issued)

    async def get_identity_memberships(
        self, ictx: IdentityContext, cursor: str | None, limit: int
    ) -> MembershipChoicePageView:
        limit = clamp_limit(limit)
        after = decode_cursor("identity-memberships", cursor) if cursor else None
        page = await self._tenancy.get_identity_memberships(ictx, after, limit)
        return MembershipChoicePageView(
            items=[MembershipChoiceView.model_validate(m) for m in page.items],
            next_cursor=(
                encode_cursor("identity-memberships", page.items[-1].user.id)
                if page.has_more
                else None
            ),
        )

    async def logout(self, ictx: IdentityContext, body: LogoutRequest | None) -> SignedOutView:
        signed_out = await self._tenancy.logout(ictx, body.return_to if body else None)
        return SignedOutView.model_validate(signed_out.session).model_copy(
            update={"provider_logout_url": signed_out.provider_logout_url}
        )

    async def delete_account(
        self, ctx: TenantContext, body: DeleteAccountRequest
    ) -> AccountDeletedView:
        deleted = await self._tenancy.delete_account(ctx, body.email, body.return_to)
        return AccountDeletedView.model_validate(deleted)

    async def get_me(self, ctx: TenantContext) -> MeView:
        # The context carries ids; the manager loads the user and the org in
        # one read. The role shown is the context's: an api key's is capped.
        me = await self._tenancy.get_me(ctx)
        return MeView(
            user=UserView.model_validate(me.user),
            org=OrgView.model_validate(me.org),
            role=ctx.security.role,
            permissions=ctx.security.permissions,
            app=ctx.app.type.value,
        )

    async def update_me(self, ctx: TenantContext, body: UpdateMeRequest) -> UserView:
        user = await self._tenancy.rename_user(ctx, ctx.user_id, body.display_name)
        return UserView.model_validate(user)

    async def get_identity(self, ctx: TenantContext) -> IdentityView:
        return IdentityView.model_validate(await self._tenancy.get_identity(ctx))

    async def update_identity(
        self, ctx: TenantContext, body: UpdateIdentityRequest
    ) -> IdentityView:
        return IdentityView.model_validate(await self._tenancy.set_time_zone(ctx, body.time_zone))

    async def get_org(self, ctx: TenantContext) -> OrgView:
        return OrgView.model_validate(await self._tenancy.get_org(ctx))

    async def delete_org(self, ctx: TenantContext, body: DeleteOrgRequest) -> OrgDeletedView:
        return OrgDeletedView.model_validate(await self._tenancy.delete_org(ctx, body.name))

    async def create_org(
        self, ctx: TenantContext, body: CreateTeamOrgRequest, attempt: Attempt
    ) -> MembershipChoiceView:
        place = await self._tenancy.create_org(ctx, body.name, body.slug, attempt)
        return MembershipChoiceView.model_validate(place)

    async def get_users(self, ctx: TenantContext, cursor: str | None, limit: int) -> UserPageView:
        # The public page size is clamped here and again by the manager; the
        # manager's lookahead past it is what makes `has_more` true.
        limit = clamp_limit(limit)
        after = decode_cursor("users", cursor) if cursor else None
        page = await self._tenancy.get_users(ctx, after, limit)
        return UserPageView(
            items=[UserView.model_validate(u) for u in page.items],
            next_cursor=encode_cursor("users", page.items[-1].id) if page.has_more else None,
        )

    async def get_memberships(
        self, ctx: TenantContext, cursor: str | None, limit: int
    ) -> MembershipPageView:
        limit = clamp_limit(limit)
        after = decode_cursor("memberships", cursor) if cursor else None
        page = await self._tenancy.get_memberships(ctx, after, limit)
        return MembershipPageView(
            items=[MembershipView.model_validate(m) for m in page.items],
            next_cursor=(
                encode_cursor("memberships", page.items[-1].user_id) if page.has_more else None
            ),
        )

    async def update_membership_role(
        self, ctx: TenantContext, user_id: UUID, body: UpdateMembershipRequest
    ) -> MembershipView:
        updated = await self._tenancy.update_membership_role(ctx, user_id, body.role)
        return MembershipView.model_validate(updated)

    async def remove_member(self, ctx: TenantContext, user_id: UUID) -> UserView:
        return UserView.model_validate(await self._tenancy.remove_member(ctx, user_id))

    async def get_invitations(
        self, ctx: TenantContext, cursor: str | None, limit: int
    ) -> InvitationPageView:
        limit = clamp_limit(limit)
        after = decode_cursor("invitations", cursor) if cursor else None
        page = await self._tenancy.get_invitations(ctx, after, limit)
        return InvitationPageView(
            items=[InvitationView.model_validate(i) for i in page.items],
            next_cursor=encode_cursor("invitations", page.items[-1].id) if page.has_more else None,
        )

    async def invite_member(
        self, ctx: TenantContext, body: InviteMemberRequest, attempt: Attempt
    ) -> InvitationView:
        invitation = await self._tenancy.invite_member(ctx, body.email, body.role, attempt)
        return InvitationView.model_validate(invitation)

    async def resend_invitation(self, ctx: TenantContext, invitation_id: UUID) -> InvitationView:
        return InvitationView.model_validate(
            await self._tenancy.resend_invitation(ctx, invitation_id)
        )

    async def revoke_invitation(self, ctx: TenantContext, invitation_id: UUID) -> InvitationView:
        return InvitationView.model_validate(
            await self._tenancy.revoke_invitation(ctx, invitation_id)
        )

    async def sso_link(self, ctx: TenantContext, body: SsoLinkRequest) -> SsoLinkView:
        url = await self._tenancy.sso_setup_link(ctx, body.intent, body.return_url)
        return SsoLinkView(url=url)

    async def get_sessions(self, ctx: TenantContext, limit: int) -> list[SessionView]:
        sessions = await self._tenancy.get_sessions(ctx, clamp_limit(limit))
        return [SessionView.model_validate(s) for s in sessions]

    async def revoke_session(self, ctx: TenantContext, session_id: UUID) -> SessionView:
        return SessionView.model_validate(await self._tenancy.revoke_session(ctx, session_id))

    async def get_api_keys(
        self, ctx: TenantContext, cursor: str | None, limit: int
    ) -> ApiKeyPageView:
        limit = clamp_limit(limit)
        after = decode_cursor("api-keys", cursor) if cursor else None
        page = await self._tenancy.get_api_keys(ctx, after, limit)
        return ApiKeyPageView(
            items=[ApiKeyView.model_validate(k) for k in page.items],
            next_cursor=encode_cursor("api-keys", page.items[-1].id) if page.has_more else None,
        )

    async def create_api_key(
        self, ctx: TenantContext, body: AddApiKeyRequest, attempt: Attempt
    ) -> IssuedApiKeyView:
        ttl = timedelta(days=body.ttl_days) if body.ttl_days is not None else None
        issued = await self._tenancy.create_api_key(ctx, body.name, body.role, ttl, attempt)
        return IssuedApiKeyView(key=issued.key, api_key=ApiKeyView.model_validate(issued.api_key))

    async def revoke_api_key(self, ctx: TenantContext, api_key_id: UUID) -> ApiKeyView:
        return ApiKeyView.model_validate(await self._tenancy.revoke_api_key(ctx, api_key_id))
