"""The tenancy swimlane: organizations, identities, users, memberships,
and credentials. Owns the identity model and issues tokens; the gateway
only verifies."""

from abc import ABC, abstractmethod
from datetime import timedelta
from uuid import UUID

from acme.integrations.identity import DeviceAuthorization, PortalIntent
from acme.om.context import (
    CredentialKind,
    IdentityContext,
    OperatorContext,
    OperatorRole,
    RequestContext,
    Role,
    TenantContext,
)
from acme.om.idempotency.types.attempt import Attempt
from acme.om.tenancy.types.api_key import ApiKey
from acme.om.tenancy.types.identity import Identity
from acme.om.tenancy.types.invitation import Invitation
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
from acme.om.tenancy.types.org import Org
from acme.om.tenancy.types.page import (
    ApiKeyPage,
    InvitationPage,
    MembershipPage,
    OrgMembershipPage,
    UserPage,
)
from acme.om.tenancy.types.session import Session
from acme.om.tenancy.types.socket_ticket import SocketPrincipal
from acme.om.tenancy.types.user import User


class TenancyManagerInterface(ABC):
    """Manager of the tenancy swimlane, on the tenant plane: every operation
    on a principal takes `TenantContext`. The operator plane is
    `TenancyOperatorManagerInterface`, which takes `OperatorContext`.

    The transitions come first. Each takes the weakest stage it needs and
    produces a stronger one: `RequestContext` in, `IdentityContext` or
    `TenantContext` out; `IdentityContext` in, `OperatorContext` out. They are the
    only constructors of those stages, and the exceptions test names them.
    """

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
    async def sign_in_url(
        self,
        rctx: RequestContext,
        redirect_uri: str,
        state: str,
        *,
        invitation_token: str | None = None,
        sign_up: bool = False,
    ) -> SignInStart:
        """Platform-internal: where a browser goes to sign in at the identity
        provider, and the PKCE verifier the caller keeps and hands back with
        the code. It comes back to `redirect_uri` with a code and `state` as
        it was handed; the state is the caller's, which binds the round trip
        to the tab that started it. A redirect this environment does not name as
        its own is refused (ValidationFailed): a deployed environment never
        sends a code anywhere else. Unavailable when no provider is
        configured."""
        ...

    @abstractmethod
    async def sign_in_with_code(
        self,
        rctx: RequestContext,
        code: str,
        invitation_token: str | None = None,
        *,
        code_verifier: str | None = None,
    ) -> IssuedLogin:
        """Platform-internal: the identity provider's sign-in, finished. The code
        the browser brought back is exchanged with the provider, server-side,
        with the verifier the sign-in started with, for the person it vouches
        for: an issuer, a subject, and a verified
        email. The identity is found by the issuer and the subject; else by
        the email, and linked to the subject from then on (a person the
        seeding or the operator plane made); else made, with their personal
        org, in one commit: a first sign-in is a sign-up.
        A sign-in that accepted an invitation lands the membership it names,
        and one through an org's single sign-on lands a membership when the
        person's address is in a domain the org verified (`sso_joins`). The
        answer is a login and the places, as every sign-in's.

        SignInRefused for a code the provider will not exchange;
        EmailNotVerified when the provider has not verified the address;
        Unavailable when the provider cannot be reached or is not
        configured."""
        ...

    @abstractmethod
    async def start_device_sign_in(self, rctx: RequestContext) -> DeviceAuthorization:
        """Platform-internal: a sign-in for a device with no browser of its own,
        the command line. The person confirms the code the answer names at the
        provider's address, in any browser; the device keeps the device code
        and finishes with it."""
        ...

    @abstractmethod
    async def finish_device_sign_in(self, rctx: RequestContext, device_code: str) -> IssuedLogin:
        """Platform-internal: asks whether the person confirmed the device
        sign-in. When they did, it is finished as `sign_in_with_code` finishes
        a browser's. SignInPending (or SignInSlowDown) while they have not;
        SignInRefused when they declined or the code expired."""
        ...

    @abstractmethod
    async def dev_sign_in(
        self, rctx: RequestContext, email: str, display_name: str = ""
    ) -> IssuedLogin:
        """Platform-internal, local and test only: a sign-in by address alone,
        for the seed, the demo recorders, the traffic generator, and the tests,
        which need people without a browser round trip. The identity is found
        by the email or made with its personal org, as a first sign-in makes
        one. NotFound unless the process was built with it on, which a deployed
        environment refuses at boot."""
        ...

    @abstractmethod
    async def verify_second_factor(self, ictx: IdentityContext, totp_code: str) -> IssuedLogin:
        """Platform-internal: a sign-in's second factor. The login presented is
        answered with a new one that records the verified code, which the
        operator gate asks for, and ends in the write that lands it, so a
        person holds one sign-in at a time; a wrong code ends nothing. The
        code is checked against the identity's enrolled secret and refused
        when it was used already. A run of wrong codes for the email makes
        the next one wait (SignInDelayed). Only a login credential is taken
        (InvalidCredential otherwise)."""
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
    async def exchange_login(self, ictx: IdentityContext, org_id: UUID) -> IssuedSession:
        """Platform-internal: exchanges the verified identity for a tenant-scoped
        session token; NotAuthorized when the identity is not a member of
        `org_id`, and the credential presented still stands. A sign-in is
        exchanged once: it ends in the same write that lands the session, so
        one sign-in makes one session. A second exchange of it, a retry after
        a lost answer among them, is refused with CredentialExpired, and the
        person signs in again; of two exchanges at once, one lands. An
        identity proven by a session is a switch: that session
        ends in the same write that lands the new one, announced as any
        revocation is, so its socket closes and a tab never holds two live
        sessions. A session another write already ended is refused with
        CredentialExpired and nothing lands. An operator token never enters a
        tenant (InvalidCredential)."""
        ...

    @abstractmethod
    async def get_identity_memberships(
        self, ictx: IdentityContext, after: UUID | None, limit: int
    ) -> OrgMembershipPage:
        """Platform-internal: no tenant is chosen, so the list is the identity's.
        The places the verified identity holds, each its org, its user, and its
        role, the same choice a sign-in answers with; by user id, a page at a
        time as `get_users` pages. Deleted orgs and ended memberships are not
        listed. An operator token lists nothing (InvalidCredential)."""
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

    # The principal.

    @abstractmethod
    async def get_org(self, ctx: TenantContext) -> Org: ...

    @abstractmethod
    async def get_me(self, ctx: TenantContext) -> OrgMembership:
        """The caller's org, their user in it, and their membership's role,
        read together in one transaction. The context carries ids, so what
        the caller is shown is loaded here, as fresh as the request."""
        ...

    @abstractmethod
    async def create_org(
        self, ctx: TenantContext, name: str, slug: str | None, attempt: Attempt | None = None
    ) -> OrgMembership:
        """A team org the caller makes and owns: the org, the caller's user in
        it under the display name they carry in this one, and the owner
        membership, in one commit. The slug is generated from the name when
        `slug` is None; a taken one is Conflict. Only a session makes an org
        (NotAuthorized for an api key), since a new tenant is a person's and
        not a program's; a person already in as many orgs as they may join is
        MembershipLimitReached. The caller's session stays in its tenant: the
        switch into the new one is the exchange. `attempt` as on
        `create_api_key`: the org is created on its id, and a rerun finds the
        org written and answers with the caller's place in it."""
        ...

    @abstractmethod
    async def get_identity(self, ctx: TenantContext) -> Identity:
        """The identity behind the caller's user."""
        ...

    @abstractmethod
    async def set_time_zone(self, ctx: TenantContext, time_zone: str) -> Identity:
        """Records where the caller is, as an IANA name, on their identity,
        so it holds in every org they are in. A name that is not one
        (`tenancy.rules.check_time_zone`) is ValidationFailed."""
        ...

    @abstractmethod
    async def invite_member(
        self, ctx: TenantContext, email: str, role: Role, attempt: Attempt | None = None
    ) -> Invitation:
        """Asks a person to join the org, by email, with a role capped at the
        caller's (NotAuthorized above it). The identity provider sends the
        email with the sign-in link; the org's organization there is made the
        first time. A person who is a member already is Conflict, and so is an
        address with an open invitation (send that one again instead); an
        expired one is replaced. This is the one door into an org for a person
        of a deployed environment, so a limit on an org's members is checked
        here, before anything is sent. `attempt` as on `create_api_key`: the
        invitation is created on its id, and a rerun finds it."""
        ...

    @abstractmethod
    async def get_invitations(
        self, ctx: TenantContext, after: UUID | None, limit: int
    ) -> InvitationPage:
        """The org's pending invitations, newest first, a page at a time, for a
        member manager."""
        ...

    @abstractmethod
    async def resend_invitation(self, ctx: TenantContext, invitation_id: UUID) -> Invitation:
        """Sends a pending invitation's email again, with a fresh expiry.
        InvitationClosed for one accepted or revoked."""
        ...

    @abstractmethod
    async def revoke_invitation(self, ctx: TenantContext, invitation_id: UUID) -> Invitation:
        """Revokes a pending invitation: its link stops working.
        InvitationClosed for one accepted or revoked."""
        ...

    @abstractmethod
    async def sso_setup_link(
        self, ctx: TenantContext, intent: PortalIntent, return_url: str
    ) -> str:
        """A short-lived link to the identity provider's admin portal, where
        an owner or an admin of a team org sets up the org's single sign-on
        (`sso`) or proves its domain (`domain_verification`) themselves. A
        personal org has no single sign-on (ValidationFailed), and only a
        member manager opens it. The org's organization at the provider is
        made the first time."""
        ...

    @abstractmethod
    async def rename_user(self, ctx: TenantContext, user_id: UUID, display_name: str) -> User:
        """Sets a user's display name in this org: the caller's own, or
        another member's with `manage_members`. Email and identity belong to
        the identity. A blank name is ValidationFailed."""
        ...

    @abstractmethod
    async def get_users(self, ctx: TenantContext, after: UUID | None, limit: int) -> UserPage:
        """The tenant's members, by id, a page at a time: `after` is the id the
        previous page ended on, and `has_more` says another follows."""
        ...

    @abstractmethod
    async def get_user(self, ctx: TenantContext, user_id: UUID) -> User: ...

    # Memberships.

    @abstractmethod
    async def get_memberships(
        self, ctx: TenantContext, after: UUID | None, limit: int
    ) -> MembershipPage:
        """The tenant's memberships, by user id, a page at a time as `get_users`
        pages: a page ending on the same user id covers the same members, so
        the two lists pair page for page."""
        ...

    @abstractmethod
    async def update_membership_role(
        self, ctx: TenantContext, user_id: UUID, role: Role
    ) -> Membership:
        """Role-capped at the caller's role, for the target's old role and its
        new one. The person of a personal org keeps their role in it
        (PersonalOrgFixed)."""
        ...

    @abstractmethod
    async def remove_member(self, ctx: TenantContext, user_id: UUID) -> User:
        """Soft-deletes the member's user in this org, ends their membership,
        and revokes every live session and api key of theirs, in one
        transaction; no list shows them, no role change reaches them, and
        each revocation is announced, so their sockets close. The person of a
        personal org is never removed from it (PersonalOrgFixed)."""
        ...

    @abstractmethod
    async def delete_account(
        self, ctx: TenantContext, confirm_email: str, return_to: str | None = None
    ) -> AccountDeleted:
        """The caller's whole account, gone for good, from a session only
        (NotAuthorized for an api key, which is a program's). `confirm_email`
        is the account's email as the person typed it (ValidationFailed when
        it is not). Refused while the person is on the operator allowlist
        (OperatorRoleHeld), and while they are the last owner of a team org
        (LastOwner, naming each one).

        One commit erases the person (`TenancyStorageInterface.delete_person`):
        the identity, their user and membership in every org, every
        credential they hold, each live one announced as revoked, and the
        sign-in delay of their address. What they made in a team org stays
        the org's, under an id that no longer names anyone. The same commit
        asks for the rest: in their personal org, the provider's side goes
        and then the org itself (`DELETE_ACCOUNT`). The answer says where the browser goes to
        end the provider's session, as `logout` does with `return_to`."""
        ...

    @abstractmethod
    async def delete_personal_org(self, ctx: TenantContext) -> Org | None:
        """Platform-internal, the last step of `DELETE_ACCOUNT`: deletes the
        caller's tenant when it is a personal org whose person is gone, and
        announces it, so its sockets close. A deleted personal org keeps no
        retention, so the sweep purges every row of it at its next pass
        (`tenancy.rules.past_retention`). None, and nothing written, when it
        is deleted already; PersonalOrgFixed for any other org."""
        ...

    @abstractmethod
    async def delete_org(self, ctx: TenantContext, confirm_name: str) -> OrgDeleted:
        """The caller's team org, deleted by its owner, from a session only
        (NotAuthorized for an api key, which is a program's, and for any role
        but owner). `confirm_name` is the org's name as the owner typed it
        (ValidationFailed when it is not). A personal org is refused
        (PersonalOrgFixed): it goes only with its person's account.

        One commit closes the org (`TenancyStorageInterface.write_closed_org`): every
        member's user and membership end, every session and api key in it is
        revoked, each announced, so every socket closes, every pending
        invitation is revoked, and the org lets go of its organization at the
        identity provider. The same commit asks for the rest (`DELETE_ORG`):
        the provider's organization goes, then the org is deleted, and the
        sweep purges it after the retention. An operator's deletion
        (`TenancyOperatorManagerInterface.delete_org`) takes the same path.
        The answer carries a session in the owner's personal org, which the
        tab takes up, as a switch does."""
        ...

    @abstractmethod
    async def delete_closed_org(self, ctx: TenantContext) -> Org | None:
        """Platform-internal, the last step of `DELETE_ORG`, on the service
        role only (NotAuthorized otherwise): soft-deletes the caller's closed
        team org, whoever closed it, and announces it, so its sockets close.
        The sweep purges it once the retention has passed. None, and nothing
        written, when it is deleted already; PersonalOrgFixed for a personal
        org."""
        ...

    @abstractmethod
    async def count_members(self, ctx: TenantContext) -> int:
        """How many live members the org has."""
        ...

    # Credentials.

    @abstractmethod
    async def get_sessions(self, ctx: TenantContext, limit: int) -> list[Session]:
        """The caller's own live sessions in this org, newest first."""
        ...

    @abstractmethod
    async def revoke_session(self, ctx: TenantContext, session_id: UUID) -> Session: ...

    @abstractmethod
    async def logout(self, ictx: IdentityContext, return_to: str | None = None) -> SignedOut:
        """Platform-internal: ending its own sign-in is an operation of the
        identity stage. Ends the credential the stage came from, whichever
        it is: a session, announced so the socket it opened closes; a sign-in,
        with or without its second factor; an operator token, which is the
        operator's sign-out. It answers where the browser goes to end the
        identity provider's session behind it, when the sign-in left one
        there. `return_to` is where the provider sends the browser after: one
        of `sign_out_return_uris`, else refused; None leaves it to the
        provider's default. An api key has no sign-out: it is never an
        identity stage, and its holder revokes it."""
        ...

    @abstractmethod
    async def get_api_keys(self, ctx: TenantContext, after: UUID | None, limit: int) -> ApiKeyPage:
        """The tenant's unrevoked keys for a member manager, the caller's own
        otherwise; newest first, a page at a time, as `get_users` pages."""
        ...

    @abstractmethod
    async def create_api_key(
        self,
        ctx: TenantContext,
        name: str,
        role: Role,
        ttl: timedelta | None = None,
        attempt: Attempt | None = None,
    ) -> IssuedApiKey:
        """Role-capped at the caller's role; the service role is refused by name.
        `attempt`, when given, is the attempt a retried request runs under: the
        id it creates on and the token of the idempotency marker holding it. A
        key that already exists under that id is the rerun of a create that
        issues a secret: the secret is re-minted on that row in the same write
        and a fresh `IssuedApiKey` with the same id comes back, since the first
        secret reached no one; the old secret stops authenticating. The re-mint
        lands only while the marker still holds the token, so an attempt that
        lost the marker to a retry cannot invalidate the key that retry already
        returned. Without an attempt the id is fresh and there is no rerun."""
        ...

    @abstractmethod
    async def revoke_api_key(self, ctx: TenantContext, api_key_id: UUID) -> ApiKey: ...

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
