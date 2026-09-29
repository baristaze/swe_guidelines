import logging
import secrets
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field

from acme.infra.cache import CacheInterface
from acme.infra.exceptions import InfraException
from acme.integrations.exceptions import (
    DevicePending,
    DeviceSlowDown,
    ProviderConflict,
    ProviderRefused,
    ProviderUnavailable,
)
from acme.integrations.identity import (
    DeviceAuthorization,
    IdentityProviderInterface,
    PortalIntent,
    ProvidedOrganization,
    ProvidedSignIn,
)
from acme.om.base import EMPTY_UUID, Platform, new_id, utcnow
from acme.om.billing.manager import EntitlementsInterface
from acme.om.billing.rules import refuse_past, seats_metered
from acme.om.billing.types.account import BillingAccount
from acme.om.billing.types.billing import Entitlements
from acme.om.billing.types.plan import Lever
from acme.om.exceptions import (
    Conflict,
    CredentialExpired,
    EmailNotVerified,
    InvalidCredential,
    InvitationClosed,
    LastOwner,
    MembershipLimitReached,
    NotAnOperator,
    NotAuthorized,
    NotFound,
    OperatorRoleHeld,
    PersonalOrgFixed,
    PlanLimitReached,
    PlatformException,
    SecondFactorRequired,
    SignInDelayed,
    SignInPending,
    SignInRefused,
    SignInSlowDown,
    Unavailable,
    UniqueKeyTaken,
    ValidationFailed,
)
from acme.om.idempotency.types.attempt import Attempt
from acme.om.opcontext import (
    CredentialKind,
    IdentityContext,
    OpContext,
    OperatorContext,
    OperatorPermission,
    OperatorRole,
    Permission,
    RequestContext,
    Role,
    build_context,
)
from acme.om.outbox import OutboxRelayInterface
from acme.om.outbox.types.row import OutboxRow, outbox_row
from acme.om.tenancy.impl.creates import (
    MAX_ORGS_PER_IDENTITY,
    Admission,
    add_member_to,
    create_org_with_owner,
    create_person,
    new_identity,
    owner_rows,
    refuse_one_more,
    slug_suffix,
    user_payload,
    users_of,
)
from acme.om.tenancy.impl.totp import TotpSealer
from acme.om.tenancy.manager import TenancyManagerInterface
from acme.om.tenancy.rules import (
    MAX_API_KEY_TTL,
    MAX_OPERATOR_TOKEN_TTL,
    PREFIX_FOR_KIND,
    capped_role,
    check_email,
    check_org,
    check_time_zone,
    confirms_deletion,
    confirms_org_deletion,
    credential_kind_of,
    email_digest,
    fold_email,
    hash_token,
    is_platform_email,
    left_without_owner,
    matching_totp_step,
    operator_permissions_of,
    past_retention,
    permissions_of,
    pkce_challenge,
    role_at_most,
    sign_in_delay,
    slug_from_name,
    sso_joins,
)
from acme.om.tenancy.storage import TenancyStorageInterface
from acme.om.tenancy.types.api_key import ApiKey
from acme.om.tenancy.types.identity import Identity
from acme.om.tenancy.types.invitation import Invitation, InvitationState
from acme.om.tenancy.types.issued import (
    AccountDeleted,
    IssuedApiKey,
    IssuedLogin,
    IssuedOperatorToken,
    IssuedSession,
    IssuedTicket,
    OrgDeleted,
    OrgMembership,
    SignedOut,
    SignInStart,
)
from acme.om.tenancy.types.membership import Membership
from acme.om.tenancy.types.org import Org, OrgKind
from acme.om.tenancy.types.page import (
    ApiKeyPage,
    InvitationPage,
    MembershipPage,
    OrgMembershipPage,
    UserPage,
)
from acme.om.tenancy.types.session import Session
from acme.om.tenancy.types.socket_ticket import SocketPrincipal, SocketTicket
from acme.om.tenancy.types.user import User
from acme.om.work.types.work_item import WorkKind, work_row_kind

log = logging.getLogger(__name__)

TICKET_USED_KEY = "ticket-used:"
"""The cache remembers a redeemed ticket so a replay is refused without a
round trip; the row decides, so a miss (or a cache that is down) costs
one conditional write and nothing else."""
TICKET_CREDENTIALS = (CredentialKind.SESSION_TOKEN, CredentialKind.API_KEY)
"""The credentials a socket ticket may stand for."""
IDENTITY_CREDENTIALS = (
    CredentialKind.LOGIN,
    CredentialKind.SESSION_TOKEN,
    CredentialKind.OPERATOR_TOKEN,
)
"""The credentials that prove an identity: the person's own sign-in, a
session exchanged from it, and an operator token, which reaches the operator
plane and nothing else. An api key is an agent's and proves none."""
SIGN_IN_USED = "this sign-in was used already; sign in again"
"""The refusal of a sign-in presented after its exchange: a sign-in makes one
session, so a retry, a second choice, or a replay starts a new sign-in."""
REVOKED_MESSAGE: Mapping[CredentialKind, str] = {
    CredentialKind.LOGIN: "this sign-in has ended (used or signed out); sign in again",
    CredentialKind.OPERATOR_TOKEN: "operator token revoked",
}
"""The refusal of an ended credential, by kind; a session's is "session revoked"."""


DELETED_PERSONAL_ORG_NAME = "Deleted account"
"""What the record of a deleted account's personal org is called."""


class TenancyOptions(Platform):
    """Tunables, built once at boot; the manager never reads the environment."""

    login_ttl: timedelta = timedelta(minutes=10)
    session_ttl: timedelta = timedelta(days=30)
    """A session's absolute lifetime, from its exchange."""
    session_idle_ttl: timedelta = timedelta(days=14)
    """A session not presented for this long has ended, whatever is left of
    its absolute lifetime. It ends at whichever passes first."""
    session_seen_every: timedelta = timedelta(minutes=1)
    """How stale `last_seen_at` may be before a request writes it again, so a
    busy session costs one write a minute and not one per request."""
    sign_in_free_failures: int = 3
    """Failed sign-ins in a row an email may make before the next waits."""
    sign_in_delay_base: timedelta = timedelta(seconds=1)
    sign_in_delay_cap: timedelta = timedelta(minutes=5)
    """The wait starts at the base and doubles with each failure past the
    free ones, up to the cap, whatever address the attempts come from."""
    operator_token_ttl: timedelta = MAX_OPERATOR_TOKEN_TTL
    """The longest an operator token lives, and the lifetime a mint that names
    none gets. Never more than an hour."""
    totp_encryption_key: str | None = Field(default=None, repr=False)
    """The key the TOTP secrets are sealed under, a process credential. None
    refuses every enrolment and every sign-in that presents a code."""
    api_key_ttl: timedelta = MAX_API_KEY_TTL
    ticket_ttl: timedelta = timedelta(seconds=60)
    max_limit: int = 200
    max_orgs_per_identity: int = MAX_ORGS_PER_IDENTITY
    """How many orgs one person may join; the bound on every read of the users
    one identity is. An add past it is refused (`MembershipLimitReached`)."""
    retention: timedelta = timedelta(days=30)
    """Removed members, revoked keys, dead sessions, and closed invitations
    are purged this long after they ended, and a deleted tenant's rows this
    long after its delete."""
    ticket_retention: timedelta = timedelta(days=1)
    """A socket ticket lives a minute and is spent once; it is purged this
    long after it expired."""
    sign_in_delay_retention: timedelta = timedelta(days=30)
    """A run of failed sign-ins is forgotten this long after its last failure."""
    purge_batch: int = 1000
    """Rows one purge statement deletes at most; the sweep calls again for
    the rest."""
    sign_in_redirect_uris: tuple[str, ...] = ()
    """Where a sign-in at the identity provider may come back to: this
    environment's own portal callback, and nothing else. A redirect not
    named here is refused."""
    sign_out_return_uris: tuple[str, ...] = ()
    """Where the identity provider's logout may send a person back to: this
    environment's own portal page for it, each one of the application's
    sign-out URIs at the provider. A return not named here is refused."""
    dev_sign_in: bool = False
    """The local sign-in by address alone, for local and test processes. A
    deployed environment refuses it at boot, so it is never on there."""
    invitation_ttl_days: int = 7
    """How long an invitation's link works, from its send or resend."""


