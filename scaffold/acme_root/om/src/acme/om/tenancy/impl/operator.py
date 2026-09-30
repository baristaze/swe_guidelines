import logging
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import Field

from acme.om.base import EMPTY_UUID, Platform, new_id, utcnow
from acme.om.context import (
    CredentialKind,
    OperatorContext,
    OperatorPermission,
    OperatorRole,
    Role,
)
from acme.om.events.storage import EventStorageInterface
from acme.om.events.types.event import Event
from acme.om.exceptions import (
    Conflict,
    InvalidCredential,
    NotAuthorized,
    NotFound,
    PersonalOrgFixed,
    ValidationFailed,
)
from acme.om.idempotency.types.attempt import Attempt
from acme.om.outbox import OutboxRelayInterface
from acme.om.outbox.types.row import OutboxRow
from acme.om.tenancy.impl.creates import (
    MAX_ORGS_PER_IDENTITY,
    add_member_to,
    create_org_with_owner,
    user_payload,
)
from acme.om.tenancy.impl.shared import ended_by, exchange_sign_in, new_operator_token
from acme.om.tenancy.impl.totp import TotpSealer, new_totp_secret
from acme.om.tenancy.operator import TenancyOperatorManagerInterface
from acme.om.tenancy.rules import (
    MAX_OPERATOR_TOKEN_TTL,
    closed_org,
    matching_totp_step,
    operator_permissions_of,
    otpauth_uri,
)
from acme.om.tenancy.storage import TenancyStorageInterface
from acme.om.tenancy.types.identity import Identity
from acme.om.tenancy.types.issued import IssuedOperatorToken, IssuedTotpSecret
from acme.om.tenancy.types.org import Org
from acme.om.tenancy.types.page import OperatorTokenPage, OrgPage, UserPage
from acme.om.tenancy.types.session import Session
from acme.om.tenancy.types.size import PlatformSize
from acme.om.tenancy.types.user import User
from acme.om.work.types.work_item import WorkKind, work_row_kind

log = logging.getLogger(__name__)

SIZE_WINDOW = timedelta(hours=24)
"""How far back from its count the traffic figures of the platform's size look."""


TOTP_ISSUER = "Acme"
"""The name an authenticator app shows beside the operator's account."""


class TenancyOperatorOptions(Platform):
    max_limit: int = 200
    max_orgs_per_identity: int = MAX_ORGS_PER_IDENTITY
    """The tenant manager's bound, held the same on this plane."""
    operator_token_ttl: timedelta = MAX_OPERATOR_TOKEN_TTL
    """The longest an operator token lives; the tenant manager's, held the same."""
    totp_encryption_key: str | None = Field(default=None, repr=False)
    """The key the TOTP secrets are sealed under; the tenant manager's."""


