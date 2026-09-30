import logging
from collections.abc import Awaitable, Callable
from uuid import UUID

from acme.infra.exceptions import InfraException
from acme.integrations.identity import IdentityProviderInterface
from acme.om.base import EMPTY_UUID, new_id, utcnow
from acme.om.context import CredentialKind, Permission, RequestContext, Role, TenantContext
from acme.om.exceptions import (
    Conflict,
    InvalidCredential,
    LastOwner,
    NotAuthorized,
    NotFound,
    OperatorRoleHeld,
    PersonalOrgFixed,
    PlatformException,
    ValidationFailed,
)
from acme.om.idempotency.types.attempt import Attempt
from acme.om.outbox import OutboxRelayInterface
from acme.om.outbox.types.row import OutboxRow, outbox_row
from acme.om.tenancy.impl.creates import (
    owner_rows,
    refuse_one_more,
    slug_suffix,
    user_payload,
    users_of,
)
from acme.om.tenancy.impl.manager import TenancyOptions
from acme.om.tenancy.impl.shared import (
    create_session,
    live_user,
    memberships_of,
    mint_token,
    principal_in,
    provider_logout_url,
)
from acme.om.tenancy.org import TenancyOrgManagerInterface
from acme.om.tenancy.rules import (
    check_org,
    check_time_zone,
    confirms_deletion,
    confirms_org_deletion,
    deadline_kept,
    hash_token,
    left_without_owner,
    slug_from_name,
)
from acme.om.tenancy.storage import TenancyStorageInterface
from acme.om.tenancy.types.identity import Identity
from acme.om.tenancy.types.issued import AccountDeleted, IssuedSession, OrgDeleted, OrgMembership
from acme.om.tenancy.types.org import Org
from acme.om.tenancy.types.session import Session
from acme.om.tenancy.types.user import User
from acme.om.work.types.work_item import WorkKind, work_row_kind

log = logging.getLogger(__name__)


DELETED_PERSONAL_ORG_NAME = "Deleted account"
"""What the record of a deleted account's personal org is called."""


class TenancyOrgManagerImpl(TenancyOrgManagerInterface):
    def __init__(
        self,
        storage: TenancyStorageInterface,
        relay: OutboxRelayInterface,
        options: TenancyOptions,
        *,
        identity_provider: IdentityProviderInterface,
        service_context: Callable[[RequestContext, UUID, UUID], Awaitable[TenantContext]],
    ) -> None:
        self._storage = storage
        self._relay = relay
        self._options = options
        self._provider = identity_provider
        # The manager's transition of that name, as a callable: a stage comes
        # only from the manager, and the manager holds this delegate, so the
        # root passes the one operation instead of the manager.
        self._service_context = service_context

    # The principal.

    async def get_org(self, ctx: TenantContext) -> Org:
        ctx.require(Permission.READ)
        org = await self._storage.read_org(ctx.org_id)
        if org is None or org.deleted_at is not None:
            raise NotFound(f"org {ctx.org_id} not found")
        return org

    async def get_me(self, ctx: TenantContext) -> OrgMembership:
        ctx.require(Permission.READ)
        org, user, membership = await self._storage.read_principal(ctx.org_id, ctx.user_id)
        if org is None or org.deleted_at is not None:
            raise NotFound(f"org {ctx.org_id} not found")
        if user is None or user.deleted_at is not None or membership is None:
            raise NotFound(f"user {ctx.user_id} not found")
        return OrgMembership(org=org, user=user, role=membership.role)

    async def create_org(
        self, ctx: TenantContext, name: str, slug: str | None, attempt: Attempt | None = None
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
        user = await live_user(self._storage, ctx, ctx.user_id)
        identity = await self._storage.read_identity(user.identity_id)
        if identity is None:
            raise InvalidCredential("the identity is gone")
        org_id = new_id() if attempt is None else attempt.target_id
        if attempt is not None and await self._storage.read_org(org_id) is not None:
            # The rerun of a create that landed: the place as it stands.
            org, owner, membership = await principal_in(
                self._storage, org_id, identity.id, self._options.max_orgs_per_identity
            )
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

    async def get_identity(self, ctx: TenantContext) -> Identity:
        ctx.require(Permission.READ)
        user = await live_user(self._storage, ctx, ctx.user_id)
        identity = await self._storage.read_identity(user.identity_id)
        if identity is None:
            raise NotFound(f"identity {user.identity_id} not found")
        return identity

    async def set_time_zone(self, ctx: TenantContext, time_zone: str) -> Identity:
        ctx.require(Permission.READ)
        try:
            check_time_zone(time_zone)
        except ValueError as error:
            raise ValidationFailed(str(error)) from None
        user = await live_user(self._storage, ctx, ctx.user_id)
        if not await self._storage.write_time_zone(user.identity_id, time_zone, utcnow()):
            raise NotFound(f"identity {user.identity_id} not found")
        return await self.get_identity(ctx)

    async def delete_account(
        self, ctx: TenantContext, confirm_email: str, return_to: str | None = None
    ) -> AccountDeleted:
        ctx.require(Permission.READ)
        # A person deletes their account, not a program: an api key belongs
        # to the tenant it was minted in, and the person is not its to erase.
        if ctx.security.credential_kind is not CredentialKind.SESSION_TOKEN:
            raise NotAuthorized("only a signed-in person deletes their account")
        if return_to is not None and return_to not in self._options.sign_out_return_uris:
            raise ValidationFailed("that is not this environment's sign-out return")
        user = await live_user(self._storage, ctx, ctx.user_id)
        identity = await self._storage.read_identity(user.identity_id)
        if identity is None:
            raise InvalidCredential("the identity is gone")
        if not confirms_deletion(identity.email, confirm_email):
            raise ValidationFailed("type your account's email to delete it")
        if identity.operator_role is not None:
            raise OperatorRoleHeld(
                "an operator's account is deleted once the operator role is taken off"
            )
        places = await memberships_of(
            self._storage, identity.id, self._options.max_orgs_per_identity
        )
        refusal = await self._last_owner(places)
        if refusal.orgs:
            raise refusal
        asking = await self._storage.read_session(ctx.org_id, ctx.security.credential_id)
        rows: list[OutboxRow] = []
        for place in places:
            # Each row is written under the person's own place in its org:
            # the removal is theirs, and so is the work it asks for.
            where = await self._service_context(ctx, place.org.id, place.user.id)
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
        provider_logout = (
            None if asking is None else provider_logout_url(self._provider, asking, return_to)
        )
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

    async def delete_personal_org(self, ctx: TenantContext) -> Org | None:
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

    async def delete_org(self, ctx: TenantContext, confirm_name: str) -> OrgDeleted:
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
        user = await live_user(self._storage, ctx, ctx.user_id)
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
        provider's session the one that asked came from, and its deadline.
        None, and the owner signs in again, when they have no personal org or
        it cannot be made."""
        places = await memberships_of(
            self._storage, user.identity_id, self._options.max_orgs_per_identity
        )
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
            expires_at=deadline_kept(asking.expires_at, now, self._options.session_ttl),
            provider_session_id=asking.provider_session_id,
        )
        try:
            await create_session(self._storage, home.org.id, session)
        except (InfraException, PlatformException) as error:
            # The org is gone either way; only the landing failed.
            log.warning("no session in the personal org of %s: %s", user.identity_id, error)
            return None
        return IssuedSession(
            token=token, expires_at=session.expires_at, org=home.org, user=home.user, role=home.role
        )

    async def delete_closed_org(self, ctx: TenantContext) -> Org | None:
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
