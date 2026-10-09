"""Wire types of the operator plane. The views a tenant's rows are read into
are the tenant's own (`OrgView`, `UserPageView`, `EventView`): an operator
sees what the tenant sees, under the tenant named in the path."""

from datetime import datetime
from uuid import UUID

from pydantic import Field

from acme.om.context import OperatorRole, Role
from acme.om.tenancy.rules import MAX_OPERATOR_TOKEN_TTL
from acme.om.work.types.tenant_cap import MAX_CAP
from acme.om.work.types.work_item import WorkKind, WorkStatus
from acme.services.api.types.common import RequestBody, View


class CreateOrgRequest(RequestBody):
    """An org with its owner, as `bootstrap` seeds one. The owner's identity
    is created with its personal org when the email is new; the owner signs
    in through the identity provider with the address."""

    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=100)
    owner_email: str = Field(min_length=1)
    owner_name: str = Field(min_length=1, max_length=200)


class AddMemberRequest(RequestBody):
    """A person in the org, as `add-member` seeds one; the owner and the
    service role are refused, since the one owner is the one the create
    minted."""

    email: str = Field(min_length=1)
    display_name: str = Field(min_length=1, max_length=200)
    role: Role


class OperatorView(View):
    """Who the operator plane admitted: the identity and what its allowlist
    entry grants, so a skill checks it holds the entry it expects before it
    reads anything."""

    identity_id: UUID
    email: str
    operator_role: OperatorRole


class PlatformSizeView(View):
    """How big the platform is, what the first responder to an alarm reads
    before it escalates: live tenants and users, and the events produced in
    the twenty-four hours from `since` to `counted_at`. The maintenance
    worker counts it every few minutes, and this is its latest count:
    `counted_at` says how old the answer is."""

    tenants: int
    users: int
    events_last_24h: int
    since: datetime
    counted_at: datetime


class IssuedTotpSecretView(View):
    """A freshly minted TOTP secret, once, as the `otpauth://` URI an
    authenticator app reads. A replay carries none."""

    secret_fields = frozenset({"otpauth_uri"})

    otpauth_uri: str | None


class ConfirmTotpRequest(RequestBody):
    """The first code from the authenticator, which confirms the secret."""

    totp_code: str = Field(min_length=6, max_length=6)


class TotpConfirmedView(View):
    """The second factor is enrolled: from now on the operator plane admits
    this identity only on a sign-in that verified a code, so the next
    request signs in again with one."""

    identity_id: UUID
    confirmed_at: datetime


class MintOperatorTokenRequest(RequestBody):
    """One permission, never wider than the caller's entry (`write` implies
    `read`), and a lifetime of at most an hour, an hour when absent."""

    permission: OperatorRole
    expires_in: int | None = Field(
        default=None, ge=1, le=int(MAX_OPERATOR_TOKEN_TTL.total_seconds())
    )


class IssuedOperatorTokenView(View):
    """The token in the clear, on this answer only; `id` names it afterwards,
    in the list and the revoke, and is no secret. The mint ends the sign-in
    it was given, so a client that lost this answer signs in again for
    another token and revokes the lost one by its id from the list."""

    secret_fields = frozenset({"token"})

    id: UUID
    token: str | None
    expires_at: datetime
    permission: OperatorRole


class OperatorTokenView(View):
    """One operator token as its operator reads it: never the secret, which is
    kept as its digest. `revoked_at` is set once it was ended."""

    id: UUID
    permission: OperatorRole
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None


class OperatorTokenPageView(View):
    """One page of the caller's live operator tokens, newest first;
    `next_cursor` as on `UserPageView`."""

    items: list[OperatorTokenView]
    next_cursor: str | None


class OperatorWorkItemView(View):
    """One background job as an operator reads it: what it does and for
    which record, where it stands, and how many of its attempts are spent.
    The payload and the claim stay the worker's."""

    id: UUID
    kind: WorkKind
    target_id: UUID
    status: WorkStatus
    available_at: datetime
    attempts: int
    max_attempts: int
    last_error: str | None
    updated_at: datetime


class SetTenantCapRequest(RequestBody):
    """An org's own cap on a lane: the most items it holds claimed there at
    once, in place of the lane's cap."""

    cap: int = Field(ge=1, le=MAX_CAP)


class TenantCapView(View):
    """An org's own cap on a lane, with who last set it and when."""

    lane: str
    cap: int
    updated_at: datetime
    updated_by: UUID