class TenancyOperatorManagerImpl(TenancyOperatorManagerInterface):
    """Reads a tenant's rows through the storage of the namespace that owns
    them, under the tenant the operator named, never through that namespace's
    manager: a tenant manager takes a `TenantContext`, and none exists on this
    plane."""

    def __init__(
        self,
        storage: TenancyStorageInterface,
        events: EventStorageInterface,
        relay: OutboxRelayInterface,
        options: TenancyOperatorOptions,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._storage = storage
        self._events = events
        self._relay = relay
        self._options = options
        self._totp = TotpSealer(options.totp_encryption_key)
        self._clock = clock

    # The operator's own credentials.

    async def enrol_totp(self, admin: OperatorContext) -> IssuedTotpSecret:
        identity = await self._identity(admin)
        if identity.totp_enrolled:
            raise Conflict("a second factor is enrolled already")
        admin.require(OperatorPermission.ENROL)
        secret = new_totp_secret()
        sealed = self._totp.seal(identity.id, secret)
        if not await self._storage.write_totp_secret(identity.id, sealed, utcnow()):
            raise Conflict("a second factor is enrolled already")
        log.info("operator %s minted a second factor", admin.identity_id)
        return IssuedTotpSecret(otpauth_uri=otpauth_uri(secret, identity.email, TOTP_ISSUER))

    async def confirm_totp(self, admin: OperatorContext, totp_code: str) -> Identity:
        identity = await self._identity(admin)
        if identity.totp_enrolled:
            raise Conflict("a second factor is enrolled already")
        admin.require(OperatorPermission.ENROL)
        if identity.totp_secret is None:
            raise ValidationFailed("mint a second factor before confirming it")
        secret = self._totp.open(identity.id, identity.totp_secret)
        step = matching_totp_step(secret, totp_code, self._clock())
        if step is None:
            raise ValidationFailed("the code does not match; check the authenticator's clock")
        if not await self._storage.confirm_totp(identity.id, step, utcnow()):
            raise Conflict("the second factor changed meanwhile; enrol again")
        log.info("operator %s confirmed a second factor", admin.identity_id)
        return await self._identity(admin)

    async def issue_operator_token(
        self,
        admin: OperatorContext,
        operator_role: OperatorRole,
        expires_in: timedelta | None = None,
    ) -> IssuedOperatorToken:
        # A token never mints a token, and a password alone never mints one:
        # the stage must come from a sign-in that verified a code.
        if admin.credential_kind is not CredentialKind.LOGIN or not admin.second_factor:
            raise NotAuthorized("an operator token is minted from a sign-in with a second factor")
        admin.require(OperatorPermission.MINT)
        entry = admin.operator_entry
        if entry is None or not operator_permissions_of(operator_role) <= operator_permissions_of(
            entry
        ):
            raise NotAuthorized(f"operator lacks {operator_role.value}")
        found = await self._storage.read_session_by_id(admin.credential_id)
        if found is None:
            raise InvalidCredential("the sign-in behind the mint is gone")
        issued, token = new_operator_token(
            admin.identity_id, operator_role, expires_in, self._options.operator_token_ttl
        )
        # The sign-in is exchanged once: it ends in the write that lands the
        # token, so a sign-in makes one token, as it makes one session.
        await exchange_sign_in(
            self._storage, EMPTY_UUID, token, ended_by(found[1], admin.identity_id, utcnow())
        )
        log.info(
            "operator %s minted %s token %s", admin.identity_id, operator_role.value, issued.id
        )
        return issued

    async def get_operator_tokens(
        self, admin: OperatorContext, after: UUID | None, limit: int
    ) -> OperatorTokenPage:
        admin.require(OperatorPermission.READ)
        limit = self._clamp(limit)
        rows = await self._storage.read_operator_tokens(
            admin.identity_id, utcnow(), after, limit + 1
        )
        return OperatorTokenPage(items=tuple(rows[:limit]), has_more=len(rows) > limit)

    async def revoke_operator_token(self, admin: OperatorContext, token_id: UUID) -> Session:
        admin.require(OperatorPermission.READ)
        found = await self._storage.read_session_by_id(token_id)
        # An operator ends their own tokens, and no other operator's: the
        # allowlist is the grant job's, and so is ending another identity's
        # credentials (its disable). Another's reads as no token at all.
        if (
            found is None
            or found[0] != EMPTY_UUID
            or found[1].credential_kind is not CredentialKind.OPERATOR_TOKEN
            or found[1].identity_id != admin.identity_id
        ):
            raise NotFound(f"operator token {token_id} not found")
        token = found[1]
        if token.revoked_at is not None:
            return token  # ended already: the same answer again
        revoked = ended_by(token, admin.identity_id, utcnow())
        await self._storage.write_session(EMPTY_UUID, revoked)
        log.info(
            "operator %s revoked operator token %s by its %s %s",
            admin.identity_id,
            token_id,
            admin.credential_kind.value,
            admin.credential_id,
        )
        return revoked

    async def _identity(self, admin: OperatorContext) -> Identity:
        identity = await self._storage.read_identity(admin.identity_id)
        if identity is None:
            raise NotFound(f"identity {admin.identity_id} not found")
        return identity

    # Across every tenant.

    async def get_orgs(self, admin: OperatorContext, after: UUID | None, limit: int) -> OrgPage:
        admin.require(OperatorPermission.READ)
        limit = self._clamp(limit)
        rows = await self._storage.read_orgs(limit + 1, after)
        return OrgPage(items=tuple(rows[:limit]), has_more=len(rows) > limit)

    async def size(self, admin: OperatorContext) -> PlatformSize:
        admin.require(OperatorPermission.READ)
        # The tally the sweep keeps: a count across tenants never runs in a
        # request (ADR 0074).
        size = await self._storage.read_platform_size()
        if size is None:
            raise NotFound(
                "the platform's size is not counted yet; the maintenance worker counts it"
                " on its first sweep"
            )
        return size

    async def tally_size(self) -> PlatformSize:
        counted_at = utcnow()
        since = counted_at - SIZE_WINDOW
        tenants, users = await self._storage.count_orgs_and_users()
        size = PlatformSize(
            tenants=tenants,
            users=users,
            events_last_24h=await self._events.count_since(since),
            since=since,
            counted_at=counted_at,
        )
        await self._storage.write_platform_size(size)
        return size

    async def create_org(
        self,
        admin: OperatorContext,
        name: str,
        slug: str,
        owner_email: str,
        owner_name: str,
        attempt: Attempt | None = None,
    ) -> Org:
        admin.require(OperatorPermission.WRITE)
        org_id = new_id() if attempt is None else attempt.target_id
        if attempt is not None:
            # The rerun of a create that landed: the row as stored, in order.
            stored = await self._storage.read_org(org_id)
            if stored is not None:
                return stored
        org, _, _ = await create_org_with_owner(
            self._storage,
            org_id=org_id,
            org_name=name,
            slug=slug,
            email=owner_email,
            display_name=owner_name,
            max_orgs=self._options.max_orgs_per_identity,
        )
        log.info("operator %s created org %s", admin.identity_id, org.id)
        return org

    # One named tenant.

    async def get_org(self, admin: OperatorContext, org_id: UUID) -> Org:
        admin.require(OperatorPermission.READ)
        org = await self._org(org_id)
        self._trail(admin, org_id, "org")
        return org

    async def get_members(
        self, admin: OperatorContext, org_id: UUID, after: UUID | None, limit: int
    ) -> UserPage:
        admin.require(OperatorPermission.READ)
        await self._org(org_id)
        limit = self._clamp(limit)
        rows = await self._storage.read_users(org_id, after, limit + 1)
        self._trail(admin, org_id, "members")
        return UserPage(items=tuple(rows[:limit]), has_more=len(rows) > limit)

    async def add_member(
        self,
        admin: OperatorContext,
        org_id: UUID,
        email: str,
        display_name: str,
        role: Role,
        attempt: Attempt | None = None,
    ) -> User:
        admin.require(OperatorPermission.WRITE)
        org = await self._org(org_id)
        # A closed org is gone to its members already: nobody joins it.
        if org.deleted_at is not None or closed_org(org, await self._storage.count_members(org_id)):
            raise NotFound(f"org {org_id} not found")
        if role in (Role.OWNER, Role.SERVICE):
            raise ValidationFailed(f"{role.value} is not a role a member is added with")
        user_id = new_id() if attempt is None else attempt.target_id
        if attempt is not None:
            stored = await self._storage.read_user(org_id, user_id)
            if stored is not None:
                return stored
        user, created = await add_member_to(
            self._storage,
            self._relay,
            org_id=org_id,
            user_id=user_id,
            email=email,
            display_name=display_name,
            role=role,
            actor_id=admin.identity_id,
            request=admin,
            max_orgs=self._options.max_orgs_per_identity,
        )
        if created:
            log.info("operator %s added user %s to org %s", admin.identity_id, user.id, org_id)
        return user

    async def get_events(
        self, admin: OperatorContext, org_id: UUID, after_seq: int, limit: int
    ) -> list[Event]:
        admin.require(OperatorPermission.READ)
        await self._org(org_id)
        events = await self._events.read_after(org_id, max(0, after_seq), self._clamp(limit))
        self._trail(admin, org_id, "events")
        return events

    async def delete_org(self, admin: OperatorContext, org_id: UUID) -> Org:
        admin.require(OperatorPermission.WRITE)
        org = await self._storage.read_org(org_id)
        if org is None or org.deleted_at is not None:
            raise NotFound(f"org {org_id} not found")
        if org.personal:
            raise PersonalOrgFixed("a personal org is not deleted; it is its person's place")
        if closed_org(org, await self._storage.count_members(org_id)):
            # Closed already, by its owner or by an operator, and its work is
            # queued: the org as it stands, and nothing asked for twice.
            return org
        now = utcnow()
        # An owner's deletion, as it writes it (ADR 0042): the org stays live,
        # with nobody in it, until the queue has ended its providers, and its
        # provider organization leaves the row now, so no sign-in through it
        # finds the org meanwhile. An operator has no user in the tenant, so
        # the actor every row records is the operator's identity, and the
        # worker deletes the org under that name.
        closed = org.model_copy(
            update={"provider_org_id": None, "updated_at": now, "updated_by": admin.identity_id}
        )
        work = self._row(
            admin,
            org_id,
            work_row_kind(WorkKind.DELETE_ORG),
            org_id,
            {"provider_org_id": org.provider_org_id},
        )

        def member_row(member: User) -> OutboxRow:
            return self._row(admin, org_id, "tenancy.user.deleted", member.id, user_payload(member))

        def revocation(kind: str, credential_id: UUID, holder: UUID) -> OutboxRow:
            return self._row(admin, org_id, kind, credential_id, {"user_id": str(holder)})

        ended = await self._storage.write_closed_org(
            org_id, closed, (work,), member_row, revocation
        )
        # Each member's removal, so a socket closes because its person left,
        # then each revocation. Every row is durable already: whatever a crash
        # leaves unrelayed, the sweep relays.
        await self._relay.relay_all(org_id, (work, *ended))
        log.info("operator %s deleted org %s", admin.identity_id, org_id)
        return closed

    @staticmethod
    def _row(
        admin: OperatorContext,
        org_id: UUID,
        kind: str,
        target_id: UUID,
        payload: Mapping[str, Any],
    ) -> OutboxRow:
        """A row an operator's write lands in the tenant's own stream. An
        operator has no user in the tenant, so the actor is their identity."""
        return OutboxRow(
            id=new_id(),
            created_at=utcnow(),
            org_id=org_id,
            kind=kind,
            target_id=target_id,
            payload=payload,
            actor_id=admin.identity_id,
            request_id=admin.request_id,
            app=admin.app.type.value,
            traceparent=admin.traceparent,
        )

    async def _org(self, org_id: UUID) -> Org:
        """The org named, deleted or not: an operator reads a deleted tenant's
        rows until the sweep takes them."""
        org = await self._storage.read_org(org_id)
        if org is None:
            raise NotFound(f"org {org_id} not found")
        return org

    def _clamp(self, limit: int) -> int:
        return max(1, min(limit, self._options.max_limit))

    @staticmethod
    def _trail(admin: OperatorContext, org_id: UUID, what: str) -> None:
        """The support trail: one line per read of a tenant's rows, naming the
        tenant and the operator and nothing of what was read."""
        log.info("operator %s read %s of org %s", admin.identity_id, what, org_id)
