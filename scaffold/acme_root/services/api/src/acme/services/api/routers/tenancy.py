"""Tenancy routes: sign in through the identity provider, choose or switch a
tenant, read the principal, create an org, manage members and their
invitations, open single sign-on, sessions, and api keys. Every function is
one call into the tenancy service."""

from uuid import UUID

from fastapi import APIRouter, Depends, Response

from acme.services.api.gateway.auth import Ctx, Identity, Rctx, dev_sign_in_open
from acme.services.api.gateway.idempotency import Idem
from acme.services.api.gateway.ratelimit import rate_limited
from acme.services.api.gateway.resolve import TenancyService
from acme.services.api.types.common import LIMIT_DEFAULT, ErrorResponse
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

router = APIRouter(tags=["tenancy"])


# The sign-in routes carry no Idempotency-Key: the marker is kept per tenant
# and principal, and none exists yet. Each is counted against the
# per-address sign-in budget. A code is exchanged once, so a retry after a
# lost answer is refused and the person signs in again.
@router.post("/auth/sign-in", response_model=SignInStartView, dependencies=[rate_limited("login")])
async def start_sign_in(
    rctx: Rctx, tenancy: TenancyService, body: SignInStartRequest
) -> SignInStartView:
    return await tenancy.start_sign_in(rctx, body)


@router.post("/auth/callback", response_model=IssuedLoginView, dependencies=[rate_limited("login")])
async def finish_sign_in(
    rctx: Rctx, tenancy: TenancyService, body: SignInCallbackRequest
) -> IssuedLoginView:
    return await tenancy.finish_sign_in(rctx, body)


@router.post("/auth/device", response_model=DeviceSignInView, dependencies=[rate_limited("login")])
async def start_device_sign_in(rctx: Rctx, tenancy: TenancyService) -> DeviceSignInView:
    return await tenancy.start_device_sign_in(rctx)


@router.post(
    "/auth/device/token", response_model=IssuedLoginView, dependencies=[rate_limited("login")]
)
async def finish_device_sign_in(
    rctx: Rctx, tenancy: TenancyService, body: DeviceTokenRequest
) -> IssuedLoginView:
    return await tenancy.finish_device_sign_in(rctx, body)


# Local and test only: 404 wherever it is off, as a route that does not
# exist answers, and a deployed process refuses to start with it on.
@router.post(
    "/auth/dev-sign-in",
    response_model=IssuedLoginView,
    dependencies=[Depends(dev_sign_in_open), rate_limited("login")],
)
async def dev_sign_in(
    rctx: Rctx, tenancy: TenancyService, body: DevSignInRequest
) -> IssuedLoginView:
    return await tenancy.dev_sign_in(rctx, body)


@router.post(
    "/auth/second-factor", response_model=IssuedLoginView, dependencies=[rate_limited("login")]
)
async def verify_second_factor(
    identity: Identity, tenancy: TenancyService, body: SecondFactorRequest
) -> IssuedLoginView:
    return await tenancy.verify_second_factor(identity, body)


# A sign-in is exchanged once: the exchange ends it in the write that makes
# the session, so the sign-in is the exchange's key and the route carries no
# Idempotency-Key. A retry after a lost answer is refused and the person signs
# in again (ADR 0037). Presented with a session, it is a switch.
@router.post("/auth/sessions", response_model=IssuedSessionView)
async def exchange_session(
    identity: Identity, tenancy: TenancyService, body: ExchangeSessionRequest
) -> IssuedSessionView:
    return await tenancy.exchange_session(identity, body)


@router.get("/auth/memberships", response_model=MembershipChoicePageView)
async def list_my_memberships(
    identity: Identity,
    tenancy: TenancyService,
    cursor: str | None = None,
    limit: int = LIMIT_DEFAULT,
) -> MembershipChoicePageView:
    return await tenancy.get_identity_memberships(identity, cursor, limit)


# The credential presented ends, whichever a person holds: a session, a
# sign-in (with or without its second factor), or an operator token, which is
# how an operator signs out. The answer names where the browser goes to end
# the identity provider's session behind it, when there is one. The body is
# optional: a caller with no browser sends none.
@router.post("/auth/logout", response_model=SignedOutView)
async def logout(
    identity: Identity, tenancy: TenancyService, body: LogoutRequest | None = None
) -> SignedOutView:
    return await tenancy.logout(identity, body)


@router.get("/me", response_model=MeView)
async def me(ctx: Ctx, tenancy: TenancyService) -> MeView:
    return await tenancy.get_me(ctx)


@router.patch("/me", response_model=UserView)
async def update_me(ctx: Ctx, tenancy: TenancyService, body: UpdateMeRequest) -> UserView:
    return await tenancy.update_me(ctx, body)


# The caller's whole account, gone: every org they are in loses them, their
# personal org goes whole, and the session presented ends with every other.
# No idempotency key: the principal that would hold the marker is what this
# erases, so a retry meets a session that no longer exists.
@router.post(
    "/me/deletion",
    response_model=AccountDeletedView,
    responses={
        403: {"model": ErrorResponse, "description": "operator_role_held"},
        409: {"model": ErrorResponse, "description": "last_owner: `error.last_owner.orgs`"},
    },
)
async def delete_account(
    ctx: Ctx, tenancy: TenancyService, body: DeleteAccountRequest
) -> AccountDeletedView:
    return await tenancy.delete_account(ctx, body)


@router.get("/me/identity", response_model=IdentityView)
async def my_identity(ctx: Ctx, tenancy: TenancyService) -> IdentityView:
    return await tenancy.get_identity(ctx)


