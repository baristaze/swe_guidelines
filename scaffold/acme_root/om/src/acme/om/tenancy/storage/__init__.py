"""Storage of the tenancy swimlane. Every operation takes `org_id` first
except the ones documented below, which are global by nature or cross
tenants on purpose; the exceptions test enumerates them."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from acme.om.context import Role
from acme.om.outbox.types.row import OutboxRow
from acme.om.tenancy.types.api_key import ApiKey
from acme.om.tenancy.types.identity import Identity
from acme.om.tenancy.types.invitation import Invitation
from acme.om.tenancy.types.issued import OrgMembership
from acme.om.tenancy.types.membership import Membership
from acme.om.tenancy.types.org import Org
from acme.om.tenancy.types.session import Session
from acme.om.tenancy.types.sign_in_delay import SignInDelay
from acme.om.tenancy.types.size import PlatformSize
from acme.om.tenancy.types.socket_ticket import SocketTicket
from acme.om.tenancy.types.user import User


class TenancyStorageInterface(ABC):
    # Identities are global: a person exists before and across tenants.
    @abstractmethod
    async def read_identity(self, identity_id: UUID) -> Identity | None:
        """Global table: identities have no tenant."""
        ...

    @abstractmethod
    async def read_identity_by_email_digest(self, email_digest: str) -> Identity | None:
        """Global table: identities have no tenant. The sign-in lookup: it runs
        before any identity is known, so it takes the system scope, on the
        system login."""
        ...

    @abstractmethod
    async def read_identity_by_issuer_subject(self, issuer: str, subject: str) -> Identity | None:
        """Global table: identities have no tenant. The identity provider's
        sign-in lookup, by the issuer and the subject the provider vouched
        for: it runs before any identity is known, so it takes the system
        scope, on the system login."""
        ...

    @abstractmethod
    async def write_identity(
        self, identity: Identity, outbox_rows: tuple[OutboxRow, ...] = ()
    ) -> None:
        """Global table: identities have no tenant. The outbox rows, when
        given, land in the same commit under the system scope: the audit of a
        write no tenant holds, a change of the operator allowlist. A subject
        another identity holds under the same issuer, or an email another
        holds, is UniqueKeyTaken."""
        ...

    @abstractmethod
    async def disable_operator(
        self, identity: Identity, outbox_rows: tuple[OutboxRow, ...], at: datetime
    ) -> int:
        """Global table: identities have no tenant. A named atomic write that
        takes an operator off the allowlist, in one commit under the system
        scope: the identity as given (its entry cleared), its audit rows, and
        the end, at `at`, of every operator credential it still holds live,
        its operator tokens and its sign-ins that verified a second factor.
        So a grant made again later revives none of them. Answers how many
        credentials it ended."""
        ...

    @abstractmethod
    async def write_totp_secret(self, identity_id: UUID, sealed: str, at: datetime) -> bool:
        """Global table: identities have no tenant. Puts a new sealed TOTP
        secret on the identity, replacing one that was never confirmed, in one
        conditional statement; False, and nothing written, once a secret is
        confirmed."""
        ...

    @abstractmethod
    async def write_time_zone(self, identity_id: UUID, time_zone: str, at: datetime) -> bool:
        """Global table: identities have no tenant. Sets the person's time
        zone in one statement, and only that column; False when there is no
        such identity."""
        ...

    @abstractmethod
    async def confirm_totp(self, identity_id: UUID, step: int, at: datetime) -> bool:
        """Global table: identities have no tenant. Confirms the secret the
        identity holds and records `step` as the last one used, in one
        conditional statement; False when there is no secret or it is
        confirmed already."""
        ...

    @abstractmethod
    async def use_totp_step(self, identity_id: UUID, step: int) -> bool:
        """Global table: identities have no tenant. Records `step` as the last
        one a code was accepted for, only when it is later than the last one,
        in one conditional statement; False for a step already used (or
        older), so of two sign-ins presenting one code only one passes."""
        ...

    # The sign-in delay, keyed on the email's digest, is a system row.
    @abstractmethod
    async def read_sign_in_delay(self, email_digest: str) -> SignInDelay | None:
        """Global table: a delay belongs to an email, known or not."""
        ...

    @abstractmethod
    async def record_failed_sign_in(self, email_digest: str, at: datetime) -> None:
        """Global table: a delay belongs to an email, known or not. One more
        failed sign-in in the email's run, counted in the statement itself, so
        guesses made at once are each counted."""
        ...

    @abstractmethod
    async def clear_failed_sign_ins(self, email_digest: str) -> None:
        """Global table: a delay belongs to an email, known or not. Ends the
        run on a sign-in that succeeded."""
        ...

    @abstractmethod
    async def purge_sign_in_delays(self, before: datetime, limit: int) -> int:
        """Global table: a delay belongs to an email, known or not. The sweep
        deletes at most `limit` runs whose last failure is older than
        `before`; returns how many went."""
        ...

    # Orgs are the tenants; their own id is their org_id.
    @abstractmethod
    async def read_org(self, org_id: UUID) -> Org | None: ...

    @abstractmethod
    async def read_org_by_slug(self, slug: str) -> Org | None:
        """Cross-tenant lookup: the slug is resolved before a tenant is known.
        The living org with that slug; a deleted org has given it up."""
        ...

    @abstractmethod
    async def count_orgs(self) -> int:
        """Global: how many orgs are live (not soft-deleted), the tenant count
        of the sweep's tally of the platform's size."""
        ...

    @abstractmethod
    async def count_orgs_and_users(self) -> tuple[int, int]:
        """Global, one statement: the live orgs, as `count_orgs` counts them,
        and the live users across every tenant, the two counts of the sweep's
        tally of the platform's size; a person in two orgs counts twice."""
        ...

    @abstractmethod
    async def write_platform_size(self, size: PlatformSize) -> None:
        """Global: the sweep's count of the platform's size becomes the one
        tally row, in the `admin` role. A count older than the row's never
        replaces it, so two workers counting at once leave the newer."""
        ...

    @abstractmethod
    async def read_platform_size(self) -> PlatformSize | None:
        """Global: the platform's size as the sweep last counted it, the one
        row read by its key; None until the first count."""
        ...

    @abstractmethod
    async def read_orgs(self, limit: int, after_id: UUID | None = None) -> list[Org]:
        """Cross-tenant sweep: every tenant, for the operator plane and for sweeps,
        in id order; `after_id` pages, so a sweep reaches every tenant and not
        only the first clamp of them."""
        ...

    @abstractmethod
    async def mark_org_purged(self, org_id: UUID, purged_at: datetime) -> bool:
        """Stamps `purged_at` on a deleted org that carries none; the sweep's
        own write, so nothing announces it. False when the org is not deleted,
        is already stamped, or is unknown."""
        ...

    @abstractmethod
    async def write_org(
        self, org_id: UUID, org: Org, outbox_rows: tuple[OutboxRow, ...] = ()
    ) -> None:
        """Lands the row and the outbox rows that announce it together: the
        deletion of an org is announced the way any change is, so the tenant's
        sockets hear of it."""
        ...

    @abstractmethod
    async def create_org_with_owner(
        self,
        org_id: UUID,
        org: Org,
        user: User,
        membership: Membership,
        identity: Identity | None = None,
        personal: tuple[Org, User, Membership] | None = None,
    ) -> None:
        """A named atomic create: the org, its first user, and the owner's
        membership land in one commit or not at all, so a slug or an identity
        taken meanwhile (`UniqueKeyTaken`) leaves no partial tenant behind.
        `identity`, when given, is the owner's identity as it should read once
        the tenant exists (new, or promoted to operator) and lands in the same
        commit: a tenant that is refused leaves no identity carrying a password
        or a flag nobody asked for. `personal`, when given, is a new owner's
        personal org with its user and membership, a second tenant landing in
        the same commit, each under its own tenant's fence."""
        ...

    @abstractmethod
    async def create_member(
        self,
        org_id: UUID,
        user: User,
        membership: Membership,
        outbox_rows: tuple[OutboxRow, ...],
        identity: Identity | None = None,
        personal: tuple[Org, User, Membership] | None = None,
        invitation: Invitation | None = None,
    ) -> None:
        """A named atomic create: the user, their membership, and the outbox rows
        land in one commit or not at all. A key taken meanwhile (one live user
        per identity, one membership per user) is `UniqueKeyTaken`, and nothing
        lands, the outbox rows included. `identity`, when given, is the person's
        new identity and lands in the same commit, for the same reason, and so
        does `personal`, the new person's personal org, as on
        `create_org_with_owner`, and `invitation`, the invitation of the tenant
        the membership accepts, as it reads once accepted."""
        ...

    @abstractmethod
    async def remove_member(
        self,
        org_id: UUID,
        user: User,
        membership: Membership,
        outbox_rows: tuple[OutboxRow, ...],
        revocation_row: Callable[[str, UUID], OutboxRow],
    ) -> tuple[OutboxRow, ...]:
        """A named atomic write: the soft-deleted user, their ended membership,
        the revocation of every live session and every unrevoked api key the
        user holds in the tenant, and the outbox rows, in one commit or not at
        all. So a failure never leaves a live user without a membership, and a
        removed member never keeps a credential. Each revoked credential lands
        with the row `revocation_row(kind, id)` builds, `tenancy.session.revoked`
        or `tenancy.api_key.deleted`, stamped with the user's `deleted_at` and
        `deleted_by`; those rows come back, for the caller to relay after the
        ones it passed. Both rows must exist in the tenant."""
        ...

    @abstractmethod
    async def write_closed_org(
        self,
        org_id: UUID,
        org: Org,
        outbox_rows: tuple[OutboxRow, ...],
        member_row: Callable[[User], OutboxRow],
        revocation_row: Callable[[str, UUID, UUID], OutboxRow],
    ) -> tuple[OutboxRow, ...]:
        """A named atomic write: the tenant's owner, or an operator, deletes
        it, and nobody keeps a way in. In one commit or not at all: the org as given, every
        live user of the tenant soft-deleted with their membership ended,
        every live session and unrevoked api key revoked, every pending
        invitation revoked, and the outbox rows, all stamped with the org's
        `updated_at` and `updated_by`. Each user it ends lands the row
        `member_row(user)` builds, `tenancy.user.deleted`, so their sockets
        close; each credential lands the row `revocation_row(kind, id,
        user_id)` builds, `tenancy.session.revoked` or
        `tenancy.api_key.deleted`. Those rows come back, the users' first,
        for the caller to relay after the ones it passed. NotFound, and
        nothing lands, when the org is not live in `org_id`."""
        ...

    @abstractmethod
    async def delete_person(
        self,
        identity_id: UUID,
        email: str,
        owned: tuple[UUID, ...],
        outbox_rows: tuple[OutboxRow, ...],
        revocation_row: Callable[[UUID, str, UUID], OutboxRow],
    ) -> tuple[OutboxRow, ...]:
        """Cross-tenant: a person is erased across every tenant they are in,
        which is the one thing this write is. A named atomic hard delete, in
        one commit or not at all: the identity (its second factor with it),
        every user it is in any tenant, live or removed, with their
        memberships, api keys, and socket tickets, every session it holds
        (the tenants' sessions, its sign-ins, and its operator tokens), the
        sign-in delay keyed on the digest of `email`, and every invitation
        that holds `email`, folded, or names one of those users as the one
        who accepted it. The outbox rows land in the same commit, each under the
        tenant it names. A session or an api key that was still live when it
        went lands the row `revocation_row(org_id, kind, id)` builds,
        `tenancy.session.revoked` or `tenancy.api_key.deleted`, so a socket
        it opened closes; those rows come back, for the caller to relay
        after the ones it passed.

        `owned` names the tenants the person owns that must keep an owner:
        their live owner memberships are locked in this transaction, and
        when one of them has no live owner but the person, nothing lands
        and it is Conflict, so two owners who leave at once never leave a
        tenant with none. NotFound, and nothing lands, when the identity is
        gone already."""
        ...

    @abstractmethod
    async def read_users(self, org_id: UUID, after: UUID | None, limit: int) -> list[User]:
        """The tenant's live users, by id ascending; a removed one is hidden.
        `after` is the id the previous page ended on, and the page starts
        strictly after it (`is_after_in_id_order`)."""
        ...

    @abstractmethod
    async def read_user(self, org_id: UUID, user_id: UUID) -> User | None: ...

    @abstractmethod
    async def read_users_by_identity(
        self, identity_id: UUID, limit: int
    ) -> list[tuple[UUID, User]]:
        """Cross-tenant sweep: the live users one identity is, in every tenant,
        with the tenant, by user id, at most `limit` of them. The caller picks
        the bound: the manager asks for one past the orgs a person may join, so
        a list cut short is told from a whole one."""
        ...

    @abstractmethod
    async def read_memberships_by_identity(
        self, identity_id: UUID, limit: int, after_user_id: UUID | None = None
    ) -> list[OrgMembership]:
        """Cross-tenant sweep: the places one identity holds, each the org, the
        user, and the role, by user id ascending, at most `limit` of them. Only
        the living: a deleted org, a removed user, or an ended membership is
        left out in the statement, so a page is never short because of rows
        the caller would drop. `after_user_id` is the user id the previous
        page ended on (`is_after_in_id_order`)."""
        ...

    @abstractmethod
    async def write_user(
        self, org_id: UUID, user: User, outbox_rows: tuple[OutboxRow, ...] = ()
    ) -> None:
        """Lands the row and the outbox rows that announce it together, so they
        land in one statement with it; so do the other writes below."""
        ...

    @abstractmethod
    async def read_memberships(
        self, org_id: UUID, limit: int, after_user_id: UUID | None = None
    ) -> list[Membership]:
        """The tenant's live memberships, by user id ascending, so a page of
        them covers the same members as the page of `read_users` that ends on
        the same id; an ended one is hidden. `after_user_id` is the user id
        the previous page ended on (`is_after_in_id_order`)."""
        ...

    @abstractmethod
    async def count_members(self, org_id: UUID, role: Role | None = None) -> int:
        """How many live memberships the tenant holds. With `role`, only
        those of that role: the owners a leaving owner would leave behind."""
        ...

    @abstractmethod
    async def read_membership_for_user(self, org_id: UUID, user_id: UUID) -> Membership | None:
        """The user's live membership, or None when there is none or it ended."""
        ...

    @abstractmethod
    async def read_principal(
        self, org_id: UUID, user_id: UUID, seen: tuple[UUID, datetime] | None = None
    ) -> tuple[Org | None, User | None, Membership | None]:
        """The three rows a credential stands for, read together: the org, the
        user in it, and the user's live membership, each None when missing.
        Every authenticated request reads them, so they come back from one
        statement in one transaction. Deleted rows are returned as stored; the
        caller decides what a deleted one means.

        `seen` is a session id and the time it was presented. When given, the
        same transaction records that use on the user's session, for its idle
        lifetime, and commits it; a revoked session is left as it is. The
        caller decides when a use is worth recording."""
        ...

    @abstractmethod
    async def write_membership(
        self, org_id: UUID, membership: Membership, outbox_rows: tuple[OutboxRow, ...] = ()
    ) -> None: ...

    @abstractmethod
    async def read_sessions(
        self, org_id: UUID, user_id: UUID, live_at: datetime, limit: int
    ) -> list[Session]:
        """One user's sessions live at `live_at` (not revoked, not yet expired),
        newest first, clamped after the filter so a live session is never
        pushed out of the page by dead ones."""
        ...

    @abstractmethod
    async def read_session(self, org_id: UUID, session_id: UUID) -> Session | None: ...

    @abstractmethod
    async def read_session_by_digest(self, token_hash: str) -> tuple[UUID, Session] | None:
        """Cross-tenant lookup: the gateway holds a token, not a tenant; the
        tenant travels back. A login credential and an operator token are
        rows of the system scope; a session token is its tenant's."""
        ...

    @abstractmethod
    async def read_session_with_identity_by_digest(
        self, token_hash: str
    ) -> tuple[UUID, Session, Identity | None] | None:
        """Cross-tenant lookup, as `read_session_by_digest`, with the identity
        the credential proves beside it, from one statement under the system
        scope: the identity stage reads both on every use. The identity is
        None when it is gone."""
        ...

    @abstractmethod
    async def read_operator_tokens(
        self, identity_id: UUID, live_at: datetime, after: UUID | None, limit: int
    ) -> list[Session]:
        """Global: an operator token is a row of the system scope, which no
        tenant holds. One identity's operator tokens live at `live_at` (not
        revoked, not yet expired), newest first, clamped after the filter;
        `after` is the id the previous page ended on, and the page starts
        strictly after it (`is_after_newest_first`). The read walks the
        system scope's live rows by expiry, which a token's hour and a
        sign-in's ten minutes bound."""
        ...

    @abstractmethod
    async def read_session_by_id(self, session_id: UUID) -> tuple[UUID, Session] | None:
        """Cross-tenant lookup: the identity stage holds the id of the session it
        was proven by, not its tenant; the tenant travels back."""
        ...

    @abstractmethod
    async def create_session(self, org_id: UUID, session: Session) -> bool:
        """The create of a credential kept as a session row: a sign-in, an
        operator token, or a session with no sign-in to end. False when the
        id is already written, which changes nothing. A token hash another
        row holds is UniqueKeyTaken, and nothing lands."""
        ...

    @abstractmethod
    async def write_session(
        self, org_id: UUID, session: Session, outbox_rows: tuple[OutboxRow, ...] = ()
    ) -> None:
        """The update by copy. A revocation is a session write with a handoff:
        the row announcing it lands beside the session, so the socket it
        opened hears of it."""
        ...

    @abstractmethod
    async def replace_session(
        self,
        org_id: UUID,
        session: Session,
        ended_org_id: UUID,
        ended: Session,
        outbox_rows: tuple[OutboxRow, ...],
    ) -> None:
        """A named atomic write: the new session lands in `org_id` and the one it
        replaces, revoked, lands in `ended_org_id` with the outbox rows that
        announce the revocation, in one commit or not at all, so a tab that
        switches tenants never holds two live sessions. The ended session must
        be in `ended_org_id` and still live as stored (not revoked); otherwise
        nothing lands: NotFound for a session that is not in that tenant,
        Conflict for one another write already ended. Two switches racing on
        one session admit one."""
        ...

    @abstractmethod
    async def exchange_sign_in(self, org_id: UUID, session: Session, ended: Session) -> None:
        """A named atomic write: the new session lands in `org_id` and the
        sign-in it was exchanged from, ended, in one commit or not at all, so
        a sign-in makes one session. A sign-in is a row of the system scope,
        which only the system scope writes, so this is one of the enumerated
        transactions that open it; the insert runs under `org_id`. With
        `EMPTY_UUID` as `org_id` the new row is the system scope's too: the
        sign-in a second factor verified, or the operator token a sign-in
        with one mints, each ending the sign-in it came from. The
        sign-in must be in the system scope and still live as stored (not
        revoked); otherwise nothing lands: NotFound for a credential that is
        not in the system scope (a tenant's session among them), Conflict for
        one another exchange already ended. Two exchanges racing on one
        sign-in admit one."""
        ...

    @abstractmethod
    async def read_api_keys(
        self, org_id: UUID, after: UUID | None, limit: int, user_id: UUID | None = None
    ) -> list[ApiKey]:
        """The tenant's unrevoked keys, newest first; only `user_id`'s when given,
        filtered before the clamp. `after` is the id the previous page ended
        on, and the page starts strictly after it (`is_after_newest_first`)."""
        ...

    @abstractmethod
    async def read_api_key(self, org_id: UUID, api_key_id: UUID) -> ApiKey | None: ...

    @abstractmethod
    async def read_api_key_by_digest(self, key_hash: str) -> tuple[UUID, ApiKey] | None:
        """Cross-tenant lookup: the gateway holds a key, not a tenant; the tenant travels back."""
        ...

    @abstractmethod
    async def issue_api_key(
        self,
        org_id: UUID,
        api_key: ApiKey,
        outbox_rows: tuple[OutboxRow, ...],
        attempt_id: UUID | None,
    ) -> tuple[ApiKey, bool]:
        """The create of a key: lands the key and its outbox rows together and
        returns `(api_key, True)`. When the id is already written, the rerun of
        a create that issues a secret, it writes the new `key_hash` (with
        `updated_at` and `updated_by`) onto that row instead, lands no outbox
        row, and returns `(the row as stored, False)`; the row keeps its name,
        role, expiry, and issuer.

        The re-mint is the one write of a rerun that changes what is stored, so
        it is fenced two ways, both in the statement. A revoked row is never
        re-minted, so a rerun cannot put a live secret back on a key somebody
        revoked in between. And the digest lands only while the idempotency
        marker on `api_key.id` still holds `attempt_id`, the attempt making the
        write: an attempt that ran past the marker's pending lease lost it to
        the retry that took it over, that retry has already handed its key to
        the caller, and the zombie's `finish` will be refused, so its re-mint
        is refused here too. The marker's liveness is the fence and no clock
        is, because two attempts can carry one `created_at` and a skewed clock
        can order them backwards; there is nothing to compare, only a marker to
        ask. `attempt_id` is None when the request carried no key, which leaves
        no marker to hold anything and so no rerun to admit. Every refusal is
        Conflict and changes nothing, as does an id written under another
        issuer.

        The marker is one of the two system rows every namespace touches, the
        way the outbox row is, so reading it here is the same crossing landing
        an outbox row already is: the Postgres impl reads it in this
        statement's own WHERE, the memory impl asks the markers the storage
        root hands it (`AttemptFenceInterface`)."""
        ...

    @abstractmethod
    async def write_api_key(
        self, org_id: UUID, api_key: ApiKey, outbox_rows: tuple[OutboxRow, ...] = ()
    ) -> None: ...

    # Invitations: a tenant's rows.
    @abstractmethod
    async def read_invitation(self, org_id: UUID, invitation_id: UUID) -> Invitation | None: ...

    @abstractmethod
    async def read_invitation_by_provider_id(
        self, org_id: UUID, provider_invitation_id: str
    ) -> Invitation | None:
        """The tenant's invitation the identity provider knows by that id."""
        ...

    @abstractmethod
    async def read_pending_invitation(self, org_id: UUID, email: str) -> Invitation | None:
        """The tenant's pending invitation for the address, in any spelling
        (`rules.fold_email`), expired or not."""
        ...

    @abstractmethod
    async def read_invitations(
        self, org_id: UUID, after: UUID | None, limit: int
    ) -> list[Invitation]:
        """The tenant's pending invitations, newest first; `after` is the id
        the previous page ended on (`is_after_newest_first`)."""
        ...

    @abstractmethod
    async def write_invitation(
        self, org_id: UUID, invitation: Invitation, outbox_rows: tuple[OutboxRow, ...] = ()
    ) -> None:
        """Lands the row and the outbox rows that announce it together. A
        provider id another row holds, or a second pending invitation for the
        address in the tenant, is UniqueKeyTaken, and nothing lands."""
        ...

    @abstractmethod
    async def purge_deleted(self, before: datetime, tickets_before: datetime, limit: int) -> int:
        """Cross-tenant, for the sweep, in the system scope: the one hard
        delete, one transaction a pass for every tenant and the system scope
        at once. It removes the users soft-deleted before `before` with their
        memberships (and any membership ended before `before`), the api keys
        revoked or expired before `before`, the sessions expired before
        `before` (a revoked one expires within the session's lifetime and
        goes then; the sign-in sessions of the system scope among them), the
        socket tickets expired before `tickets_before`, and the invitations
        accepted, revoked, or expired before `before`; at most `limit` rows
        of each kind, whatever their tenant, skipping rows another
        transaction holds; returns how many rows went."""
        ...

    @abstractmethod
    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        """The hard delete of a deleted tenant's rows once the retention has
        passed: every user, membership, api key, session, socket ticket, and
        invitation of the tenant, whatever its state, at most `limit` of each
        kind per call; returns how many rows went. The org row
        stays as the record that the tenant existed, so the operator plane
        still lists it and no new tenant takes its id."""
        ...

    @abstractmethod
    async def create_socket_ticket(self, org_id: UUID, ticket: SocketTicket) -> bool:
        """The create, and the only write of a ticket besides its redemption.
        False when the id is already written, which changes nothing. A ticket
        hash another row holds is UniqueKeyTaken, and nothing lands."""
        ...

    @abstractmethod
    async def redeem_socket_ticket(
        self, ticket_hash: str, redeemed_at: datetime
    ) -> tuple[UUID, SocketTicket] | None:
        """Cross-tenant lookup: the gateway holds a ticket, not a tenant; the tenant
        travels back. One statement: marks the unredeemed ticket with this hash
        redeemed and returns it, or None when it is unknown or already redeemed.
        Two concurrent redeemers never both get it."""
        ...
