import logging
from datetime import timedelta
from uuid import UUID

from pydantic import Field

from acme.infra.cache import CacheInterface
from acme.om.base import EMPTY_UUID, Platform, new_id, utcnow
from acme.om.context import (
    CredentialKind,
    IdentityContext,
    OperatorContext,
    OperatorPermission,
    OperatorRole,
    Permission,
    RequestContext,
    Role,
    TenantContext,
    build_context,
)
from acme.om.exceptions import (
    Conflict,
    CredentialExpired,
    InvalidCredential,
    NotAnOperator,
    NotAuthorized,
    NotFound,
    SecondFactorRequired,
    ValidationFailed,
)
from acme.om.outbox import OutboxRelayInterface
from acme.om.outbox.types.row import OutboxRow
from acme.om.tenancy.credentials import TenancyCredentialsManagerInterface
from acme.om.tenancy.impl.creates import (
    MAX_ORGS_PER_IDENTITY,
    add_member_to,
    create_org_with_owner,
    new_identity,
)
from acme.om.tenancy.impl.shared import (
    check_session,
    create_session,
    mint_token,
    new_operator_token,
    principal_in,
)
from acme.om.tenancy.manager import TenancyManagerInterface
from acme.om.tenancy.members import TenancyMembersManagerInterface
from acme.om.tenancy.org import TenancyOrgManagerInterface
from acme.om.tenancy.rules import (
    MAX_API_KEY_TTL,
    MAX_OPERATOR_TOKEN_TTL,
    capped_role,
    credential_kind_of,
    email_digest,
    hash_token,
    is_platform_email,
    operator_permissions_of,
    past_retention,
    permissions_of,
    role_at_most,
)
from acme.om.tenancy.sign_in import TenancySignInManagerInterface
from acme.om.tenancy.storage import TenancyStorageInterface
from acme.om.tenancy.types.api_key import ApiKey
from acme.om.tenancy.types.identity import Identity
from acme.om.tenancy.types.issued import IssuedOperatorToken, IssuedTicket
from acme.om.tenancy.types.membership import Membership
from acme.om.tenancy.types.org import Org
from acme.om.tenancy.types.session import Session
from acme.om.tenancy.types.socket_ticket import SocketPrincipal, SocketTicket
from acme.om.tenancy.types.user import User

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


class TenancyOptions(Platform):
    """Tunables, built once at boot; the manager never reads the environment."""

    login_ttl: timedelta = timedelta(minutes=10)
    session_ttl: timedelta = timedelta(days=30)
    """A session's absolute lifetime, from the exchange of its sign-in. A
    session made from another, by a switch, keeps that one's deadline."""
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


class TenancyManagerImpl(TenancyManagerInterface):
    def __init__(
        self,
        storage: TenancyStorageInterface,
        relay: OutboxRelayInterface,
        cache: CacheInterface,
        options: TenancyOptions,
        *,
        sign_in: TenancySignInManagerInterface,
        org: TenancyOrgManagerInterface,
        members: TenancyMembersManagerInterface,
        credentials: TenancyCredentialsManagerInterface,
    ) -> None:
        self._storage = storage
        self._relay = relay
        self._cache = cache
        self._options = options
        # The delegates: the root builds each one and hands it over, and
        # nothing sets one again.
        self.sign_in = sign_in
        self.org = org
        self.members = members
        self.credentials = credentials
        # The last sweep pass's answer to `tenant_expired` and `sweep_context`,
        # read with the org rows `service_contexts` pages through: its request
        # id, the tenants it minted a context for and has not marked purged
        # since, and those of them past the retention.
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
    ) -> tuple[TenantContext, Org]:
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
    ) -> tuple[TenantContext, User, bool]:
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
        check_session(proof, kind, self._options.session_idle_ttl)
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

    async def authenticate(self, rctx: RequestContext, credential: str) -> TenantContext:
        kind = credential_kind_of(credential)
        if kind is CredentialKind.SESSION_TOKEN:
            found = await self._storage.read_session_by_digest(hash_token(credential))
            if found is None:
                raise InvalidCredential("unknown session token")
            org_id, session = found
            check_session(session, CredentialKind.SESSION_TOKEN, self._options.session_idle_ttl)
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
            org, user, membership = await self._principal(org_id, api_key.user_id)
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
        await create_session(self._storage, EMPTY_UUID, session)
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
        if credential_kind is CredentialKind.SESSION_TOKEN:
            session = await self._storage.read_session(org_id, credential_id)
            if session is None:
                raise InvalidCredential("the session behind the ticket is gone")
            check_session(session, CredentialKind.SESSION_TOKEN, self._options.session_idle_ttl)
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
            org, user, membership = await self._principal(org_id, api_key.user_id)
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

    async def service_context(
        self, rctx: RequestContext, org_id: UUID, user_id: UUID
    ) -> TenantContext:
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
    ) -> TenantContext | None:
        # The digest folds the address, so every spelling finds one person.
        identity = await self._storage.read_identity_by_email_digest(email_digest(email))
        # The provider links a person once it has verified their address; a
        # person it never linked holds an address someone typed for them.
        if identity is None or identity.subject is None:
            return None
        try:
            org, user, membership = await principal_in(
                self._storage, org_id, identity.id, self._options.max_orgs_per_identity
            )
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

    async def service_contexts(self, rctx: RequestContext) -> list[TenantContext]:
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

    async def sweep_context(self, rctx: RequestContext, org_id: UUID) -> TenantContext | None:
        # The pass that listed the tenants knows each one it minted a context
        # for. Any other request stage, or a tenant made since, reads its org
        # row: deleted, it still has one, since its rows are the sweep's to
        # settle; marked purged, or never made, it has none. The system scope
        # has no org row and is never purged.
        swept = self._pass
        known = swept is not None and rctx.request_id == swept[0] and org_id in swept[1]
        if not known and org_id != EMPTY_UUID:
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

    async def purge_tenant(self, ctx: TenantContext) -> int:
        ctx.require(Permission.MANAGE_MEMBERS)
        if not await self.tenant_expired(ctx):
            return 0
        # The tenant itself is past the retention: every row of it goes.
        return await self._storage.purge_tenant(ctx.org_id, self._options.purge_batch)

    async def tenant_expired(self, ctx: TenantContext) -> bool:
        ctx.require(Permission.READ)
        swept = self._pass
        if swept is not None and ctx.request_id == swept[0] and ctx.org_id in swept[1]:
            return ctx.org_id in swept[2]
        org = await self._storage.read_org(ctx.org_id)
        return org is not None and past_retention(org, utcnow() - self._options.retention)

    async def mark_purged(self, ctx: TenantContext) -> bool:
        ctx.require(Permission.MANAGE_MEMBERS)
        if not await self.tenant_expired(ctx):
            return False
        marked = await self._storage.mark_org_purged(ctx.org_id, utcnow())
        swept = self._pass
        if swept is not None and ctx.request_id == swept[0]:
            # Purged, now or by another pass: the rest of this one has no
            # context for the tenant either (`sweep_context`).
            gone = frozenset({ctx.org_id})
            self._pass = (swept[0], swept[1] - gone, swept[2] - gone)
        return marked

    async def issue_ticket(self, ctx: TenantContext) -> IssuedTicket:
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

    @staticmethod
    def _check_api_key(api_key: ApiKey) -> None:
        if api_key.deleted_at is not None:
            raise CredentialExpired("api key revoked")
        if api_key.expires_at <= utcnow():
            raise CredentialExpired("api key expired")

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
