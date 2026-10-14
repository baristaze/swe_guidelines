from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from acme.om.context import CredentialKind, OperatorRole, Permission, Role
from acme.om.tenancy.rules import MAX_API_KEY_TTL, MAX_OPERATOR_TOKEN_TTL
from acme.om.tenancy.types.invitation import InvitationState
from acme.om.tenancy.types.org import OrgKind
from acme.services.api.types.common import RequestBody, View


class OrgView(View):
    """`kind` says what the org is for: every person has one `personal` org,
    made with them, which is never deleted and never changes hands; every
    other org is a `team` org."""

    id: UUID
    name: str
    slug: str
    kind: OrgKind
    created_at: datetime
    deleted_at: datetime | None = None


class UserView(View):
    id: UUID
    email: str
    display_name: str
    created_at: datetime


class IdentityView(View):
    """The person behind the caller's user. `operator_role` is the allowlist
    entry: null for a person who is not an operator."""

    id: UUID
    email: str
    operator_role: OperatorRole | None
    created_at: datetime
    # Later fields default, so a client reads a response from a build that
    # predates them.
    time_zone: str | None = None


class UpdateIdentityRequest(RequestBody):
    """Where the person is, as an IANA name ("Europe/Istanbul"): the portal
    sends the browser's own on sign-in, and a time shown or sent to the
    person is in it; with none sent, in UTC. A name that is not one is
    422."""

    time_zone: str = Field(min_length=1, max_length=64)


class UpdateMeRequest(RequestBody):
    display_name: str = Field(min_length=1, max_length=200)


class MembershipView(View):
    id: UUID
    user_id: UUID
    role: Role
    teams: tuple[UUID, ...]


class UpdateMembershipRequest(RequestBody):
    role: Role


class MembershipChoiceView(View):
    org: OrgView
    user: UserView
    role: Role


class SignInStartRequest(RequestBody):
    """The start of a sign-in at the identity provider. `redirect_uri` is where
    the browser comes back with a code: this environment's portal callback,
    and nothing else. `state` is the caller's own random value, kept by the
    tab that started the sign-in and compared when the browser comes back,
    which binds the round trip to that tab; it may carry nothing else.
    `invitation_token` is the one an invitation's link carried, and
    `sign_up` opens the provider on its sign-up screen."""

    redirect_uri: str = Field(min_length=1, max_length=2000)
    state: str = Field(min_length=16, max_length=200)
    invitation_token: str | None = Field(default=None, max_length=500)
    sign_up: bool = False


class SignInStartView(View):
    """Where the browser goes next, the identity provider's sign-in, and the
    PKCE verifier the tab keeps beside its state and sends back with the
    code. The provider holds only the verifier's digest, so a code is worth
    nothing to anyone who does not hold it."""

    secret_fields = frozenset({"code_verifier"})

    authorization_url: str
    code_verifier: str


class SignInCallbackRequest(RequestBody):
    """The code the browser brought back to the callback, and the verifier
    the sign-in's start answered with, which the API exchanges with the
    identity provider, server-side. The answer is a
    sign-in's (`IssuedLoginView`); a person nobody knew is signed up by it,
    with their personal org."""

    code: str = Field(min_length=1, max_length=500)
    code_verifier: str = Field(min_length=43, max_length=128)
    invitation_token: str | None = Field(default=None, max_length=500)


class DeviceSignInView(View):
    """The start of a sign-in for a device with no browser, the command
    line. The person opens `verification_uri_complete` (or
    `verification_uri` and types `user_code`) in any browser; the device
    keeps `device_code` to itself and asks `POST /v1/auth/device/token`
    with it every `interval` seconds, for at most `expires_in` seconds."""

    secret_fields = frozenset({"device_code"})

    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int


class DeviceTokenRequest(RequestBody):
    """The device code a device sign-in started with. The answer is a
    sign-in's once the person confirmed it; before that, 400
    `sign_in_pending` (or `sign_in_slow_down`: ask less often), and 401
    `sign_in_refused` when they declined or it expired."""

    device_code: str = Field(min_length=1, max_length=500)


class DevSignInRequest(RequestBody):
    """Local and test only: a sign-in by address alone, with no browser round
    trip, for the seed, the traffic generator, and the tests. A
    person nobody knew is made, with their personal org. A deployed
    environment never serves it: the route answers 404 there, and the
    process refuses to start with it on."""

    email: str = Field(min_length=3, max_length=320)
    display_name: str = Field(default="", max_length=200)


class SecondFactorRequest(RequestBody):
    """The code from an authenticator, presented with a sign-in credential.
    The answer is a new sign-in that records the verified code, which the
    operator plane asks of an enrolled operator. A code used once is
    refused."""

    totp_code: str = Field(min_length=6, max_length=6)


class CreateTeamOrgRequest(RequestBody):
    """A team org the caller makes and owns. `slug` is lower-case letters and
    digits joined by hyphens; left out, one is made from the name. A taken
    slug is 409."""

    name: str = Field(min_length=1, max_length=200)
    slug: str | None = Field(default=None, min_length=1, max_length=48)


class IssuedLoginView(View):
    """Carries the freshly minted login credential in the clear, once."""

    token: str
    expires_at: datetime
    memberships: list[MembershipChoiceView]


class MembershipChoicePageView(View):
    """One page of the signed-in person's places, each the org, the user, and
    the role: the same choice a sign-in answers with. `next_cursor` as on
    `UserPageView`."""

    items: list[MembershipChoiceView]
    next_cursor: str | None


class ExchangeAuthSessionRequest(RequestBody):
    """The org to enter. Presented with the sign-in credential, it opens the
    first session; presented with a session, it is a switch, and that session
    ends in the same write."""

    org_id: UUID


