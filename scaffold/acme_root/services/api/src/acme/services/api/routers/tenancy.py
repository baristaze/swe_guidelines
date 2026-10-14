"""Tenancy: who a caller is, and the org it acts in.

- **Sign-in.** Start a sign-in at the identity provider, WorkOS AuthKit,
  for this environment's portal callback, and finish it with the code the
  browser brought back, which the API exchanges server-side; a person
  nobody knew is signed up by it, with their personal org
  (`/v1/auth/sign-in`, `/v1/auth/callback`). Start and finish a device
  sign-in for the command line (`/v1/auth/device`,
  `/v1/auth/device/token`). Confirm an operator's second factor on a
  sign-in (`/v1/auth/second-factor`). List my places; exchange the login,
  once, or switch an auth session, for an auth session in one org; sign
  out (`/v1/auth/memberships`, `/v1/auth/sessions`, `/v1/auth/logout`).
  The sign-out ends the credential presented, whichever a person holds:
  an auth session, a login (with or without its second factor), or an
  operator token; an API key has none and is `401`. It takes an optional
  `return_to`, one of this environment's `ACME_SIGN_OUT_RETURN_URIS`, and
  answers the row it ended with `provider_logout_url`: WorkOS's logout
  for the AuthKit session the sign-in left in the browser, or null when
  there is none (the device sign-in, the local sign-in, an operator
  token). Locally and in the tests only, sign in by address alone
  (`/v1/auth/dev-sign-in`, ADR 0029); a deployed process refuses to start
  with it on.
- **Me.** The current org, my user, my identity, and my display name; a
  new team org I own (`/v1/orgs/current`, `/v1/me`, `/v1/me/identity`,
  `/v1/orgs`).
- **Delete my account.** From an auth session, with the account's email
  typed to confirm: the account goes at once, in every org, and the
  answer names the identity provider's logout, as a sign-out does. `409
  last_owner` names each team org the person is the last owner of, in the
  envelope's `last_owner.orgs`; `403 operator_role_held` refuses an
  operator (`POST /v1/me/deletion`, ADR 0041).
- **Delete this organization.** From an owner's auth session, with the
  org's name typed to confirm: everyone in the team org loses it at once,
  and the answer carries the owner's new auth session in their personal
  org. `403 not_authorized` refuses an admin, a member, and an API key;
  `409 personal_org_fixed` refuses a personal org
  (`POST /v1/orgs/current/deletion`, ADR 0042).
- **Members.** The org's members and their roles a page at a time, a
  member's role, removing a member (`/v1/users`, `/v1/memberships`,
  `/v1/memberships/{user_id}`).
- **Invitations and single sign-on.** Invite a person by email with a
  role, under an Idempotency-Key; the identity provider sends the email.
  The org's pending invitations a page at a time, sending one again,
  revoking one. A short-lived link to the provider's admin portal, where
  an owner or an admin of a team org sets up single sign-on or proves a
  domain (`/v1/invitations`, `/v1/invitations/{invitation_id}/resend`,
  `/v1/invitations/{invitation_id}`, `/v1/orgs/current/sso-link`).
- **Credentials.** My live auth sessions and revoking one; the org's API
  keys, creating one, revoking one (`/v1/auth/sessions`, `/v1/api-keys`).
- **Flags.** The session's flags that a client reads to decide what it
  shows, under an entity tag, so a client that asks again with it is
  answered `304` until a value changes (`/v1/flags`).
- **The identity provider's deliveries.** Outside `/v1`, since their
  shape is the provider's (`routers/webhooks.py`, `/webhooks/identity`).
- **The operator plane.** For an identity on the operator allowlist,
  across every org: create an org with its owner, add a member, read an
  org and its members, list every org a page at a time, delete a team org
  as its owner does (closed for everyone in it at once; the worker ends
  its providers and then deletes it; a personal org is refused), and read
  the system's size: the tenant count, the user count, and the change
  events of twenty-four hours, as the maintenance worker last counted
  them, with the moment it did (`404` before its first count; the route
  counts nothing) (`/v1/admin/orgs`, `/v1/admin/orgs/{org_id}`,
  `/v1/admin/orgs/{org_id}/members`, `/v1/admin/size`). An org's change
  events and its work are their own namespaces' operator routes, under the
  same plane. The plane's routes take OperatorContext and cannot reach a
  tenant manager, because no TenantContext exists on this path. A read
  needs the read permission of the allowlist entry and a write the write
  one; the manager decides, and a refusal is the same `not_authorized` a
  viewer's write gets. The creating routes run under the operator's
  idempotency record, keyed like every other creating route. Every read
  and write takes an operator token (`OperatorCtx`), which refuses a
  sign-in `operator_token_required`. An operator enrols the second factor
  once, at the first sign-in to the plane, and until then only the two
  enrolment routes answer (`EnrollingOperatorCtx`, `/v1/admin/me/totp`,
  `/v1/admin/me/totp/confirm`). A sign-in that verified a TOTP code mints
  one operator token, one permission and an hour at most, and ends in that
  mint (`MintingOperatorCtx`, `POST /v1/admin/me/tokens`). An operator
  lists their own live tokens and revokes one by its id, refused from its
  next request; another operator's is `404` (`GET /v1/admin/me/tokens`,
  `DELETE /v1/admin/me/tokens/{token_id}`, ADR 0068).

Every function is one call into the tenancy service, or, on the operator
plane, the operator service."""