@router.patch("/me/identity", response_model=IdentityView)
async def update_my_identity(
    ctx: Ctx, tenancy: TenancyService, body: UpdateIdentityRequest
) -> IdentityView:
    return await tenancy.update_identity(ctx, body)


@router.get("/orgs/current", response_model=OrgView)
async def current_org(ctx: Ctx, tenancy: TenancyService) -> OrgView:
    return await tenancy.get_org(ctx)


# The caller's team org, gone for everyone in it, and the owner's session
# with it. No idempotency key, as on an account's deletion: the principal
# that would hold the marker is what this ends, so a retry meets a session
# that no longer exists.
@router.post(
    "/orgs/current/deletion",
    response_model=OrgDeletedView,
    responses={
        403: {"model": ErrorResponse, "description": "not_authorized: an owner's session only"},
        409: {"model": ErrorResponse, "description": "personal_org_fixed"},
    },
)
async def delete_org(ctx: Ctx, tenancy: TenancyService, body: DeleteOrgRequest) -> OrgDeletedView:
    return await tenancy.delete_org(ctx, body)


# A team org the caller makes and owns, answered with the caller's place in
# it; the switch there is the exchange. The idempotency marker is the
# caller's in the tenant they call from, and the org is created on its id.
@router.post("/orgs", response_model=MembershipChoiceView, status_code=201)
async def create_org(
    ctx: Ctx, tenancy: TenancyService, body: CreateTeamOrgRequest, idem: Idem
) -> Response:
    return await idem.run(201, lambda attempt: tenancy.create_org(ctx, body, attempt))


@router.get("/users", response_model=UserPageView)
async def list_users(
    ctx: Ctx, tenancy: TenancyService, cursor: str | None = None, limit: int = LIMIT_DEFAULT
) -> UserPageView:
    return await tenancy.get_users(ctx, cursor, limit)


@router.get("/memberships", response_model=MembershipPageView)
async def list_memberships(
    ctx: Ctx, tenancy: TenancyService, cursor: str | None = None, limit: int = LIMIT_DEFAULT
) -> MembershipPageView:
    return await tenancy.get_memberships(ctx, cursor, limit)


@router.patch("/memberships/{user_id}", response_model=MembershipView)
async def update_membership(
    ctx: Ctx, tenancy: TenancyService, user_id: UUID, body: UpdateMembershipRequest
) -> MembershipView:
    return await tenancy.update_membership_role(ctx, user_id, body)


@router.delete("/memberships/{user_id}", response_model=UserView)
async def remove_member(ctx: Ctx, tenancy: TenancyService, user_id: UUID) -> UserView:
    return await tenancy.remove_member(ctx, user_id)


@router.get("/invitations", response_model=InvitationPageView)
async def list_invitations(
    ctx: Ctx, tenancy: TenancyService, cursor: str | None = None, limit: int = LIMIT_DEFAULT
) -> InvitationPageView:
    return await tenancy.get_invitations(ctx, cursor, limit)


# The identity provider sends the email. The idempotency marker is the
# caller's, and the invitation is created on its id, so a retry sends one.
@router.post("/invitations", response_model=InvitationView, status_code=201)
async def invite_member(
    ctx: Ctx, tenancy: TenancyService, body: InviteMemberRequest, idem: Idem
) -> Response:
    return await idem.run(201, lambda attempt: tenancy.invite_member(ctx, body, attempt))


@router.post("/invitations/{invitation_id}/resend", response_model=InvitationView)
async def resend_invitation(
    ctx: Ctx, tenancy: TenancyService, invitation_id: UUID
) -> InvitationView:
    return await tenancy.resend_invitation(ctx, invitation_id)


@router.delete("/invitations/{invitation_id}", response_model=InvitationView)
async def revoke_invitation(
    ctx: Ctx, tenancy: TenancyService, invitation_id: UUID
) -> InvitationView:
    return await tenancy.revoke_invitation(ctx, invitation_id)


@router.post("/orgs/current/sso-link", response_model=SsoLinkView)
async def sso_link(ctx: Ctx, tenancy: TenancyService, body: SsoLinkRequest) -> SsoLinkView:
    return await tenancy.sso_link(ctx, body)


@router.get("/sessions", response_model=list[SessionView])
async def list_sessions(
    ctx: Ctx, tenancy: TenancyService, limit: int = LIMIT_DEFAULT
) -> list[SessionView]:
    return await tenancy.get_sessions(ctx, limit)


@router.delete("/sessions/{session_id}", response_model=SessionView)
async def revoke_session(ctx: Ctx, tenancy: TenancyService, session_id: UUID) -> SessionView:
    return await tenancy.revoke_session(ctx, session_id)


@router.get("/api-keys", response_model=ApiKeyPageView)
async def list_api_keys(
    ctx: Ctx, tenancy: TenancyService, cursor: str | None = None, limit: int = LIMIT_DEFAULT
) -> ApiKeyPageView:
    return await tenancy.get_api_keys(ctx, cursor, limit)


@router.post("/api-keys", response_model=IssuedApiKeyView, status_code=201)
async def create_api_key(
    ctx: Ctx, tenancy: TenancyService, body: AddApiKeyRequest, idem: Idem
) -> Response:
    return await idem.run(201, lambda attempt: tenancy.create_api_key(ctx, body, attempt))


@router.delete("/api-keys/{api_key_id}", response_model=ApiKeyView)
async def revoke_api_key(ctx: Ctx, tenancy: TenancyService, api_key_id: UUID) -> ApiKeyView:
    return await tenancy.revoke_api_key(ctx, api_key_id)
