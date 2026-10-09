"""Operator routes under /v1/admin/*. They take OperatorContext and cannot
reach a tenant manager because no TenantContext exists on this path. A read
requires the read permission of the allowlist entry and a write the write
one; the manager decides, and a refusal is the same `not_authorized` a
viewer's write gets. The creating routes run under the operator's
idempotency record, keyed like every other creating route.

The two enrolment routes take `EnrollingOperatorCtx`, the one gate an
operator with no second factor yet passes. The mint takes
`MintingOperatorCtx`, which refuses that operator
`second_factor_not_enrolled`; the manager then takes a sign-in with its code
and nothing else. Every other route takes `OperatorCtx`, which refuses a
sign-in `operator_token_required` too: the plane reads and writes with a
token, which its operator lists and revokes one at a time (ADR 0068)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from acme.services.api.gateway.admin import (
    EnrollingOperatorCtx,
    MintingOperatorCtx,
    OperatorCtx,
)
from acme.services.api.gateway.idempotency import OperatorIdem
from acme.services.api.gateway.resolve import AdminService
from acme.services.api.types.admin import (
    AddMemberRequest,
    ConfirmTotpRequest,
    CreateOrgRequest,
    IssuedOperatorTokenView,
    IssuedTotpSecretView,
    MintOperatorTokenRequest,
    OperatorTokenPageView,
    OperatorTokenView,
    OperatorView,
    OperatorWorkItemView,
    PlatformSizeView,
    SetTenantCapRequest,
    TenantCapView,
    TotpConfirmedView,
)
from acme.services.api.types.common import LIMIT_DEFAULT
from acme.services.api.types.events import OperatorEventView
from acme.services.api.types.tenancy import OrgPageView, OrgView, UserPageView, UserView

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/me", response_model=OperatorView)
async def operator_me(admin: OperatorCtx, service: AdminService) -> OperatorView:
    """Who the plane admitted and what the entry grants; the check a skill
    makes before its first read."""
    return await service.me(admin)


@router.post("/me/totp", response_model=IssuedTotpSecretView)
async def enrol_totp(admin: EnrollingOperatorCtx, service: AdminService) -> IssuedTotpSecretView:
    """Mints the operator's TOTP secret and answers it once, as the
    `otpauth://` URI an authenticator app reads. It creates no row, and a
    retry is safe: minting again replaces a secret that was never confirmed,
    so it takes no Idempotency-Key. Refused once one is confirmed."""
    return await service.enrol_totp(admin)


@router.post("/me/totp/confirm", response_model=TotpConfirmedView)
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
@router.post("/me/tokens", response_model=IssuedOperatorTokenView)
async def mint_token(
    admin: MintingOperatorCtx, service: AdminService, body: MintOperatorTokenRequest
) -> IssuedOperatorTokenView:
    """An operator token: one permission, never wider than the caller's
    entry, an hour at most. Minted only from a sign-in that verified a second
    factor, which it ends, so a token never mints a token and a sign-in
    mints one."""
    return await service.mint_token(admin, body)


@router.get("/me/tokens", response_model=OperatorTokenPageView)
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


@router.delete("/me/tokens/{token_id}", response_model=OperatorTokenView)
async def revoke_token(
    admin: OperatorCtx, service: AdminService, token_id: UUID
) -> OperatorTokenView:
    """Ends one of the caller's own tokens at once; its next request is
    refused. Ending one ended already answers it as stored. Another
    operator's token is `404`: ending it is the grant job's disable."""
    return await service.revoke_token(admin, token_id)


@router.get("/size", response_model=PlatformSizeView)
async def platform_size(admin: OperatorCtx, service: AdminService) -> PlatformSizeView:
    """The platform's size as the maintenance worker last counted it, with
    the moment it did; the route counts nothing. `404` until the first
    count."""
    return await service.size(admin)


@router.get("/orgs", response_model=OrgPageView)
async def list_orgs(
    admin: OperatorCtx,
    service: AdminService,
    cursor: str | None = None,
    limit: int = LIMIT_DEFAULT,
) -> OrgPageView:
    return await service.get_orgs(admin, cursor, limit)


@router.post("/orgs", response_model=OrgView, status_code=201)
async def create_org(
    admin: OperatorCtx, service: AdminService, body: CreateOrgRequest, idem: OperatorIdem
) -> Response:
    return await idem.run(201, lambda attempt: service.create_org(admin, body, attempt))


@router.get("/orgs/{org_id}", response_model=OrgView)
async def get_org(admin: OperatorCtx, service: AdminService, org_id: UUID) -> OrgView:
    return await service.get_org(admin, org_id)


@router.delete("/orgs/{org_id}", response_model=OrgView)
async def delete_org(admin: OperatorCtx, service: AdminService, org_id: UUID) -> OrgView:
    return await service.delete_org(admin, org_id)


@router.get("/orgs/{org_id}/members", response_model=UserPageView)
async def list_members(
    admin: OperatorCtx,
    service: AdminService,
    org_id: UUID,
    cursor: str | None = None,
    limit: int = LIMIT_DEFAULT,
) -> UserPageView:
    return await service.get_members(admin, org_id, cursor, limit)


@router.post("/orgs/{org_id}/members", response_model=UserView, status_code=201)
async def add_member(
    admin: OperatorCtx,
    service: AdminService,
    org_id: UUID,
    body: AddMemberRequest,
    idem: OperatorIdem,
) -> Response:
    return await idem.run(201, lambda attempt: service.add_member(admin, org_id, body, attempt))


@router.get("/orgs/{org_id}/events", response_model=list[OperatorEventView])
async def list_events(
    admin: OperatorCtx,
    service: AdminService,
    org_id: UUID,
    after_seq: Annotated[int, Query(ge=0)] = 0,
    limit: int = LIMIT_DEFAULT,
) -> list[OperatorEventView]:
    return await service.get_events(admin, org_id, after_seq, limit)


@router.post("/orgs/{org_id}/work/{item_id}/requeue", response_model=OperatorWorkItemView)
async def requeue_work(
    admin: OperatorCtx, service: AdminService, org_id: UUID, item_id: UUID
) -> OperatorWorkItemView:
    """Sends one failed work item back to the queue, available now, with its
    attempts reset; the org's diary names the operator who did. Refused
    with `409 work_not_failed` for an item that is not failed, so a second
    call finds the first one's work done and says so."""
    return await service.requeue_work(admin, org_id, item_id)