from uuid import UUID

from fastapi import APIRouter, Depends, Response

from acme.services.api.gateway.admin import EnrollingOperatorCtx, MintingOperatorCtx, OperatorCtx
from acme.services.api.gateway.auth import Ctx, Identity, Rctx, dev_sign_in_open
from acme.services.api.gateway.idempotency import Idem, OperatorIdem
from acme.services.api.gateway.ratelimit import rate_limited
from acme.services.api.gateway.resolve import AdminService, FlagsService, TenancyService
from acme.services.api.gateway.snapshot import NOT_MODIFIED, Snap
from acme.services.api.types.common import LIMIT_DEFAULT, ErrorResponse
from acme.services.api.types.tenancy import (
    AccountDeletedView,
    AddApiKeyRequest,
    AddMemberRequest,
    ApiKeyPageView,
    ApiKeyView,
    AuthSessionView,
    ConfirmTotpRequest,
    CreateOrgRequest,
    CreateTeamOrgRequest,
    DeleteAccountRequest,
    DeleteOrgRequest,
    DeviceSignInView,
    DeviceTokenRequest,
    DevSignInRequest,
    ExchangeAuthSessionRequest,
    FlagsView,
    IdentityView,
    InvitationPageView,
    InvitationView,
    InviteMemberRequest,
    IssuedApiKeyView,
    IssuedAuthSessionView,
    IssuedLoginView,
    IssuedOperatorTokenView,
    IssuedTotpSecretView,
    LogoutRequest,
    MembershipChoicePageView,
    MembershipChoiceView,
    MembershipPageView,
    MembershipView,
    MeView,
    MintOperatorTokenRequest,
    OperatorTokenPageView,
    OperatorTokenView,
    OperatorView,
    OrgDeletedView,
    OrgPageView,
    OrgView,
    SecondFactorRequest,
    SignedOutView,
    SignInCallbackRequest,
    SignInStartRequest,
    SignInStartView,
    SsoLinkRequest,
    SsoLinkView,
    SystemSizeView,
    TotpConfirmedView,
    UpdateIdentityRequest,
    UpdateMembershipRequest,
    UpdateMeRequest,
    UserPageView,
    UserView,
)

router = APIRouter(tags=["tenancy"])
operator = APIRouter(prefix="/admin", tags=["tenancy"])


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
@router.post("/auth/sessions", response_model=IssuedAuthSessionView)
async def exchange_auth_session(
    identity: Identity, tenancy: TenancyService, body: ExchangeAuthSessionRequest
) -> IssuedAuthSessionView:
    return await tenancy.exchange_auth_session(identity, body)


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


@router.get("/users/{user_id}", response_model=UserView)
async def get_user(ctx: Ctx, tenancy: TenancyService, user_id: UUID) -> UserView:
    return await tenancy.get_user(ctx, user_id)


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


@router.get("/auth/sessions", response_model=list[AuthSessionView])
async def list_auth_sessions(
    ctx: Ctx, tenancy: TenancyService, limit: int = LIMIT_DEFAULT
) -> list[AuthSessionView]:
    return await tenancy.get_auth_sessions(ctx, limit)


@router.delete("/auth/sessions/{auth_session_id}", response_model=AuthSessionView)
async def revoke_auth_session(
    ctx: Ctx, tenancy: TenancyService, auth_session_id: UUID
) -> AuthSessionView:
    return await tenancy.revoke_auth_session(ctx, auth_session_id)


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


@router.get("/flags", response_model=FlagsView, responses=NOT_MODIFIED)
async def get_flags(ctx: Ctx, flags: FlagsService, snapshot: Snap) -> Response:
    """The flags marked for clients, for the session's org and user. A
    client never decides what the server allows: an operation a flag gates
    is refused on the server whatever a client shows."""
    return await snapshot.answer(flags.get_flags(ctx))


@operator.get("/me", response_model=OperatorView)
async def operator_me(admin: OperatorCtx, service: AdminService) -> OperatorView:
    """Who the plane admitted and what the entry grants; the check a skill
    makes before its first read."""
    return await service.me(admin)