class IssuedAuthSessionView(View):
    token: str
    expires_at: datetime
    org: OrgView
    user: UserView
    role: Role


class MeView(View):
    user: UserView
    org: OrgView
    role: Role
    permissions: tuple[Permission, ...]
    app: str


class AuthSessionView(View):
    """Only the hash of a token is ever kept, so a session view carries no secret."""

    id: UUID
    credential_kind: CredentialKind
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None


class LogoutRequest(RequestBody):
    """Where the identity provider sends the browser once it has ended its own
    session: this environment's portal page for it, and nothing else. Left
    out, the provider sends it to its default sign-out address."""

    return_to: str | None = Field(default=None, min_length=1, max_length=2000)


class SignedOutView(AuthSessionView):
    """The credential that ended, a session, a sign-in, or an operator
    token, and `provider_logout_url`: where the browser goes next to end the
    identity provider's session behind it, so the next sign-in on this
    browser asks who it is. Null when the sign-in left no session there
    (the device sign-in, the local sign-in, an operator token). It names the
    provider's session, which is not a secret."""

    provider_logout_url: str | None = None


class DeleteAccountRequest(RequestBody):
    """The account's email as the person typed it, which is how they say
    they mean it; and, as on the sign-out, where the identity provider sends
    the browser once it has ended its own session."""

    email: str = Field(min_length=1, max_length=320)
    return_to: str | None = Field(default=None, min_length=1, max_length=2000)


class AccountDeletedView(View):
    """The account is gone: `deleted_at` is when. The database's backups
    still hold it until they expire, seven days on. `provider_logout_url` is
    where the browser goes next, as on a sign-out; null when the sign-in
    left no session there."""

    deleted_at: datetime
    provider_logout_url: str | None = None


class DeleteOrgRequest(RequestBody):
    """The org's name as its owner typed it, which is how they say they mean
    it."""

    name: str = Field(min_length=1, max_length=200)


class OrgDeletedView(View):
    """The org is gone for everyone in it: `deleted_at` is when. `auth_session` is
    the owner's new session in their personal org, which replaces the one
    that asked, as a switch's does; null when none could be made, and the
    owner signs in again."""

    deleted_at: datetime
    auth_session: IssuedAuthSessionView | None = None


class ApiKeyView(View):
    id: UUID
    name: str
    role: Role
    user_id: UUID
    created_at: datetime
    expires_at: datetime
    deleted_at: datetime | None


class UserPageView(View):
    """One page of the tenant's members. `next_cursor` fetches the next page
    and is null on the last one, so a client reads every member instead of
    whatever a fixed limit happened to cover."""

    items: list[UserView]
    next_cursor: str | None


class MembershipPageView(View):
    """One page of the tenant's memberships, by user id; `next_cursor` as on
    `UserPageView`. A page read with the same limit as a page of users
    covers the same members, so roles pair with members page for page."""

    items: list[MembershipView]
    next_cursor: str | None


class OrgPageView(View):
    """One page of every org, by id, for the operator plane; `next_cursor` as
    on `UserPageView`."""

    items: list[OrgView]
    next_cursor: str | None


class ApiKeyPageView(View):
    """One page of the api key list, newest first; `next_cursor` as on
    `UserPageView`."""

    items: list[ApiKeyView]
    next_cursor: str | None


class AddApiKeyRequest(RequestBody):
    name: str
    role: Role
    ttl_days: int | None = Field(default=None, ge=1, le=MAX_API_KEY_TTL.days)


class IssuedApiKeyView(View):
    """The key in the clear is present on the first response only: the stored
    outcome of the create carries no secret, so a replay under the same
    Idempotency-Key answers with `key` null and `Idempotent-Replayed: true`. A
    client that lost the first response revokes the key and issues another."""

    secret_fields = frozenset({"key"})

    key: str | None
    api_key: ApiKeyView


class InvitationView(View):
    """A person asked to join the org. The identity provider sent the email
    with the link; `expires_at` is when the link stops working, after which
    the invitation is sent again or replaced."""

    id: UUID
    email: str
    role: Role
    state: InvitationState
    expires_at: datetime
    created_at: datetime
    created_by: UUID


class InviteMemberRequest(RequestBody):
    """The address to invite and the role the person gets, at most the
    caller's own."""

    email: str = Field(min_length=3, max_length=320)
    role: Role


class InvitationPageView(View):
    """One page of the org's pending invitations, newest first; `next_cursor`
    as on `UserPageView`."""

    items: list[InvitationView]
    next_cursor: str | None


class SsoLinkRequest(RequestBody):
    """What the identity provider's admin portal opens on: the single sign-on
    connection (`sso`) or the org's domains (`domain_verification`), and the
    page of this environment's portal it links back to."""

    intent: Literal["sso", "domain_verification"]
    return_url: str = Field(min_length=1, max_length=2000)


class SsoLinkView(View):
    """A short-lived link to the admin portal; open it at once."""

    url: str


class FlagsView(View):
    """The session's flags that a client may read, evaluated for its org and
    its user. A flag read on the server alone is never in it."""

    flags: dict[str, bool] = Field(
        description="Each flag marked for clients, by name, and its value for the "
        "session's org and user."
    )


# The operator plane's. The views a tenant's rows are read into are the
# tenant's own (`OrgView`, `UserPageView`): an operator sees what the tenant
# sees, under the tenant named in the path.


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


class SystemSizeView(View):
    """How big the system is, what the first responder to an alarm reads
    before it escalates: live tenants and users, and the events produced in
    the twenty-four hours from `since` to `counted_at`. The maintenance
    worker counts it every few minutes, and this is its latest count:
    `counted_at` says how old the answer is."""

    tenants: int
    users: int
    change_events_last_24h: int
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
