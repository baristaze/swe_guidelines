"""The tenancy swimlane: organizations, identities, users, memberships,
and credentials. Owns the identity model and issues tokens; the gateway
only verifies."""

from abc import ABC, abstractmethod
from datetime import timedelta
from uuid import UUID

from acme.om.context import (
    CredentialKind,
    IdentityContext,
    OperatorContext,
    OperatorRole,
    RequestContext,
    Role,
    TenantContext,
)
from acme.om.tenancy.credentials import TenancyCredentialsManagerInterface
from acme.om.tenancy.members import TenancyMembersManagerInterface
from acme.om.tenancy.org import TenancyOrgManagerInterface
from acme.om.tenancy.sign_in import TenancySignInManagerInterface
from acme.om.tenancy.types.identity import Identity
from acme.om.tenancy.types.issued import IssuedOperatorToken, IssuedTicket
from acme.om.tenancy.types.org import Org
from acme.om.tenancy.types.socket_ticket import SocketPrincipal
from acme.om.tenancy.types.user import User


class TenancyManagerInterface(ABC):
    """Manager of the tenancy swimlane, on the tenant plane: every operation
    on a principal takes `TenantContext`. The operator plane is
    `TenancyOperatorManagerInterface`, which takes `OperatorContext`.

    The manager keeps what every process calls: the seeding, the transitions
    with the socket tickets, the grant job, and the sweep. Its other duties
    are delegates, each an interface of its own that the root builds and a
    caller outside the namespace reaches through the manager, as
    `tenancy.credentials.create_api_key`.

    The transitions come first. Each takes the weakest stage it needs and
    produces a stronger one: `RequestContext` in, `IdentityContext` or
    `TenantContext` out; `IdentityContext` in, `OperatorContext` out. They are the
    only constructors of those stages, and the exceptions test names them.
    """

    sign_in: TenancySignInManagerInterface
    """Signing in, the exchange for a session, and signing out."""
    org: TenancyOrgManagerInterface
    """The caller's org and account, and their deletion."""
    members: TenancyMembersManagerInterface
    """The org's members, its invitations, and its single sign-on."""
    credentials: TenancyCredentialsManagerInterface
    """The caller's sessions and the org's API keys."""

    @abstractmethod
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
        """Platform-internal: seeds a fresh environment with one team org and its
        owner; an owner nobody has seen before is made with their personal org.

        Produces the owner's context once the identity, org, user, and
        membership exist; the rest of the seeding runs under it. Returns
        that context beside the org. `operator_role` puts the owner's
        identity on the operator allowlist with that role, or widens the
        entry it has; it never narrows one. The owner holds no credential:
        they sign in through the identity provider, or locally through the
        local sign-in, with the address.
        """
        ...

    @abstractmethod
    async def add_member(
        self,
        rctx: RequestContext,
        slug: str,
        email: str,
        display_name: str,
        role: Role,
    ) -> tuple[TenantContext, User, bool]:
        """Platform-internal: seeds a person into an existing org, for local and
        test environments; a person joins a deployed one by invitation.

        Produces the context of the org's creator first, and the rest runs
        under it: the identity is created with its personal org if the email
        is new, then the user and the membership, with the role capped at the
        creator's and the write recorded as theirs. A person who is already a
        member is left as is. Returns that context beside the user and whether
        it was created.
        """
        ...

    @abstractmethod
    async def authenticate_login(self, rctx: RequestContext, credential: str) -> IdentityContext:
        """Platform-internal: the transition to the identity stage. Verifies the
        person's own sign-in and produces the identity behind it: the login
        credential (`lgn_`), a live session token (`ses_`), which proves the
        identity of its user as well as the tenant, so a signed-in app lists
        its memberships and switches with the one bearer it holds, or an
        operator token (`opr_`), which carries its one permission to the
        operator gate and reaches nothing else. A revoked or expired session,
        one idle past its idle lifetime, or one whose user, membership, or org
        is gone, is refused; an api key is refused with InvalidCredential,
        since it is an agent's and not the person's sign-in. Every operation on
        an identity starts here."""
        ...

    @abstractmethod
    async def authenticate(self, rctx: RequestContext, credential: str) -> TenantContext:
        """Platform-internal: the transition to the tenant stage. The gateway asks
        for the principal behind a session token or an api key."""
        ...

    @abstractmethod
    async def admit_operator(self, ictx: IdentityContext) -> OperatorContext:
        """Platform-internal: the transition to the operator stage. Admits the
        verified identity when it is on the operator allowlist, NotAnOperator
        otherwise. Two credentials admit, and a tenant's never does (a tenant
        session is InvalidCredential): the person's own sign-in, and an
        operator token. A sign-in that verified a TOTP code is admitted with
        `OperatorPermission.MINT` alone: it mints one operator token and does
        nothing else on the plane (SecondFactorRequired when an enrolled
        operator's sign-in verified none); an operator with no second factor
        enrolled yet is admitted with `OperatorPermission.ENROL` alone. An
        operator token admits with its one permission, never wider than the
        entry grants today: the one exception to "a sign-in alone never
        admits", since a second factor or the grant job stood behind it. So
        every read and write on the plane is a token's, and each token is
        listed and ended by itself."""
        ...

    @abstractmethod
    async def grant_operator(
        self, rctx: RequestContext, email: str, operator_role: OperatorRole
    ) -> Identity:
        """Platform-internal: the grant job's, on a deployed database as on a
        local one. Puts the identity that holds the email on the operator
        allowlist with that entry, audited under the system scope. No identity
        holding the email is NotFound, since an operator signs in like any
        person first; the platform's own identities (the provisioner and the
        smoke identity, in the platform's reserved domain) are made here the
        first time, with no org and no way to sign in. A rerun with
        the same arguments changes nothing. It enrols no second factor: the
        operator does that at the first sign-in to the plane."""
        ...

    @abstractmethod
    async def disable_operator(self, rctx: RequestContext, email: str) -> Identity:
        """Platform-internal: the grant job's. Takes the identity off the
        allowlist, audited, and ends every operator token and every sign-in
        with a second factor it holds, in the same commit: they stop at once,
        and a grant made again later revives none of them."""
        ...

    @abstractmethod
    async def grant_operator_token(
        self,
        rctx: RequestContext,
        email: str,
        expires_in: timedelta | None = None,
        operator_role: OperatorRole | None = None,
    ) -> IssuedOperatorToken:
        """Platform-internal: the grant job mints the operator token of an
        agent's identity, the provisioner's or the smoke identity's, carrying
        one permission, `operator_role` or else the entry's, never wider than
        the entry (NotAuthorized), and expiring within the hour (3600 seconds
        when `expires_in` is None; longer is ValidationFailed). An identity
        off the allowlist is NotAnOperator. The token is shown once and
        stored as its digest."""
        ...

    @abstractmethod
    async def resume(
        self,
        rctx: RequestContext,
        org_id: UUID,
        credential_kind: CredentialKind,
        credential_id: UUID,
        *,
        record_use: bool = True,
    ) -> SocketPrincipal:
        """Platform-internal: re-checks the credential behind a redeemed socket
        ticket and yields the socket's context with the credential's expiry,
        the bound on the socket's authority. The redemption calls it once,
        and an open socket calls it again on an interval. That recheck
        passes `record_use=False`: it asks whether the credential still
        holds, and a question is not a use, so an open socket never keeps an
        idle session alive."""
        ...

    @abstractmethod
    async def redeem_ticket(self, rctx: RequestContext, ticket: str) -> SocketPrincipal:
        """Platform-internal: consumes a socket ticket exactly once and re-checks the
        credential behind it. The gateway holds a ticket, not a principal; what
        it gets back is the principal and the instant its authority ends."""
        ...

    @abstractmethod
    async def service_context(
        self, rctx: RequestContext, org_id: UUID, user_id: UUID
    ) -> TenantContext:
        """Platform-internal: the context a claimed work item runs under. Minted
        for the tenant on the service role, with `user_id` kept as the
        attribution: the person authorized the work once, at enqueue, so only
        the org must be live, not their user or membership; a member who has
        left does not stop the work they asked for. A deleted org is refused
        with InvalidCredential."""
        ...

    @abstractmethod
    async def member_context(
        self, rctx: RequestContext, org_id: UUID, email: str
    ) -> TenantContext | None:
        """Platform-internal: the context an integration's call acts under when
        it acts for a person. The live member of `org_id` whose identity holds
        `email`, found by the address's digest, with their own role, its
        permissions, and their teams, on the credential kind `INTERNAL`.
        Only a proven address counts: one the identity provider verified when
        the person signed in through it. An address the seeding, the operator
        plane, or the local sign-in typed proves nobody until then.

        None when no identity holds the address, when nobody has proven it,
        and when its person is not a live member of the org: the integration
        acts as nobody. The caller is an integration's handler alone, with
        `org_id` from the integration its token found and `email` one its
        provider vouches is the acting person's own: verified and carried as
        the actor's in the payload it signed, or read from the provider by the
        actor id that payload carries. An address the actor typed at the
        provider, such as a message's sender, is never one, signed or not."""
        ...

    @abstractmethod
    async def service_contexts(self, rctx: RequestContext) -> list[TenantContext]:
        """Platform-internal: one service context per tenant, deleted ones
        included, for sweeps, with one for the system scope (`EMPTY_UUID` as the
        org) first, since login credentials live there and a sweep that never
        visits it lets them pile up. Minted for the tenant, not for a member: it
        carries the tenant, the service role, and the system user (`EMPTY_UUID`)
        as its user id, so a tenant whose members have all left, or that was
        deleted, is still swept; a sweep that skipped a deleted tenant would
        leave its rows and its claimed work forever. The one tenant left out
        is one marked purged (`mark_purged`): nothing of it is left to sweep.
        The contexts share the request stage `rctx`, and `tenant_expired`
        answers for them from the org rows this call read, once per tenant
        per pass."""
        ...

    @abstractmethod
    async def purge_across_tenants(self) -> int:
        """Platform-internal: the sweep, across tenants, once a pass, in one
        transaction: hard-deletes removed members (and their memberships),
        revoked or expired api keys, revoked or expired sessions (the
        sign-ins of the system scope among them), redeemed or expired socket
        tickets, closed invitations past the retention period, and the
        sign-in delays whose run ended long ago; a batch of each kind at
        most; returns how many rows went. It takes no context, because it
        runs for no tenant and no principal. Erasing a person is this purge;
        personal data lives in named fields (`email`, `display_name`), which
        no event about a user carries."""
        ...

    @abstractmethod
    async def purge_tenant(self, ctx: TenantContext) -> int:
        """The sweep, for one tenant deleted longer ago than the retention: every
        user, membership, api key, session, socket ticket, and invitation of
        the tenant goes, a batch of each at most a call, and the org row
        stays as the record; returns how many rows went. Any other tenant
        returns 0 and reads nothing: its rows past the retention go across
        tenants."""
        ...

    @abstractmethod
    async def tenant_expired(self, ctx: TenantContext) -> bool:
        """Platform-internal: True when the tenant's org row is deleted longer ago
        than the retention. Every namespace's sweep asks it before its own
        purge, so one answer decides for the whole system: a tenant past it
        keeps its org row as the record and no row of any other kind. Under a
        context `service_contexts` minted, the answer is the one it read with
        the org rows, so a pass reads it once per tenant and not once per
        namespace."""
        ...

    @abstractmethod
    async def mark_purged(self, ctx: TenantContext) -> bool:
        """Platform-internal, for the sweep, after a pass found nothing of the
        tenant left to trim: stamps the org `purged_at` when the tenant is past
        its retention, and the sweep leaves it out from then on; its org row
        stays as the record. False, with nothing written, for any other
        tenant: a live one always has something to trim later."""
        ...

    @abstractmethod
    async def issue_ticket(self, ctx: TenantContext) -> IssuedTicket:
        """A single-use, short-lived ticket standing for the caller's credential."""
        ...