@operator.post("/me/totp", response_model=IssuedTotpSecretView)
async def enrol_totp(admin: EnrollingOperatorCtx, service: AdminService) -> IssuedTotpSecretView:
    """Mints the operator's TOTP secret and answers it once, as the
    `otpauth://` URI an authenticator app reads. It creates no row, and a
    retry is safe: minting again replaces a secret that was never confirmed,
    so it takes no Idempotency-Key. Refused once one is confirmed."""
    return await service.enrol_totp(admin)


@operator.post("/me/totp/confirm", response_model=TotpConfirmedView)
async def confirm_totp(
    admin: EnrollingOperatorCtx, service: AdminService, body: ConfirmTotpRequest
) -> TotpConfirmedView:
    """The first code confirms the secret. From then on the plane admits
    this identity only on a sign-in that verified a code."""
    return await service.confirm_totp(admin, body)


# The mint exchanges the sign-in: it ends in the write that lands the token,
# so the sign-in is the mint's key, and the route carries no Idempotency-Key
# and answers 200, as the exchange of a sign-in for a session does (ADR 0037,
# ADR 0068). A retry after a lost answer is refused and the person signs in
# again.
@operator.post("/me/tokens", response_model=IssuedOperatorTokenView)
async def mint_token(
    admin: MintingOperatorCtx, service: AdminService, body: MintOperatorTokenRequest
) -> IssuedOperatorTokenView:
    """An operator token: one permission, never wider than the caller's
    entry, an hour at most. Minted only from a sign-in that verified a second
    factor, which it ends, so a token never mints a token and a sign-in
    mints one."""
    return await service.mint_token(admin, body)


@operator.get("/me/tokens", response_model=OperatorTokenPageView)
async def list_tokens(
    admin: OperatorCtx,
    service: AdminService,
    cursor: str | None = None,
    limit: int = LIMIT_DEFAULT,
) -> OperatorTokenPageView:
    """The caller's own live operator tokens, newest first: the ids a revoke
    names, whoever minted them (the caller, or the grant job for a machine
    identity). Never a token's secret."""
    return await service.list_tokens(admin, cursor, limit)


@operator.delete("/me/tokens/{token_id}", response_model=OperatorTokenView)
async def revoke_token(
    admin: OperatorCtx, service: AdminService, token_id: UUID
) -> OperatorTokenView:
    """Ends one of the caller's own tokens at once; its next request is
    refused. Ending one ended already answers it as stored. Another
    operator's token is `404`: ending it is the grant job's disable."""
    return await service.revoke_token(admin, token_id)


@operator.get("/size", response_model=SystemSizeView)
async def system_size(admin: OperatorCtx, service: AdminService) -> SystemSizeView:
    """The system's size as the maintenance worker last counted it, with
    the moment it did; the route counts nothing. `404` until the first
    count."""
    return await service.size(admin)


@operator.get("/orgs", response_model=OrgPageView)
async def operator_list_orgs(
    admin: OperatorCtx,
    service: AdminService,
    cursor: str | None = None,
    limit: int = LIMIT_DEFAULT,
) -> OrgPageView:
    return await service.get_orgs(admin, cursor, limit)


@operator.post("/orgs", response_model=OrgView, status_code=201)
async def operator_create_org(
    admin: OperatorCtx, service: AdminService, body: CreateOrgRequest, idem: OperatorIdem
) -> Response:
    return await idem.run(201, lambda attempt: service.create_org(admin, body, attempt))


@operator.get("/orgs/{org_id}", response_model=OrgView)
async def operator_get_org(admin: OperatorCtx, service: AdminService, org_id: UUID) -> OrgView:
    return await service.get_org(admin, org_id)


@operator.delete("/orgs/{org_id}", response_model=OrgView)
async def operator_delete_org(admin: OperatorCtx, service: AdminService, org_id: UUID) -> OrgView:
    return await service.delete_org(admin, org_id)


@operator.get("/orgs/{org_id}/members", response_model=UserPageView)
async def operator_list_members(
    admin: OperatorCtx,
    service: AdminService,
    org_id: UUID,
    cursor: str | None = None,
    limit: int = LIMIT_DEFAULT,
) -> UserPageView:
    return await service.get_members(admin, org_id, cursor, limit)


@operator.post("/orgs/{org_id}/members", response_model=UserView, status_code=201)
async def operator_add_member(
    admin: OperatorCtx,
    service: AdminService,
    org_id: UUID,
    body: AddMemberRequest,
    idem: OperatorIdem,
) -> Response:
    return await idem.run(201, lambda attempt: service.add_member(admin, org_id, body, attempt))