# A cap is set whole by a PUT, so a retry after a lost answer sets the same
# cap again and the route takes no Idempotency-Key.
@router.put("/orgs/{org_id}/work/lanes/{lane}/cap", response_model=TenantCapView)
async def set_tenant_cap(
    admin: OperatorCtx,
    service: AdminService,
    org_id: UUID,
    lane: str,
    body: SetTenantCapRequest,
) -> TenantCapView:
    """Sets the org's own cap on the lane: the most items it holds claimed
    there at once, in place of the lane's cap, from the next claim on."""
    return await service.set_tenant_cap(admin, org_id, lane, body)


@router.get("/orgs/{org_id}/work/lanes/{lane}/cap", response_model=TenantCapView)
async def get_tenant_cap(
    admin: OperatorCtx, service: AdminService, org_id: UUID, lane: str
) -> TenantCapView:
    """The org's own cap on the lane; `404` when it has none, and the lane's
    cap holds for it."""
    return await service.get_tenant_cap(admin, org_id, lane)


@router.delete("/orgs/{org_id}/work/lanes/{lane}/cap", response_model=TenantCapView)
async def clear_tenant_cap(
    admin: OperatorCtx, service: AdminService, org_id: UUID, lane: str
) -> TenantCapView:
    """Removes the org's own cap on the lane and answers the cap it removed;
    the lane's cap holds for it from the next claim on. `404` when it has
    none."""
    return await service.clear_tenant_cap(admin, org_id, lane)