def origin_of(url: str) -> str:
    """The scheme and the host of an address, which is what makes it this
    environment's portal or not."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower()


def mint_token(kind: CredentialKind) -> str:
    return PREFIX_FOR_KIND[kind] + secrets.token_urlsafe(32)


def new_operator_token(
    identity_id: UUID,
    operator_role: OperatorRole,
    expires_in: timedelta | None,
    most: timedelta,
) -> tuple[IssuedOperatorToken, Session]:
    """An operator token for one identity and the row that keeps it: one
    permission, an hour at most, a session of kind `operator_token` under the
    system scope, kept as its digest. The two entry points, a signed-in
    operator's mint and the grant job's, authorize before they call this, and
    each lands the row its own way: the mint ends the sign-in in the same
    write, the grant job has none to end."""
    ttl = most if expires_in is None else expires_in
    if not timedelta(0) < ttl <= most:
        raise ValidationFailed(
            f"an operator token lives between one second and {int(most.total_seconds())} seconds"
        )
    now = utcnow()
    token = mint_token(CredentialKind.OPERATOR_TOKEN)
    session = Session(
        id=new_id(),
        created_at=now,
        updated_at=now,
        created_by=identity_id,
        updated_by=identity_id,
        identity_id=identity_id,
        token_hash=hash_token(token),
        credential_kind=CredentialKind.OPERATOR_TOKEN,
        expires_at=now + ttl,
        operator_role=operator_role,
    )
    issued = IssuedOperatorToken(
        id=session.id, token=token, expires_at=session.expires_at, operator_role=operator_role
    )
    return issued, session


def ended_by(credential: Session, by: UUID, at: datetime) -> Session:
    """A credential as it is once `by` ended it at `at`: the identity that
    ended a sign-in or an operator token, the user that ended a session."""
    return credential.model_copy(update={"revoked_at": at, "updated_at": at, "updated_by": by})


async def exchange_sign_in(
    storage: TenancyStorageInterface, org_id: UUID, session: Session, ended: Session
) -> None:
    """Lands `session` in `org_id` and the sign-in it came from, ended, in one
    write, so a sign-in is exchanged once: for a tenant session, for the
    sign-in its second factor verified, or for an operator token. A second
    exchange of it, a retry after a lost answer among them, is refused, and
    the person signs in again."""
    try:
        await storage.exchange_sign_in(org_id, session, ended)
    except NotFound:
        raise InvalidCredential("the sign-in behind the exchange is gone") from None
    except UniqueKeyTaken:
        raise  # a key of the new row, not the sign-in presented
    except Conflict:
        raise CredentialExpired(SIGN_IN_USED) from None


class TenancyManagerImpl(TenancyManagerInterface):
    def __init__(
        self,
        storage: TenancyStorageInterface,
        relay: OutboxRelayInterface,
        cache: CacheInterface,
        options: TenancyOptions,
        clock: Callable[[], datetime] = utcnow,
        *,
        identity_provider: IdentityProviderInterface,
        entitlements: EntitlementsInterface,
    ) -> None:
        """`entitlements` is what the plan levers ask: an api key and a
        per-seat plan's seat count read the org's plan from it."""
        self._storage = storage
        self._relay = relay
        self._cache = cache
        self._options = options
        self._provider = identity_provider
        self._entitlements = entitlements
        self._totp = TotpSealer(options.totp_encryption_key)
        # The TOTP time step is read from this clock, so a test can step it.
        self._clock = clock
        # The last sweep pass's answer to `tenant_expired`, read with the org
        # rows `service_contexts` pages through: its request id, the tenants
        # it minted a context for, and those of them past the retention.
        self._pass: tuple[UUID, frozenset[UUID], frozenset[UUID]] | None = None

    # The transitions: each takes a stage and produces a stronger one.

    async def bootstrap(
        self,
        rctx: RequestContext,
        org_name: str,
        slug: str,
        email: str,
        display_name: str,
        *,
        operator_role: OperatorRole | None = None,
    ) -> tuple[OpContext, Org]:
        org, user, membership = await create_org_with_owner(
            self._storage,
            org_id=new_id(),
            org_name=org_name,
            slug=slug,
            email=email,
            display_name=display_name,
            max_orgs=self._options.max_orgs_per_identity,
            operator_role=operator_role,
        )
        # The principal now exists; everything after this line runs under it.
        ctx = build_context(
            rctx,
            user_id=user.id,
            org_id=org.id,
            role=membership.role,
            permissions=permissions_of(membership.role),
            credential_kind=CredentialKind.INTERNAL,
            teams=membership.teams,
        )
        return ctx, org

    async def add_member(
        self,
        rctx: RequestContext,
        slug: str,
        email: str,
        display_name: str,
        role: Role,
    ) -> tuple[OpContext, User, bool]:
        org = await self._storage.read_org_by_slug(slug)
        if org is None or org.deleted_at is not None:
            raise NotFound(f"org {slug!r} not found")
        # The org's creator is the principal; everything after this line runs under it.
        org, creator, membership = await self._principal(org.id, org.created_by)
        ctx = build_context(
            rctx,
            user_id=creator.id,
            org_id=org.id,
            role=membership.role,
            permissions=permissions_of(membership.role),
            credential_kind=CredentialKind.INTERNAL,
            teams=membership.teams,
        )
        ctx.require(Permission.MANAGE_MEMBERS)
        if role is Role.SERVICE:
            raise ValidationFailed("service is not a membership role")
        if not role_at_most(role, ctx.security.role):
            raise NotAuthorized(f"cannot grant role {role.value} above {ctx.security.role.value}")
        user, created = await add_member_to(
            self._storage,
            self._relay,
            org_id=ctx.org_id,
            user_id=new_id(),
            email=email,
            display_name=display_name,
            role=role,
            actor_id=ctx.user_id,
            request=ctx,
            max_orgs=self._options.max_orgs_per_identity,
        )
        return ctx, user, created

    async def sign_in_url(
        self,
        rctx: RequestContext,
        redirect_uri: str,
        state: str,
        *,
        invitation_token: str | None = None,
        sign_up: bool = False,
    ) -> SignInStart:
        if redirect_uri not in self._options.sign_in_redirect_uris:
            raise ValidationFailed("that is not this environment's sign-in callback")
        if not state:
            raise ValidationFailed("a sign-in carries the state that binds it to its tab")
        verifier = secrets.token_urlsafe(48)
        try:
            url = self._provider.authorization_url(
                redirect_uri=redirect_uri,
                state=state,
                code_challenge=pkce_challenge(verifier),
                invitation_token=invitation_token,
                screen_hint="sign-up" if sign_up else None,
            )
        except ProviderUnavailable as error:
            raise Unavailable(f"sign-in is not available: {error.message}") from None
        return SignInStart(authorization_url=url, code_verifier=verifier)

    async def sign_in_with_code(
        self,
        rctx: RequestContext,
        code: str,
        invitation_token: str | None = None,
        *,
        code_verifier: str | None = None,
    ) -> IssuedLogin:
        try:
            signed_in = await self._provider.authenticate_code(
                code,
                code_verifier=code_verifier,
                invitation_token=invitation_token,
                deadline=rctx.deadline,
            )
        except ProviderUnavailable as error:
            raise Unavailable(f"sign-in is not available: {error.message}") from None
        except ProviderRefused as error:
            raise SignInRefused(f"the sign-in was refused: {error.message}") from None
        return await self._signed_in(rctx, signed_in)

    async def start_device_sign_in(self, rctx: RequestContext) -> DeviceAuthorization:
        try:
            return await self._provider.start_device(deadline=rctx.deadline)
        except ProviderUnavailable as error:
            raise Unavailable(f"sign-in is not available: {error.message}") from None
        except ProviderRefused as error:
            raise SignInRefused(f"the sign-in was refused: {error.message}") from None

    async def finish_device_sign_in(self, rctx: RequestContext, device_code: str) -> IssuedLogin:
        try:
            signed_in = await self._provider.authenticate_device(
                device_code, deadline=rctx.deadline
            )
        except DeviceSlowDown:
            raise SignInSlowDown("asked too often; wait longer before asking again") from None
        except DevicePending:
            raise SignInPending("the sign-in is not confirmed yet") from None
        except ProviderUnavailable as error:
            raise Unavailable(f"sign-in is not available: {error.message}") from None
        except ProviderRefused as error:
            raise SignInRefused(f"the sign-in was refused: {error.message}") from None
        return await self._signed_in(rctx, signed_in)

    async def dev_sign_in(
        self, rctx: RequestContext, email: str, display_name: str = ""
    ) -> IssuedLogin:
        if not self._options.dev_sign_in:
            raise NotFound("Not Found")
        if is_platform_email(email):
            raise ValidationFailed("that address belongs to the platform")
        try:
            check_email(email)
        except ValueError as error:
            raise ValidationFailed(str(error)) from None
        identity = await self._storage.read_identity_by_email_digest(email_digest(email))
        if identity is None:
            identity = await self._made(new_identity(email, utcnow()), display_name)
        memberships = await self._with_personal(identity, await self._memberships_of(identity.id))
        return await self._issue_login(identity, memberships)

    async def verify_second_factor(self, ictx: IdentityContext, totp_code: str) -> IssuedLogin:
        if ictx.credential_kind is not CredentialKind.LOGIN:
            raise InvalidCredential("a second factor is verified on a sign-in credential")
        identity = await self._storage.read_identity(ictx.identity_id)
        if identity is None:
            raise InvalidCredential("the identity is gone")
        digest = email_digest(identity.email)
        now = utcnow()
        # A run of wrong codes for the email makes the next one wait, counted
        # in the tenancy role's own storage, so it holds when the cache is
        # down and whatever address the guesses come from.
        run = await self._storage.read_sign_in_delay(digest)
        if run is not None:
            wait = sign_in_delay(
                run.failures,
                run.last_failed_at,
                now,
                free=self._options.sign_in_free_failures,
                base=self._options.sign_in_delay_base,
                cap=self._options.sign_in_delay_cap,
            )
            if wait > timedelta(0):
                raise SignInDelayed(wait)
        if not await self._second_factor_holds(identity, totp_code):
            await self._storage.record_failed_sign_in(digest, now)
            raise InvalidCredential("the code is wrong or was used already")
        if run is not None:
            await self._storage.clear_failed_sign_ins(digest)
        memberships = await self._memberships_of(identity.id)
        presented = await self._storage.read_session_by_id(ictx.credential_id)
        if presented is None:
            raise InvalidCredential("the sign-in behind the code is gone")
        _, sign_in = presented
        # The new sign-in stands for the same visit to the provider as the one
        # that presented the code, so it carries the same provider session,
        # and it ends that one in the write that lands it: a person holds one
        # sign-in at a time, as a tab holds one session (ADR 0068).
        token, verified = self._new_login(
            identity, second_factor_at=now, provider_session_id=sign_in.provider_session_id
        )
        await exchange_sign_in(
            self._storage, EMPTY_UUID, verified, ended_by(sign_in, identity.id, now)
        )
        return IssuedLogin(token=token, expires_at=verified.expires_at, memberships=memberships)

    async def _signed_in(self, rctx: RequestContext, signed_in: ProvidedSignIn) -> IssuedLogin:
        """The identity the provider vouched for, found, linked, or made, and
        the places it holds, joined first to the org the sign-in named when
        an invitation or a single sign-on puts the person there."""
        person = signed_in.user
        if not person.email_verified:
            raise EmailNotVerified("verify your email address with the sign-in provider first")
        if is_platform_email(person.email):
            raise InvalidCredential("that address belongs to the platform")
        identity = await self._identity_of(signed_in)
        memberships = await self._memberships_of(identity.id)
        if signed_in.organization_id is not None and await self._join_through_provider(
            rctx, identity, signed_in, memberships
        ):
            memberships = await self._memberships_of(identity.id)
        memberships = await self._with_personal(identity, memberships)
        return await self._issue_login(
            identity, memberships, provider_session_id=signed_in.session_id
        )

    async def _identity_of(self, signed_in: ProvidedSignIn) -> Identity:
        """By the issuer and the subject; else by the verified email, linked
        from now on; else a new person. A person who changed their address at
        the provider is found by the subject and keeps the identity they
        have."""
        issuer, person = self._provider.issuer, signed_in.user
        identity = await self._storage.read_identity_by_issuer_subject(issuer, person.id)
        if identity is not None:
            return identity
        by_email = await self._storage.read_identity_by_email_digest(email_digest(person.email))
        now = utcnow()
        if by_email is None:
            return await self._made(
                new_identity(person.email, now, issuer=issuer, subject=person.id),
                person.display_name,
            )
        # The provider verified the address this identity holds, so it is the
        # same person: an identity the seeding or the operator plane made, or
        # one an older release made with a password. It is linked once; a link
        # to another subject is replaced, since the address, verified again,
        # decides who holds it.
        linked = by_email.model_copy(
            update={
                "issuer": issuer,
                "subject": person.id,
                "updated_at": now,
                "updated_by": by_email.id,
            }
        )
        try:
            await self._storage.write_identity(linked)
        except UniqueKeyTaken:
            # A second sign-in of the same subject linked it meanwhile.
            raced = await self._storage.read_identity_by_issuer_subject(issuer, person.id)
            if raced is None:
                raise
            return raced
        return linked

    async def _made(self, identity: Identity, display_name: str) -> Identity:
        """A new person with their personal org, in one commit. Two first
        sign-ins that race for one address or subject meet the unique key,
        and the loser reads the winner's identity."""
        try:
            await create_person(self._storage, identity, display_name)
        except UniqueKeyTaken:
            raced = await self._storage.read_identity_by_email_digest(email_digest(identity.email))
            if raced is None:
                raise
            return raced
        return identity

    async def _join_through_provider(
        self,
        rctx: RequestContext,
        identity: Identity,
        signed_in: ProvidedSignIn,
        held: tuple[OrgMembership, ...],
    ) -> bool:
        """The membership a sign-in through one of the provider's organizations
        lands: the invitation the person accepted, with its role; else, for a
        sign-in through the org's single sign-on, a member's place when the
        person's address is in a domain the org verified. Anything else lands
        nothing, and the sign-in goes on: a place the person cannot have is
        not a reason to refuse them their own. True when it tried to land one.

        A person who holds a live place in the org the organization stands
        for is not asked about: each of their places carries its org's
        organization id. The provider's invitations are read for them only
        when the org holds a pending invitation to their own address, which
        their sign-in may have accepted. Anyone else is asked about in full,
        and the provider's invitations are read only while the org holds a
        pending invitation, since only a pending one is accepted."""
        organization_id = signed_in.organization_id
        assert organization_id is not None
        member_of = next((m.org for m in held if m.org.provider_org_id == organization_id), None)
        try:
            if member_of is not None:
                org, provided = member_of, None
                pending = await self._storage.read_pending_invitation(org.id, identity.email)
            else:
                provided = await self._provider.get_organization(
                    organization_id, deadline=rctx.deadline
                )
                found = await self._org_of(provided)
                if found is None:
                    return False
                org = found
                pending = next(iter(await self._storage.read_invitations(org.id, None, 1)), None)
            accepted = (
                None
                if pending is None
                else await self._provider.accepted_invitation(
                    organization_id=organization_id,
                    user_id=signed_in.user.id,
                    email=signed_in.user.email,
                    deadline=rctx.deadline,
                )
            )
        except (ProviderRefused, ProviderUnavailable) as error:
            log.warning(
                "sign-in through organization %s joined nothing: %s",
                organization_id,
                error,
            )
            return False
        invitation = (
            None
            if accepted is None
            else await self._storage.read_invitation_by_provider_id(org.id, accepted.id)
        )
        try:
            if invitation is not None and invitation.state is InvitationState.PENDING:
                await self._accept(rctx, org, identity, signed_in, invitation)
                return True
            if provided is not None and signed_in.via_sso and org.kind is OrgKind.TEAM:
                if sso_joins(identity.email, provided.verified_domains):
                    await self._join(rctx, org, identity, signed_in, Role.MEMBER, org.created_by)
                    return True
        except (MembershipLimitReached, PlanLimitReached) as error:
            # A person over their own bound of orgs, or an org whose plan has
            # no seat left: the invitation stays pending, and the sign-in
            # goes on into the places the person has.
            log.warning("sign-in into org %s joined nothing: %s", org.id, error.message)
        return False

    async def _org_of(self, provided: ProvidedOrganization) -> Org | None:
        """The living team or personal org the provider's organization stands
        for: the one its external id names, which names it back."""
        try:
            org_id = UUID(provided.external_id or "")
        except ValueError:
            return None
        org = await self._storage.read_org(org_id)
        if org is None or org.deleted_at is not None or org.provider_org_id != provided.id:
            return None
        return org

    async def _accept(
        self,
        rctx: RequestContext,
        org: Org,
        identity: Identity,
        signed_in: ProvidedSignIn,
        invitation: Invitation,
    ) -> None:
        """The invitation's membership, with its role and recorded as its
        inviter's, and the invitation accepted, in one commit. A person who
        is a member already keeps their place and the invitation closes."""
        now = utcnow()
        user_id = new_id()
        accepted = invitation.model_copy(
            update={
                "state": InvitationState.ACCEPTED,
                "accepted_user_id": user_id,
                "updated_at": now,
                "updated_by": invitation.created_by,
            }
        )
        user, created = await self._join(
            rctx,
            org,
            identity,
            signed_in,
            invitation.role,
            invitation.created_by,
            accepted,
            user_id,
        )
        if not created:
            await self._storage.write_invitation(
                org.id, accepted.model_copy(update={"accepted_user_id": user.id})
            )

    async def _join(
        self,
        rctx: RequestContext,
        org: Org,
        identity: Identity,
        signed_in: ProvidedSignIn,
        role: Role,
        actor_id: UUID,
        invitation: Invitation | None = None,
        user_id: UUID | None = None,
    ) -> tuple[User, bool]:
        shown = signed_in.user.display_name or identity.email.partition("@")[0]
        return await add_member_to(
            self._storage,
            self._relay,
            org_id=org.id,
            user_id=user_id or new_id(),
            email=identity.email,
            display_name=shown,
            role=role,
            actor_id=actor_id,
            request=rctx,
            max_orgs=self._options.max_orgs_per_identity,
            invitation=invitation,
            admission=self._admission(rctx, org.id),
        )

    async def _with_personal(
        self, identity: Identity, memberships: tuple[OrgMembership, ...]
    ) -> tuple[OrgMembership, ...]:
        """The places a sign-in answers with, the personal org among them. A
        person the release before this one made has none yet, whether the
        backfill ran before it or not, so the sign-in makes it: every person
        who signs in has a place to work. Two sign-ins that race to make it
        meet the unique key, and the loser reads the winner's."""
        if any(m.org.personal for m in memberships):
            return memberships
        if len(memberships) >= self._options.max_orgs_per_identity:
            # A place past the bound would make every read of this person's
            # places refuse; a person at the bound has places to work.
            return memberships
        name = memberships[0].user.display_name if memberships else ""
        try:
            await create_person(self._storage, identity, name, new=False)
        except UniqueKeyTaken:
            pass
        return await self._memberships_of(identity.id)

    async def _second_factor_holds(self, identity: Identity, code: str) -> bool:
        """The code matches the identity's enrolled secret in the window around
        now, and its step was not used before: the step is recorded in one
        conditional write, so of two sign-ins presenting one code, one passes."""
        if not identity.totp_enrolled or identity.totp_secret is None:
            raise ValidationFailed("no second factor is enrolled for this sign-in")
        secret = self._totp.open(identity.id, identity.totp_secret)
        step = matching_totp_step(secret, code, self._clock())
        if step is None:
            return False
        return await self._storage.use_totp_step(identity.id, step)

    async def _issue_login(
        self,
        identity: Identity,
        memberships: tuple[OrgMembership, ...],
        *,
        provider_session_id: str | None = None,
    ) -> IssuedLogin:
        """The credential that carries no tenant, stored under the system scope."""
        token, session = self._new_login(identity, provider_session_id=provider_session_id)
        await self._create_session(EMPTY_UUID, session)
        return IssuedLogin(token=token, expires_at=session.expires_at, memberships=memberships)

    def _new_login(
        self,
        identity: Identity,
        *,
        second_factor_at: datetime | None = None,
        provider_session_id: str | None = None,
    ) -> tuple[str, Session]:
        """A sign-in credential and the row that keeps it, as its digest."""
        now = utcnow()
        token = mint_token(CredentialKind.LOGIN)
        session = Session(
            id=new_id(),
            created_at=now,
            updated_at=now,
            created_by=identity.id,
            updated_by=identity.id,
            identity_id=identity.id,
            token_hash=hash_token(token),
            credential_kind=CredentialKind.LOGIN,
            expires_at=now + self._options.login_ttl,
            second_factor_at=second_factor_at,
            provider_session_id=provider_session_id,
        )
        return token, session

    async def authenticate_login(self, rctx: RequestContext, credential: str) -> IdentityContext:
        kind = credential_kind_of(credential)
        if kind is None or kind not in IDENTITY_CREDENTIALS:
            raise InvalidCredential(
                "expected the sign-in credential, a session token, or an operator token"
            )
        # The credential and the identity it proves in one read: the operator
        # gate takes what it needs of the identity from the stage, so an
        # operator's call reads the identity once.
        found = await self._storage.read_session_with_identity_by_digest(hash_token(credential))
        if found is None:
            raise InvalidCredential(f"unknown {kind.value} credential")
        org_id, proof, identity = found
        self._check_session(proof, kind)
        if kind is CredentialKind.SESSION_TOKEN:
            # A session proves its user's identity only while it proves the
            # tenant too: the org, the user, and the membership are live.
            await self._principal(org_id, proof.user_id, proof)
        if identity is None:
            raise InvalidCredential("the identity is gone")
        return IdentityContext(
            request_id=rctx.request_id,
            app=rctx.app,
            trace_id=rctx.trace_id,
            traceparent=rctx.traceparent,
            caused_by_request_id=rctx.caused_by_request_id,
            deadline=rctx.deadline,
            identity_id=identity.id,
            email=identity.email,
            credential_kind=kind,
            credential_id=proof.id,
            second_factor=proof.second_factor_at is not None,
            operator_role=proof.operator_role,
            operator_entry=identity.operator_role,
            second_factor_enrolled=identity.totp_enrolled,
        )

    async def exchange_login(self, ictx: IdentityContext, org_id: UUID) -> IssuedSession:
        self._refuse_operator_token(ictx)
        org, user, membership = await self._principal_in(org_id, ictx.identity_id)
        # The credential presented: the sign-in, or the session a switch ends.
        # The new session carries the provider's session it came from.
        presented = await self._storage.read_session_by_id(ictx.credential_id)
        if presented is None:
            raise InvalidCredential("the credential behind the exchange is gone")
        now = utcnow()
        token = mint_token(CredentialKind.SESSION_TOKEN)
        session = Session(
            id=new_id(),
            created_at=now,
            updated_at=now,
            created_by=user.id,
            updated_by=user.id,
            identity_id=ictx.identity_id,
            user_id=user.id,
            token_hash=hash_token(token),
            credential_kind=CredentialKind.SESSION_TOKEN,
            expires_at=now + self._options.session_ttl,
            provider_session_id=presented[1].provider_session_id,
        )
        if ictx.credential_kind is CredentialKind.SESSION_TOKEN:
            await self._switch(ictx, presented, org_id, session, now)
        else:
            await self._exchange_sign_in(ictx, presented, org_id, session, now)
        return IssuedSession(
            token=token, expires_at=session.expires_at, org=org, user=user, role=membership.role
        )

    async def _exchange_sign_in(
        self,
        ictx: IdentityContext,
        found: tuple[UUID, Session],
        org_id: UUID,
        session: Session,
        now: datetime,
    ) -> None:
        """The exchange of a sign-in: it ends in the write that lands the
        session, so one sign-in makes one session. A second exchange of it,
        a replay or a retry, is refused, and the person signs in again. A
        sign-in has no socket and no tenant, so nothing is announced."""
        _, presented = found
        self._check_session(presented, CredentialKind.LOGIN)
        await exchange_sign_in(
            self._storage, org_id, session, ended_by(presented, ictx.identity_id, now)
        )

    async def _switch(
        self,
        ictx: IdentityContext,
        found: tuple[UUID, Session],
        org_id: UUID,
        session: Session,
        now: datetime,
    ) -> None:
        """The exchange of a session for another: the one presented ends in the
        write that lands the new one, and its revocation is announced under the
        tenant it belonged to, by its own user, so its socket closes."""
        ended_org_id, presented = found
        self._check_session(presented, CredentialKind.SESSION_TOKEN)
        ended = ended_by(presented, presented.user_id, now)
        row = self._revocation_row(ictx, ended_org_id, ended, now)
        try:
            await self._storage.replace_session(org_id, session, ended_org_id, ended, (row,))
        except NotFound:
            raise InvalidCredential("the session behind the switch is gone") from None
        except UniqueKeyTaken:
            raise  # a key of the new session, not the one presented
        except Conflict:
            raise CredentialExpired("session revoked") from None
        await self._relay.relay(ended_org_id, row)

    async def get_identity_memberships(
        self, ictx: IdentityContext, after: UUID | None, limit: int
    ) -> OrgMembershipPage:
        self._refuse_operator_token(ictx)
        limit = self._clamp(limit)
        rows = await self._storage.read_memberships_by_identity(ictx.identity_id, limit + 1, after)
        return OrgMembershipPage(items=tuple(rows[:limit]), has_more=len(rows) > limit)

    async def authenticate(self, rctx: RequestContext, credential: str) -> OpContext:
        kind = credential_kind_of(credential)
        if kind is CredentialKind.SESSION_TOKEN:
            found = await self._storage.read_session_by_digest(hash_token(credential))
            if found is None:
                raise InvalidCredential("unknown session token")
            org_id, session = found
            self._check_session(session, CredentialKind.SESSION_TOKEN)
            org, user, membership = await self._principal(org_id, session.user_id, session)
            return build_context(
                rctx,
                user_id=user.id,
                org_id=org.id,
                role=membership.role,
                permissions=permissions_of(membership.role),
                credential_kind=kind,
                teams=membership.teams,
                credential_id=session.id,
            )
        if kind is CredentialKind.API_KEY:
            found = await self._storage.read_api_key_by_digest(hash_token(credential))
            if found is None:
                raise InvalidCredential("unknown api key")
            org_id, api_key = found
            self._check_api_key(api_key)
            (
                found_org,
                found_user,
                found_membership,
                account,
            ) = await self._storage.read_key_principal(org_id, api_key.user_id)
            org, user, membership = self._live_principal(found_org, found_user, found_membership)
            ctx = build_context(
                rctx,
                user_id=user.id,
                org_id=org.id,
                role=capped_role(api_key.role, membership.role),
                permissions=permissions_of(capped_role(api_key.role, membership.role)),
                credential_kind=kind,
                teams=membership.teams,
                credential_id=api_key.id,
            )
            self._refuse_keyless(self._entitlements.entitlements_of(ctx, account))
            return ctx
        raise InvalidCredential("this route accepts a session token or an api key")

    async def admit_operator(self, ictx: IdentityContext) -> OperatorContext:
        if ictx.credential_kind not in (CredentialKind.LOGIN, CredentialKind.OPERATOR_TOKEN):
            raise InvalidCredential(
                "the operator plane takes the sign-in credential or an operator token"
            )
        # The allowlist entry and the enrolment as the stage read them, with
        # the credential, in this request: no second read of the identity.
        entry = ictx.operator_entry
        if entry is None:
            raise NotAnOperator("this identity is not an operator")
        if ictx.credential_kind is CredentialKind.OPERATOR_TOKEN:
            # The one exception to "a sign-in alone never admits": a second
            # factor or the grant job stood behind the mint. The token carries
            # one permission, and never more than the entry grants today.
            if ictx.operator_role is None:
                raise InvalidCredential("an operator token names its permission")
            granted = operator_permissions_of(ictx.operator_role) & operator_permissions_of(entry)
        elif not ictx.second_factor_enrolled:
            # Allowlisted, with no second factor yet: the two calls that
            # enrol one, and nothing else.
            granted = frozenset({OperatorPermission.ENROL})
        elif not ictx.second_factor:
            raise SecondFactorRequired("sign in with the code from your authenticator")
        else:
            # A sign-in with its code is exchanged once for a token, and every
            # read and write on the plane is a token's, which is listed and
            # revoked by itself (ADR 0068). The mint caps the token at the
            # entry the stage carries.
            granted = frozenset({OperatorPermission.MINT})
        return OperatorContext(
            request_id=ictx.request_id,
            app=ictx.app,
            trace_id=ictx.trace_id,
            traceparent=ictx.traceparent,
            caused_by_request_id=ictx.caused_by_request_id,
            deadline=ictx.deadline,
            identity_id=ictx.identity_id,
            email=ictx.email,
            credential_kind=ictx.credential_kind,
            credential_id=ictx.credential_id,
            second_factor=ictx.second_factor,
            operator_role=ictx.operator_role,
            operator_entry=entry,
            second_factor_enrolled=ictx.second_factor_enrolled,
            permissions=granted,
        )

    async def grant_operator(
        self, rctx: RequestContext, email: str, operator_role: OperatorRole
    ) -> Identity:
        identity = await self._storage.read_identity_by_email_digest(email_digest(email))
        if identity is None:
            if not is_platform_email(email):
                raise NotFound("no identity holds that email; an operator signs up first")
            identity = await self._platform_identity(email)
        if identity.operator_role is operator_role:
            return identity
        return await self._write_entry(rctx, identity, operator_role)

    async def disable_operator(self, rctx: RequestContext, email: str) -> Identity:
        identity = await self._storage.read_identity_by_email_digest(email_digest(email))
        if identity is None:
            raise NotFound("no identity holds that email")
        if identity.operator_role is None:
            return identity
        return await self._write_entry(rctx, identity, None)

    async def _platform_identity(self, email: str) -> Identity:
        """The provisioner or the smoke identity, made the first time the grant
        job names it: no org, and no way to sign in, since the provider never
        vouches for an address in the platform's domain."""
        identity = new_identity(email, utcnow())
        await self._storage.write_identity(identity)
        return identity

    async def _write_entry(
        self, rctx: RequestContext, identity: Identity, operator_role: OperatorRole | None
    ) -> Identity:
        """The allowlist entry changes with its audit row, in one commit under
        the system scope: the grant job acts for the platform, so the actor
        is the system user."""
        now = utcnow()
        updated = identity.model_copy(
            update={"operator_role": operator_role, "updated_at": now, "updated_by": EMPTY_UUID}
        )
        kind = "granted" if operator_role is not None else "disabled"
        row = OutboxRow(
            id=new_id(),
            created_at=now,
            org_id=EMPTY_UUID,
            kind=f"tenancy.operator.{kind}",
            target_id=identity.id,
            payload={} if operator_role is None else {"operator_role": operator_role.value},
            actor_id=EMPTY_UUID,
            request_id=rctx.request_id,
            traceparent=rctx.traceparent,
            app=rctx.app.type.value,
        )
        if operator_role is None:
            # Off the plane: every token and every sign-in with a code it
            # holds ends in the same commit, so a later grant revives none.
            ended = await self._storage.disable_operator(updated, (row,), now)
            log.info("operator %s disabled, %d live credentials ended", identity.id, ended)
        else:
            await self._storage.write_identity(updated, (row,))
        await self._relay.relay(EMPTY_UUID, row)
        return updated

    async def grant_operator_token(
        self,
        rctx: RequestContext,
        email: str,
        expires_in: timedelta | None = None,
        operator_role: OperatorRole | None = None,
    ) -> IssuedOperatorToken:
        identity = await self._storage.read_identity_by_email_digest(email_digest(email))
        if identity is None or identity.operator_role is None:
            raise NotAnOperator("no operator holds that email")
        role = operator_role or identity.operator_role
        if not operator_permissions_of(role) <= operator_permissions_of(identity.operator_role):
            raise NotAuthorized(
                f"the entry grants {identity.operator_role.value}, not {role.value}"
            )
        issued, session = new_operator_token(
            identity.id, role, expires_in, self._options.operator_token_ttl
        )
        await self._create_session(EMPTY_UUID, session)
        return issued

    async def resume(
        self,
        rctx: RequestContext,
        org_id: UUID,
        credential_kind: CredentialKind,
        credential_id: UUID,
        *,
        record_use: bool = True,
    ) -> SocketPrincipal:
        account: BillingAccount | None = None
        if credential_kind is CredentialKind.SESSION_TOKEN:
            session = await self._storage.read_session(org_id, credential_id)
            if session is None:
                raise InvalidCredential("the session behind the ticket is gone")
            self._check_session(session, CredentialKind.SESSION_TOKEN)
            org, user, membership = await self._principal(
                org_id, session.user_id, session if record_use else None
            )
            role = membership.role
            expires_at = session.expires_at
        elif credential_kind is CredentialKind.API_KEY:
            api_key = await self._storage.read_api_key(org_id, credential_id)
            if api_key is None:
                raise InvalidCredential("the api key behind the ticket is gone")
            self._check_api_key(api_key)
            (
                found_org,
                found_user,
                found_membership,
                account,
            ) = await self._storage.read_key_principal(org_id, api_key.user_id)
            org, user, membership = self._live_principal(found_org, found_user, found_membership)
            role = capped_role(api_key.role, membership.role)
            expires_at = api_key.expires_at
        else:
            raise InvalidCredential("a ticket stands for a session token or an api key")
        ctx = build_context(
            rctx,
            user_id=user.id,
            org_id=org.id,
            role=role,
            permissions=permissions_of(role),
            credential_kind=CredentialKind.SOCKET_TICKET,
            teams=membership.teams,
            credential_id=credential_id,
        )
        if credential_kind is CredentialKind.API_KEY:
            # The socket asks the plan what the key's every request asks it,
            # from the account read with the principal: a key the plan no
            # longer allows closes its socket at the next recheck.
            self._refuse_keyless(self._entitlements.entitlements_of(ctx, account))
        return SocketPrincipal(
            ctx=ctx,
            expires_at=expires_at,
            credential_kind=credential_kind,
            membership_id=membership.id,
        )

    async def redeem_ticket(self, rctx: RequestContext, ticket: str) -> SocketPrincipal:
        if credential_kind_of(ticket) is not CredentialKind.SOCKET_TICKET:
            raise InvalidCredential("expected a socket ticket")
        digest = hash_token(ticket)
        if await self._cache.get(EMPTY_UUID, TICKET_USED_KEY + digest) is not None:
            raise InvalidCredential("socket ticket already redeemed")
        now = utcnow()
        # One conditional write consumes the ticket: only the first redeemer gets the row.
        consumed = await self._storage.redeem_socket_ticket(digest, now)
        if consumed is None:
            raise InvalidCredential("unknown or already redeemed socket ticket")
        org_id, behind = consumed
        await self._cache.put(EMPTY_UUID, TICKET_USED_KEY + digest, b"1", self._options.ticket_ttl)
        if behind.expires_at <= now:
            raise InvalidCredential("socket ticket expired")
        return await self.resume(rctx, org_id, behind.credential_kind, behind.credential_id)

    async def service_context(self, rctx: RequestContext, org_id: UUID, user_id: UUID) -> OpContext:
        # Minted for the tenant on the service role's authority; the person is
        # the attribution, not the authority: they authorized the work once, at
        # enqueue, so neither their user nor their membership is read, and a
        # member who has left does not stop the work they asked for.
        org = await self._storage.read_org(org_id)
        if org is None or org.deleted_at is not None:
            raise InvalidCredential("the org is gone")
        return build_context(
            rctx,
            user_id=user_id,
            org_id=org.id,
            role=Role.SERVICE,
            permissions=permissions_of(Role.SERVICE),
            credential_kind=CredentialKind.INTERNAL,
        )

    async def member_context(
        self, rctx: RequestContext, org_id: UUID, email: str
    ) -> OpContext | None:
        # Slack's profile may spell the address with capitals; the digest is
        # of the folded address, so any spelling finds the person.
        identity = await self._storage.read_identity_by_email_digest(email_digest(email.strip()))
        if identity is None:
            return None
        try:
            org, user, membership = await self._principal_in(org_id, identity.id)
        except NotAuthorized:
            return None
        return build_context(
            rctx,
            user_id=user.id,
            org_id=org.id,
            role=membership.role,
            permissions=permissions_of(membership.role),
            credential_kind=CredentialKind.INTERNAL,
            teams=membership.teams,
        )

    async def _every_org(self) -> list[Org]:
        """Every tenant, page by page: a sweep that stopped at the first clamp
        would never reach the tenants behind it."""
        orgs: list[Org] = []
        after_id: UUID | None = None
        while True:
            page = await self._storage.read_orgs(self._options.max_limit, after_id)
            orgs.extend(page)
            if len(page) < self._options.max_limit:
                return orgs
            after_id = page[-1].id

    async def service_contexts(self, rctx: RequestContext) -> list[OpContext]:
        # Minted for the tenant, not for a member: the system user is the actor
        # and no user or membership is read, so it costs one read per page of
        # tenants and a tenant whose members have all left is still swept. So
        # is a deleted tenant: its rows and its claimed work are the sweep's to
        # settle, until a pass found none left and marked it purged. The system
        # scope comes first: login credentials live under it.
        orgs = [org for org in await self._every_org() if org.purged_at is None]
        before = utcnow() - self._options.retention
        scopes = [EMPTY_UUID, *(org.id for org in orgs)]
        self._pass = (
            rctx.request_id,
            frozenset(scopes),
            frozenset(org.id for org in orgs if past_retention(org, before)),
        )
        return [
            build_context(
                rctx,
                user_id=EMPTY_UUID,
                org_id=org_id,
                role=Role.SERVICE,
                permissions=permissions_of(Role.SERVICE),
                credential_kind=CredentialKind.INTERNAL,
            )
            for org_id in scopes
        ]

    # The principal.

    async def get_org(self, ctx: OpContext) -> Org:
        ctx.require(Permission.READ)
        org = await self._storage.read_org(ctx.org_id)
        if org is None or org.deleted_at is not None:
            raise NotFound(f"org {ctx.org_id} not found")
        return org

    async def get_me(self, ctx: OpContext) -> OrgMembership:
        ctx.require(Permission.READ)
        org, user, membership = await self._storage.read_principal(ctx.org_id, ctx.user_id)
        if org is None or org.deleted_at is not None:
            raise NotFound(f"org {ctx.org_id} not found")
        if user is None or user.deleted_at is not None or membership is None:
            raise NotFound(f"user {ctx.user_id} not found")
        return OrgMembership(org=org, user=user, role=membership.role)

    async def create_org(
        self, ctx: OpContext, name: str, slug: str | None, attempt: Attempt | None = None
    ) -> OrgMembership:
        ctx.require(Permission.READ)
        # A person makes an org, not a program: an api key belongs to the
        # tenant it was minted in, and a new tenant is not its to make.
        if ctx.security.credential_kind is not CredentialKind.SESSION_TOKEN:
            raise NotAuthorized("only a signed-in person creates an org")
        try:
            check_org(name, slug)
        except ValueError as error:
            raise ValidationFailed(str(error)) from None
        user = await self._live_user(ctx, ctx.user_id)
        identity = await self._storage.read_identity(user.identity_id)
        if identity is None:
            raise InvalidCredential("the identity is gone")
        org_id = new_id() if attempt is None else attempt.target_id
        if attempt is not None and await self._storage.read_org(org_id) is not None:
            # The rerun of a create that landed: the place as it stands.
            org, owner, membership = await self._principal_in(org_id, identity.id)
            return OrgMembership(org=org, user=owner, role=membership.role)
        name = name.strip()
        slug = slug or slug_from_name(name, slug_suffix())
        if await self._storage.read_org_by_slug(slug) is not None:
            raise Conflict(f"org slug {slug!r} is taken")
        most = self._options.max_orgs_per_identity
        refuse_one_more(identity.id, await users_of(self._storage, identity.id, most), most)
        # The person's name in the new org is the one they carry here.
        org, owner, membership = owner_rows(
            org_id, name, slug, identity, user.display_name, utcnow()
        )
        # One commit, as every create of a tenant: a slug taken meanwhile is
        # UniqueKeyTaken, a Conflict, and nothing lands.
        await self._storage.create_org_with_owner(org.id, org, owner, membership)
        return OrgMembership(org=org, user=owner, role=membership.role)

    async def get_identity(self, ctx: OpContext) -> Identity:
        ctx.require(Permission.READ)
        user = await self._live_user(ctx, ctx.user_id)
        identity = await self._storage.read_identity(user.identity_id)
        if identity is None:
            raise NotFound(f"identity {user.identity_id} not found")
        return identity

    async def set_time_zone(self, ctx: OpContext, time_zone: str) -> Identity:
        ctx.require(Permission.READ)
        try:
            check_time_zone(time_zone)
        except ValueError as error:
            raise ValidationFailed(str(error)) from None
        user = await self._live_user(ctx, ctx.user_id)
        if not await self._storage.write_time_zone(user.identity_id, time_zone, utcnow()):
            raise NotFound(f"identity {user.identity_id} not found")
        return await self.get_identity(ctx)

    async def get_time_zone(self, ctx: OpContext, user_id: UUID) -> str | None:
        ctx.require(Permission.READ)
        user = await self._storage.read_user(ctx.org_id, user_id)
        if user is None:
            return None
        identity = await self._storage.read_identity(user.identity_id)
        return None if identity is None else identity.time_zone

    # Invitations and single sign-on.

    async def invite_member(
        self, ctx: OpContext, email: str, role: Role, attempt: Attempt | None = None
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
        # The plan's seats are checked here, before anything is sent: this is
        # the one door a person of a deployed environment comes in by. The
        # acceptance asks again, since the org may have filled up meanwhile.
        await self._refuse_past_seats(ctx)
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
        self, ctx: OpContext, after: UUID | None, limit: int
    ) -> InvitationPage:
        ctx.require(Permission.MANAGE_MEMBERS)
        limit = self._clamp(limit)
        rows = await self._storage.read_invitations(ctx.org_id, after, limit + 1)
        return InvitationPage(items=tuple(rows[:limit]), has_more=len(rows) > limit)

    async def resend_invitation(self, ctx: OpContext, invitation_id: UUID) -> Invitation:
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

    async def revoke_invitation(self, ctx: OpContext, invitation_id: UUID) -> Invitation:
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

    async def sso_setup_link(self, ctx: OpContext, intent: PortalIntent, return_url: str) -> str:
        ctx.require(Permission.MANAGE_MEMBERS)
        if origin_of(return_url) not in {origin_of(u) for u in self._options.sign_in_redirect_uris}:
            raise ValidationFailed("the link comes back to this environment's portal only")
        current = await self.get_org(ctx)
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

    async def _provider_org(self, ctx: OpContext) -> Org:
        """The org, with its organization at the identity provider, made the
        first time and kept: the provider finds it by the org's id when a
        write of the link was lost, so a rerun never makes a second one."""
        org = await self.get_org(ctx)
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

    async def _refuse_member(self, ctx: OpContext, email: str) -> None:
        """An address whose person is a member of the org already is Conflict."""
        identity = await self._storage.read_identity_by_email_digest(email_digest(email))
        if identity is None:
            return
        most = self._options.max_orgs_per_identity
        for member_org_id, user in await users_of(self._storage, identity.id, most):
            if member_org_id == ctx.org_id and user.deleted_at is None:
                raise Conflict("that person is a member already")

    async def _pending_invitation(self, ctx: OpContext, invitation_id: UUID) -> Invitation:
        invitation = await self._storage.read_invitation(ctx.org_id, invitation_id)
        if invitation is None:
            raise NotFound(f"invitation {invitation_id} not found")
        if invitation.state is not InvitationState.PENDING:
            raise InvitationClosed(f"the invitation is {invitation.state.value}")
        return invitation

    async def _close_invitation(
        self, ctx: OpContext, invitation: Invitation, state: InvitationState
    ) -> Invitation:
        closed = invitation.model_copy(
            update={"state": state, "updated_at": utcnow(), "updated_by": ctx.user_id}
        )
        row = outbox_row(ctx, "tenancy.invitation.updated", closed.id, {})
        await self._storage.write_invitation(ctx.org_id, closed, (row,))
        await self._relay.relay(ctx.org_id, row)
        return closed

    async def rename_user(self, ctx: OpContext, user_id: UUID, display_name: str) -> User:
        ctx.require(Permission.READ)
        if user_id != ctx.user_id:
            ctx.require(Permission.MANAGE_MEMBERS)
        existing = await self._live_user(ctx, user_id)
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

    async def get_users(self, ctx: OpContext, after: UUID | None, limit: int) -> UserPage:
        ctx.require(Permission.READ)
        limit = self._clamp(limit)
        rows = await self._storage.read_users(ctx.org_id, after, limit + 1)
        return UserPage(items=tuple(rows[:limit]), has_more=len(rows) > limit)

    async def get_user(self, ctx: OpContext, user_id: UUID) -> User:
        ctx.require(Permission.READ)
        user = await self._storage.read_user(ctx.org_id, user_id)
        if user is None or user.deleted_at is not None:
            raise NotFound(f"user {user_id} not found")
        return user

    # Memberships.

    async def get_memberships(
        self, ctx: OpContext, after: UUID | None, limit: int
    ) -> MembershipPage:
        ctx.require(Permission.READ)
        limit = self._clamp(limit)
        rows = await self._storage.read_memberships(ctx.org_id, limit + 1, after)
        return MembershipPage(items=tuple(rows[:limit]), has_more=len(rows) > limit)

    async def update_membership_role(self, ctx: OpContext, user_id: UUID, role: Role) -> Membership:
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

    async def remove_member(self, ctx: OpContext, user_id: UUID) -> User:
        ctx.require(Permission.MANAGE_MEMBERS)
        if user_id == ctx.user_id:
            raise ValidationFailed("a member cannot remove themselves")
        membership = await self._live_membership(ctx, user_id)
        user = await self._live_user(ctx, user_id)
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
        rows = (row, *await self._seat_rows(ctx))

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

    async def delete_account(
        self, ctx: OpContext, confirm_email: str, return_to: str | None = None
    ) -> AccountDeleted:
        ctx.require(Permission.READ)
        # A person deletes their account, not a program: an api key belongs
        # to the tenant it was minted in, and the person is not its to erase.
        if ctx.security.credential_kind is not CredentialKind.SESSION_TOKEN:
            raise NotAuthorized("only a signed-in person deletes their account")
        if return_to is not None and return_to not in self._options.sign_out_return_uris:
            raise ValidationFailed("that is not this environment's sign-out return")
        user = await self._live_user(ctx, ctx.user_id)
        identity = await self._storage.read_identity(user.identity_id)
        if identity is None:
            raise InvalidCredential("the identity is gone")
        if not confirms_deletion(identity.email, confirm_email):
            raise ValidationFailed("type your account's email to delete it")
        if identity.operator_role is not None:
            raise OperatorRoleHeld(
                "an operator's account is deleted once the operator role is taken off"
            )
        places = await self._memberships_of(identity.id)
        refusal = await self._last_owner(places)
        if refusal.orgs:
            raise refusal
        asking = await self._storage.read_session(ctx.org_id, ctx.security.credential_id)
        rows: list[OutboxRow] = []
        for place in places:
            # Each row is written under the person's own place in its org:
            # the removal is theirs, and so is the work it asks for.
            where = await self.service_context(ctx, place.org.id, place.user.id)
            if place.org.personal_identity_id == identity.id:
                rows.append(
                    outbox_row(
                        where,
                        work_row_kind(WorkKind.DELETE_ACCOUNT),
                        place.org.id,
                        {"provider_user_id": self._provider_user_id(identity)},
                    )
                )
                continue
            rows.append(
                outbox_row(where, "tenancy.user.deleted", place.user.id, user_payload(place.user))
            )
            rows.append(
                outbox_row(where, work_row_kind(WorkKind.UNASSIGN_TASKS), place.user.id, {})
            )
            rows.extend(await self._seat_rows(where))

        users = {place.org.id: place.user.id for place in places}

        def revocation(org_id: UUID, kind: str, credential_id: UUID) -> OutboxRow:
            # Ids only, as every revocation's row; the actor is the person's
            # place in the org, the system user for one they had already left.
            actor = users.get(org_id, EMPTY_UUID)
            return OutboxRow(
                id=new_id(),
                created_at=utcnow(),
                org_id=org_id,
                kind=kind,
                target_id=credential_id,
                payload={"user_id": str(actor)},
                actor_id=actor,
                request_id=ctx.request_id,
                traceparent=ctx.traceparent,
                app=ctx.app.type.value,
            )

        # The tenants that must keep an owner once the person goes: the
        # storage counts their owners again under a lock, so two owners who
        # leave at once never leave one with none.
        owned = tuple(
            place.org.id for place in places if not place.org.personal and place.role is Role.OWNER
        )
        now = utcnow()
        try:
            revocations = await self._storage.delete_person(
                identity.id, identity.email, owned, tuple(rows), revocation
            )
        except NotFound:
            raise InvalidCredential("the identity is gone") from None
        except Conflict:
            # Another owner left first: the refusal the check above would give now.
            raise await self._last_owner(places) from None
        # Each removal first, so a socket closes because its person left;
        # then each revocation, as the record it is. Every row is durable
        # already: whatever a crash leaves unrelayed, the sweep relays.
        by_org: dict[UUID, list[OutboxRow]] = {}
        for landed in (*rows, *revocations):
            by_org.setdefault(landed.org_id, []).append(landed)
        for org_id, landed_rows in by_org.items():
            await self._relay.relay_all(org_id, landed_rows)
        log.info("identity %s deleted its account", identity.id)
        provider_logout = None if asking is None else self._provider_logout(asking, return_to)
        return AccountDeleted(deleted_at=now, provider_logout_url=provider_logout)

    async def _last_owner(self, places: tuple[OrgMembership, ...]) -> LastOwner:
        """The refusal naming every team org the person is the last owner of;
        one naming none when there is no such org."""
        stranded = [
            place.org
            for place in places
            if left_without_owner(
                place.org, place.role, await self._storage.count_members(place.org.id, Role.OWNER)
            )
        ]
        return LastOwner(tuple((str(org.id), org.name, org.slug) for org in stranded))

    def _provider_user_id(self, identity: Identity) -> str | None:
        """The person's name at the identity provider, when this environment's
        provider is the one that named them; None for a person who only ever
        signed in locally."""
        if identity.subject is None or identity.issuer != self._provider.issuer:
            return None
        return identity.subject

    async def delete_personal_org(self, ctx: OpContext) -> Org | None:
        ctx.require(Permission.MANAGE_MEMBERS)
        org = await self._storage.read_org(ctx.org_id)
        if org is None:
            raise NotFound(f"org {ctx.org_id} not found")
        if org.deleted_at is not None:
            return None
        person = org.personal_identity_id
        if person is None or await self._storage.read_identity(person) is not None:
            raise PersonalOrgFixed("only the personal org of a deleted account is deleted")
        now = utcnow()
        # The org row stays as the record that the tenant existed, and a
        # personal org is named after its person, so the name and the slug
        # made from it go now: the row keeps ids and nothing that says who.
        deleted = org.model_copy(
            update={
                "name": DELETED_PERSONAL_ORG_NAME,
                "slug": f"deleted-{org.id}",
                "deleted_at": now,
                "deleted_by": ctx.user_id,
                "updated_at": now,
                "updated_by": ctx.user_id,
            }
        )
        # Announced like any change: the sockets of the tenant close on it.
        row = outbox_row(ctx, "tenancy.org.deleted", org.id, {})
        await self._storage.write_org(org.id, deleted, (row,))
        await self._relay.relay(org.id, row)
        return deleted

    async def delete_org(self, ctx: OpContext, confirm_name: str) -> OrgDeleted:
        ctx.require(Permission.MANAGE_MEMBERS)
        # A person deletes the org, not a program: an api key is the tenant's,
        # and the tenant is not its to end.
        if ctx.security.credential_kind is not CredentialKind.SESSION_TOKEN:
            raise NotAuthorized("only a signed-in owner deletes an organization")
        if ctx.security.role is not Role.OWNER:
            raise NotAuthorized("only an owner deletes an organization")
        org = await self.get_org(ctx)
        if org.personal:
            raise PersonalOrgFixed("a personal org goes only with its person's account")
        if not confirms_org_deletion(org.name, confirm_name):
            raise ValidationFailed("type the organization's name to delete it")
        asking = await self._storage.read_session(ctx.org_id, ctx.security.credential_id)
        user = await self._live_user(ctx, ctx.user_id)
        now = utcnow()
        # The org stays live, with nobody in it, until the queue has ended its
        # providers: deleted first, it could no longer run the work that names
        # it. Its provider organization leaves the row now, so no sign-in
        # through it, by invitation or single sign-on, finds the org meanwhile.
        closed = org.model_copy(
            update={"provider_org_id": None, "updated_at": now, "updated_by": ctx.user_id}
        )
        work = outbox_row(
            ctx,
            work_row_kind(WorkKind.DELETE_ORG),
            org.id,
            {"provider_org_id": org.provider_org_id},
        )

        def member_row(member: User) -> OutboxRow:
            return outbox_row(ctx, "tenancy.user.deleted", member.id, user_payload(member))

        def revocation(kind: str, credential_id: UUID, holder: UUID) -> OutboxRow:
            return outbox_row(ctx, kind, credential_id, {"user_id": str(holder)})

        try:
            ended = await self._storage.write_closed_org(
                ctx.org_id, closed, (work,), member_row, revocation
            )
        except NotFound:
            raise InvalidCredential("the org is gone") from None
        # Each member's removal first, so a socket closes because its person
        # left; then each revocation, as the record it is. Every row is
        # durable already: whatever a crash leaves unrelayed, the sweep relays.
        await self._relay.relay_all(ctx.org_id, (work, *ended))
        log.info("owner %s deleted org %s", ctx.user_id, org.id)
        landing = None if asking is None else await self._land_home(user, asking)
        return OrgDeleted(deleted_at=now, session=landing)

    async def _land_home(self, user: User, asking: Session) -> IssuedSession | None:
        """The session an owner lands on in their personal org once their team
        org is gone, as a switch would make it: the same person, carrying the
        provider's session the one that asked came from. None, and the owner
        signs in again, when they have no personal org or it cannot be made."""
        places = await self._memberships_of(user.identity_id)
        home = next((p for p in places if p.org.personal_identity_id == user.identity_id), None)
        if home is None:
            return None
        now = utcnow()
        token = mint_token(CredentialKind.SESSION_TOKEN)
        session = Session(
            id=new_id(),
            created_at=now,
            updated_at=now,
            created_by=home.user.id,
            updated_by=home.user.id,
            identity_id=user.identity_id,
            user_id=home.user.id,
            token_hash=hash_token(token),
            credential_kind=CredentialKind.SESSION_TOKEN,
            expires_at=now + self._options.session_ttl,
            provider_session_id=asking.provider_session_id,
        )
        try:
            await self._create_session(home.org.id, session)
        except (InfraException, PlatformException) as error:
            # The org is gone either way; only the landing failed.
            log.warning("no session in the personal org of %s: %s", user.identity_id, error)
            return None
        return IssuedSession(
            token=token, expires_at=session.expires_at, org=home.org, user=home.user, role=home.role
        )

    async def delete_closed_org(self, ctx: OpContext) -> Org | None:
        ctx.require(Permission.MANAGE_MEMBERS)
        if ctx.security.role is not Role.SERVICE:
            raise NotAuthorized("a closed org is ended by the platform")
        org = await self._storage.read_org(ctx.org_id)
        if org is None:
            raise NotFound(f"org {ctx.org_id} not found")
        if org.personal:
            raise PersonalOrgFixed("a personal org goes only with its person's account")
        if org.deleted_at is not None:
            return None
        now = utcnow()
        # The row stays as the record, and the sweep purges the tenant once
        # the retention has passed.
        deleted = org.model_copy(
            update={
                "deleted_at": now,
                "deleted_by": ctx.user_id,
                "updated_at": now,
                "updated_by": ctx.user_id,
            }
        )
        # Announced like any change: the sockets of the tenant close on it.
        row = outbox_row(ctx, "tenancy.org.deleted", org.id, {})
        await self._storage.write_org(org.id, deleted, (row,))
        await self._relay.relay(org.id, row)
        return deleted

    async def count_members(self, ctx: OpContext) -> int:
        ctx.require(Permission.READ)
        return await self._storage.count_members(ctx.org_id)

    async def _seat_rows(self, ctx: OpContext) -> tuple[OutboxRow, ...]:
        """The row that asks for a per-seat subscription's quantity to follow a
        change of members, when the org's plan is per seat; none otherwise.
        It rides the change's own commit, as work that follows a write does."""
        entitlements = await self._entitlements.get_entitlements(ctx)
        if not seats_metered(entitlements.plan):
            return ()
        return (outbox_row(ctx, work_row_kind(WorkKind.SYNC_SEATS), ctx.org_id, {}),)

    async def _refuse_past_seats(self, ctx: OpContext) -> None:
        """The plan's bound on members, for one more."""
        entitlements = await self._entitlements.get_entitlements(ctx)
        refuse_past(entitlements.plan, Lever.MEMBERS, await self._storage.count_members(ctx.org_id))

    def _admission(self, rctx: RequestContext, org_id: UUID) -> Admission:
        """What a person joining the org by invitation or by its single sign-on
        is asked: a seat, under the org's service context, since no member of
        the org is acting; and on a per-seat plan, the row that asks for the
        seat count."""

        async def admit() -> tuple[OutboxRow, ...]:
            ctx = await self.service_context(rctx, org_id, EMPTY_UUID)
            await self._refuse_past_seats(ctx)
            return await self._seat_rows(ctx)

        return admit

    async def _refuse_without_keys(self, ctx: OpContext) -> None:
        self._refuse_keyless(await self._entitlements.get_entitlements(ctx))

    @staticmethod
    def _refuse_keyless(entitlements: Entitlements) -> None:
        """PlanLimitReached when the org's plan has no api keys. A key of such
        an org is kept and refused, never revoked: the refusal says why, and
        the key works again the day the org is on a plan with keys."""
        refuse_past(entitlements.plan, Lever.API_KEYS, 0)

    # Credentials.

    async def get_sessions(self, ctx: OpContext, limit: int) -> list[Session]:
        ctx.require(Permission.READ)
        # Live at the storage: a page of dead sessions cannot hide a live one.
        return await self._storage.read_sessions(
            ctx.org_id, ctx.user_id, utcnow(), self._clamp(limit)
        )

    async def revoke_session(self, ctx: OpContext, session_id: UUID) -> Session:
        ctx.require(Permission.READ)
        session = await self._storage.read_session(ctx.org_id, session_id)
        if session is None or session.revoked_at is not None:
            raise NotFound(f"session {session_id} not found")
        if session.user_id != ctx.user_id and not ctx.has(Permission.MANAGE_MEMBERS):
            raise NotAuthorized("only the owner of a session or a member manager may revoke it")
        now = utcnow()
        revoked = session.model_copy(
            update={"revoked_at": now, "updated_at": now, "updated_by": ctx.user_id}
        )
        # Announced like any change: the socket this session opened, in
        # whichever process holds it, closes on the row the relay publishes.
        await self._write_session(ctx, revoked, "revoked")
        return revoked

    async def logout(self, ictx: IdentityContext, return_to: str | None = None) -> SignedOut:
        if return_to is not None and return_to not in self._options.sign_out_return_uris:
            raise ValidationFailed("that is not this environment's sign-out return")
        found = await self._storage.read_session_by_id(ictx.credential_id)
        if found is None:
            raise InvalidCredential("the credential behind the sign-out is gone")
        org_id, presented = found
        now = utcnow()
        if ictx.credential_kind is CredentialKind.SESSION_TOKEN:
            # Announced like any revocation, under the tenant it belonged to,
            # by its own user: the socket it opened, in whichever process
            # holds it, closes on the row the relay publishes.
            ended = ended_by(presented, presented.user_id, now)
            row = self._revocation_row(ictx, org_id, ended, now)
            await self._storage.write_session(org_id, ended, (row,))
            await self._relay.relay(org_id, row)
        else:
            # A sign-in or an operator token: a row of the system scope, which
            # opens no socket and belongs to no tenant, so nothing is announced.
            ended = ended_by(presented, ictx.identity_id, now)
            await self._storage.write_session(EMPTY_UUID, ended)
            log.info(
                "identity %s signed out its %s %s",
                ictx.identity_id,
                ictx.credential_kind.value,
                ended.id,
            )
        return SignedOut(session=ended, provider_logout_url=self._provider_logout(ended, return_to))

    def _revocation_row(
        self, rctx: RequestContext, org_id: UUID, ended: Session, now: datetime
    ) -> OutboxRow:
        """The row that announces a session's end under its tenant, by the
        session's own user, when the stage that ends it is not a tenant's."""
        return OutboxRow(
            id=new_id(),
            created_at=now,
            org_id=org_id,
            kind="tenancy.session.revoked",
            target_id=ended.id,
            payload=self._session_payload(ended),
            actor_id=ended.user_id,
            request_id=rctx.request_id,
            traceparent=rctx.traceparent,
            app=rctx.app.type.value,
        )

    def _provider_logout(self, ended: Session, return_to: str | None) -> str | None:
        """Where the browser goes to end the provider's session behind the one
        that ended here. Acme's session is over whatever this answers: a
        provider this process cannot reach leaves the provider's session to
        its own lifetime, and says so."""
        if ended.provider_session_id is None:
            return None
        try:
            return self._provider.logout_url(
                session_id=ended.provider_session_id, return_to=return_to
            )
        except ProviderUnavailable as error:
            log.warning("the provider's session outlives the sign-out: %s", error.message)
            return None

    async def get_api_keys(self, ctx: OpContext, after: UUID | None, limit: int) -> ApiKeyPage:
        ctx.require(Permission.MANAGE_KEYS)
        # A member manager sees the tenant's keys; anyone else their own, filtered
        # at the storage so a page of other people's keys cannot hide theirs.
        own_only = None if ctx.has(Permission.MANAGE_MEMBERS) else ctx.user_id
        limit = self._clamp(limit)
        # One row past the page, kept out of it: `has_more` is then a fact
        # about the rows, so no key is left unreachable behind a fixed limit.
        rows = await self._storage.read_api_keys(ctx.org_id, after, limit + 1, own_only)
        return ApiKeyPage(items=tuple(rows[:limit]), has_more=len(rows) > limit)

    async def create_api_key(
        self,
        ctx: OpContext,
        name: str,
        role: Role,
        ttl: timedelta | None = None,
        attempt: Attempt | None = None,
    ) -> IssuedApiKey:
        ctx.require(Permission.MANAGE_KEYS)
        # A key never mints its successor. Revoking a leaked key has to end the
        # access it gave; a key that can issue another one outlives its own
        # revocation, and nothing ties the successor back to it. `logout`
        # gates on the credential kind for the same reason.
        if ctx.security.credential_kind is CredentialKind.API_KEY:
            raise NotAuthorized("an api key cannot create another; sign in to create one")
        if role is Role.SERVICE:
            raise ValidationFailed("service is not an api key role")
        if not role_at_most(role, ctx.security.role):
            raise NotAuthorized(f"cannot issue role {role.value} above {ctx.security.role.value}")
        if ttl is not None and not (timedelta(0) < ttl <= self._options.api_key_ttl):
            raise ValidationFailed(
                f"an api key lives between one second and {self._options.api_key_ttl.days} days"
            )
        await self._refuse_without_keys(ctx)
        now = utcnow()
        key = mint_token(CredentialKind.API_KEY)
        api_key = ApiKey(
            id=attempt.target_id if attempt else new_id(),
            name=name,
            created_at=now,
            updated_at=now,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
            user_id=ctx.user_id,
            key_hash=hash_token(key),
            role=role,
            expires_at=now + (ttl or self._options.api_key_ttl),
        )
        # A create that issues a secret: the row as stored is not enough on a
        # rerun, because the secret is a digest there and was shown to no one
        # (the marker stored no outcome). The one storage method inserts the
        # key, or re-mints the secret on the row the id already names, and the
        # re-mint lands only while the marker still holds this attempt.
        row = outbox_row(ctx, "tenancy.api_key.created", api_key.id, self._key_payload(api_key))
        stored, created = await self._storage.issue_api_key(
            ctx.org_id, api_key, (row,), attempt.attempt_id if attempt else None
        )
        if created:
            await self._relay.relay(ctx.org_id, row)
        return IssuedApiKey(key=key, api_key=stored)

    async def revoke_api_key(self, ctx: OpContext, api_key_id: UUID) -> ApiKey:
        ctx.require(Permission.MANAGE_KEYS)
        api_key = await self._storage.read_api_key(ctx.org_id, api_key_id)
        if api_key is None or api_key.deleted_at is not None:
            raise NotFound(f"api key {api_key_id} not found")
        if api_key.user_id != ctx.user_id and not ctx.has(Permission.MANAGE_MEMBERS):
            raise NotAuthorized("only the owner of a key or a member manager may revoke it")
        now = utcnow()
        revoked = api_key.model_copy(
            update={
                "deleted_at": now,
                "deleted_by": ctx.user_id,
                "updated_at": now,
                "updated_by": ctx.user_id,
            }
        )
        await self._write_api_key(ctx, revoked, "deleted")
        return revoked

    async def purge_across_tenants(self) -> int:
        batch = self._options.purge_batch
        now = utcnow()
        purged = await self._storage.purge_deleted(
            now - self._options.retention, now - self._options.ticket_retention, batch
        )
        # The system scope also holds the sign-in delays, keyed on emails
        # nobody may hold: a run that ended long ago goes with the logins.
        purged += await self._storage.purge_sign_in_delays(
            now - self._options.sign_in_delay_retention, batch
        )
        return purged

    async def purge_tenant(self, ctx: OpContext) -> int:
        ctx.require(Permission.MANAGE_MEMBERS)
        if not await self.tenant_expired(ctx):
            return 0
        # The tenant itself is past the retention: every row of it goes.
        return await self._storage.purge_tenant(ctx.org_id, self._options.purge_batch)

    async def sweep_context(self, rctx: RequestContext, org_id: UUID) -> OpContext | None:
        swept = self._pass
        if swept is None or rctx.request_id != swept[0] or org_id not in swept[1]:
            org = await self._storage.read_org(org_id)
            if org is None or org.purged_at is not None:
                return None
        return build_context(
            rctx,
            user_id=EMPTY_UUID,
            org_id=org_id,
            role=Role.SERVICE,
            permissions=permissions_of(Role.SERVICE),
            credential_kind=CredentialKind.INTERNAL,
        )

    async def tenant_expired(self, ctx: OpContext) -> bool:
        ctx.require(Permission.READ)
        swept = self._pass
        if swept is not None and ctx.request_id == swept[0] and ctx.org_id in swept[1]:
            return ctx.org_id in swept[2]
        org = await self._storage.read_org(ctx.org_id)
        return org is not None and past_retention(org, utcnow() - self._options.retention)

    async def mark_purged(self, ctx: OpContext) -> bool:
        ctx.require(Permission.MANAGE_MEMBERS)
        if not await self.tenant_expired(ctx):
            return False
        return await self._storage.mark_org_purged(ctx.org_id, utcnow())

    async def issue_ticket(self, ctx: OpContext) -> IssuedTicket:
        ctx.require(Permission.READ)
        if ctx.security.credential_kind not in TICKET_CREDENTIALS:
            raise NotAuthorized("a ticket stands for a session token or an api key")
        ticket = mint_token(CredentialKind.SOCKET_TICKET)
        now = utcnow()
        behind = SocketTicket(
            id=new_id(),
            created_at=now,
            user_id=ctx.user_id,
            ticket_hash=hash_token(ticket),
            credential_kind=ctx.security.credential_kind,
            credential_id=ctx.security.credential_id,
            expires_at=now + self._options.ticket_ttl,
        )
        if not await self._storage.create_socket_ticket(ctx.org_id, behind):
            raise Conflict(f"socket ticket {behind.id} is already written")
        return IssuedTicket(ticket=ticket, expires_at=behind.expires_at)

    # Helpers.

    def _clamp(self, limit: int) -> int:
        return max(1, min(limit, self._options.max_limit))

    # The core row and its outbox row land in one storage call; the relay then
    # appends the event and pushes at once, and the sweep catches what a crash
    # left behind.

    async def _write_user(self, ctx: OpContext, user: User, action: str) -> None:
        row = outbox_row(ctx, f"tenancy.user.{action}", user.id, user_payload(user))
        await self._storage.write_user(ctx.org_id, user, (row,))
        await self._relay.relay(ctx.org_id, row)

    @staticmethod
    def _session_payload(session: Session) -> Mapping[str, Any]:
        # Ids only: the row names the session by its target, and never carries
        # the hash; the event is a record, not a credential.
        return {"user_id": str(session.user_id)}

    def _session_row(self, ctx: OpContext, session: Session, action: str) -> OutboxRow:
        return outbox_row(
            ctx, f"tenancy.session.{action}", session.id, self._session_payload(session)
        )

    async def _create_session(self, org_id: UUID, session: Session) -> None:
        """Lands a new credential's row through the insert that reports. Its id
        is minted with its secret, so an id already written is never a retry
        of this call: the row there keeps another digest, and the secret is
        refused rather than handed out as one that opens nothing."""
        if not await self._storage.create_session(org_id, session):
            raise Conflict(f"session {session.id} is already written")

    async def _write_session(self, ctx: OpContext, session: Session, action: str) -> None:
        row = self._session_row(ctx, session, action)
        await self._storage.write_session(ctx.org_id, session, (row,))
        await self._relay.relay(ctx.org_id, row)

    @staticmethod
    def _key_payload(api_key: ApiKey) -> Mapping[str, Any]:
        # Ids only, and never the hash; the event is a record, not a credential.
        return {"user_id": str(api_key.user_id)}

    async def _write_api_key(self, ctx: OpContext, api_key: ApiKey, action: str) -> None:
        row = outbox_row(ctx, f"tenancy.api_key.{action}", api_key.id, self._key_payload(api_key))
        await self._storage.write_api_key(ctx.org_id, api_key, (row,))
        await self._relay.relay(ctx.org_id, row)

    def _check_session(self, session: Session, kind: CredentialKind) -> None:
        if session.credential_kind is not kind:
            raise InvalidCredential("credential kind does not match its prefix")
        if session.revoked_at is not None:
            # A sign-in ends at its exchange or its sign-out, an operator
            # token at its revoke or its sign-out.
            raise CredentialExpired(REVOKED_MESSAGE.get(kind, "session revoked"))
        now = utcnow()
        if session.expires_at <= now:
            raise CredentialExpired("session expired")
        # The idle lifetime beside the absolute one. A session no request has
        # touched yet (one an older release wrote) starts its idle clock at
        # its first use here, not at its creation.
        seen = session.last_seen_at
        if seen is not None and seen + self._options.session_idle_ttl <= now:
            raise CredentialExpired("session idle")

    @staticmethod
    def _refuse_operator_token(ictx: IdentityContext) -> None:
        """An operator token reaches the operator plane and nothing else: it
        never lists a person's places or enters a tenant."""
        if ictx.credential_kind is CredentialKind.OPERATOR_TOKEN:
            raise InvalidCredential("an operator token reaches the operator plane only")

    @staticmethod
    def _check_api_key(api_key: ApiKey) -> None:
        if api_key.deleted_at is not None:
            raise CredentialExpired("api key revoked")
        if api_key.expires_at <= utcnow():
            raise CredentialExpired("api key expired")

    async def _live_user(self, ctx: OpContext, user_id: UUID) -> User:
        """Existence and tenancy, or NotFound."""
        user = await self._storage.read_user(ctx.org_id, user_id)
        if user is None or user.deleted_at is not None:
            raise NotFound(f"user {user_id} not found")
        return user

    async def _live_membership(self, ctx: OpContext, user_id: UUID) -> Membership:
        """The membership of a live user, or NotFound: a removed member has no
        membership to read or change, whatever the row says."""
        await self._live_user(ctx, user_id)
        membership = await self._storage.read_membership_for_user(ctx.org_id, user_id)
        if membership is None or membership.deleted_at is not None:
            raise NotFound(f"membership of user {user_id} not found")
        return membership

    async def _refuse_personal_owner(self, ctx: OpContext, user_id: UUID, rule: str) -> None:
        """A personal org belongs to its person for good: nobody removes them
        from it or changes their role, so it never changes hands. Anyone else
        in it is an ordinary member."""
        org = await self._storage.read_org(ctx.org_id)
        if org is None or not org.personal:
            return
        user = await self._live_user(ctx, user_id)
        if user.identity_id == org.personal_identity_id:
            raise PersonalOrgFixed(f"a personal org {rule}")

    async def _principal(
        self, org_id: UUID, user_id: UUID, session: Session | None = None
    ) -> tuple[Org, User, Membership]:
        """The org, the user, and the live membership a credential stands
        for, or InvalidCredential. A tenant session presented is recorded as
        used, at most once a `session_seen_every`, for its idle lifetime, in
        the same transaction as the read."""
        seen = None
        if session is not None:
            now = utcnow()
            last = session.last_seen_at
            if last is None or last + self._options.session_seen_every <= now:
                seen = (session.id, now)
        return self._live_principal(*await self._storage.read_principal(org_id, user_id, seen))

    @staticmethod
    def _live_principal(
        org: Org | None, user: User | None, membership: Membership | None
    ) -> tuple[Org, User, Membership]:
        """The principal a read found, when all three are live, or
        InvalidCredential."""
        if org is None or org.deleted_at is not None:
            raise InvalidCredential("the org is gone")
        if user is None or user.deleted_at is not None:
            raise InvalidCredential("the user is gone")
        if membership is None:
            raise InvalidCredential("the membership is gone")
        return org, user, membership

    async def _principal_in(self, org_id: UUID, identity_id: UUID) -> tuple[Org, User, Membership]:
        """The principal a verified identity is in `org_id`, or NotAuthorized: the
        sign-in is good, the tenant is not theirs, so a client keeps its login
        and picks another tenant. A gone org, user, or membership is refused the
        same way; InvalidCredential is for a credential that fails, and a login
        that names a tenant it cannot enter has not failed."""
        users = await users_of(self._storage, identity_id, self._options.max_orgs_per_identity)
        user = next((user for user_org, user in users if user_org == org_id), None)
        if user is None:
            raise NotAuthorized("this identity is not a member of that org")
        org = await self._storage.read_org(org_id)
        if org is None or org.deleted_at is not None:
            raise NotAuthorized("that org is gone")
        membership = await self._storage.read_membership_for_user(org_id, user.id)
        if membership is None:
            raise NotAuthorized("this identity is no longer a member of that org")
        return org, user, membership

    async def _memberships_of(self, identity_id: UUID) -> tuple[OrgMembership, ...]:
        """The places a person holds, each the org, the user, and the role, in
        one read whatever their number. The read asks for one past the most a
        person may hold, so a list cut short is never taken for the whole:
        more than that (two adds that raced past the refusal) is
        `MembershipLimitReached`, as `users_of` answers."""
        most = self._options.max_orgs_per_identity
        found = await self._storage.read_memberships_by_identity(identity_id, most + 1)
        if len(found) > most:
            raise MembershipLimitReached(
                f"identity {identity_id} is a member of more than {most} orgs"
            )
        return tuple(found)
