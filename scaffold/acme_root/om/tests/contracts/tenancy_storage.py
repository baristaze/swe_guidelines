"""The tenancy storage contract. The cases named in `CROSS_TENANT_CASES` are
the tenant fence's evidence: each one presents another tenant's identifier and
asserts that nothing is found and nothing changes. The negative control that
says what they catch is in `docs/runbooks/tenant-isolation.md`."""

from collections.abc import Callable
from datetime import datetime, timedelta
from unittest.mock import ANY
from uuid import UUID, uuid4

import pytest

from acme.om.base import EMPTY_UUID, new_id, utcnow
from acme.om.context import OperatorRole, Role
from acme.om.exceptions import Conflict, NotFound, RowDeleted, TenantMismatch, UniqueKeyTaken
from acme.om.idempotency.storage import IdempotencyStorageInterface
from acme.om.idempotency.types.attempt import lease_bound
from acme.om.idempotency.types.record import IdempotencyRecord
from acme.om.outbox.storage import OutboxStorageInterface
from acme.om.outbox.types.row import OutboxRow
from acme.om.tenancy.rules import email_digest
from acme.om.tenancy.storage import TenancyStorageInterface
from acme.om.tenancy.types.api_key import ApiKey
from acme.om.tenancy.types.identity import Identity
from acme.om.tenancy.types.invitation import InvitationState
from acme.om.tenancy.types.session import Session
from acme.om.tenancy.types.size import PlatformSize
from acme.om.tenancy.types.user import User
from contracts.factories import (
    make_api_key,
    make_identity,
    make_invitation,
    make_membership,
    make_operator_token,
    make_org,
    make_personal_org,
    make_session,
    make_sign_in,
    make_socket_ticket,
    make_user,
)
from contracts.outbox_storage import claim_all
from contracts.racing import race


async def drained(storage: TenancyStorageInterface) -> datetime:
    """A cut no other case's row is past, with whatever an earlier run of
    these cases left behind it purged first: the purge reaches across
    tenants, so a case owns the rows behind its cut."""
    cut = utcnow() - timedelta(days=36500)
    while await storage.purge_deleted(cut, cut, 1000):
        pass
    return cut


def before(cut: datetime, by: timedelta) -> timedelta:
    """The lifetime from now that ends `by` before `cut`, for a factory that
    takes a lifetime and not an instant."""
    return cut - by - utcnow()


CROSS_TENANT_CASES: frozenset[str] = frozenset(
    {
        "write_closed_org",
        "count_members",
        "create_member",
        "create_session",
        "create_socket_ticket",
        "exchange_sign_in",
        "create_org_with_owner",
        "issue_api_key",
        "mark_org_purged",
        "read_invitation",
        "read_invitation_by_provider_id",
        "read_invitations",
        "read_pending_invitation",
        "write_invitation",
        "purge_tenant",
        "read_api_key",
        "read_api_keys",
        "read_membership_for_user",
        "read_memberships",
        "read_org",
        "read_principal",
        "read_session",
        "read_sessions",
        "read_user",
        "read_users",
        "remove_member",
        "replace_session",
        "write_api_key",
        "write_membership",
        "write_org",
        "write_session",
        "write_user",
    }
)
"""Every method of `TenancyStorageInterface` that takes a tenant has a case in
this module that presents another tenant's. `test_storage_exceptions.py` holds
the two sets to each other, so a new method arrives with its case."""


def make_marker(api_key: ApiKey, attempt_id: UUID) -> IdempotencyRecord:
    """The pending marker a creating request runs under, on the key's id: what
    the re-mint of a rerun is fenced on. A marker is per (tenant, user, key),
    so each attempt in a test that needs two markers carries its own key."""
    return IdempotencyRecord(
        id=new_id(),
        created_at=utcnow(),
        user_id=api_key.user_id,
        key=f"key-{attempt_id}",
        request_digest="digest",
        target_id=api_key.id,
        attempt_id=attempt_id,
    )


def make_key_row(org_id: UUID, api_key: ApiKey) -> OutboxRow:
    """The outbox row a key's create lands with; the memory outbox the root
    wires receives it, the Postgres one inserts it in the same commit."""
    return OutboxRow(
        id=new_id(),
        created_at=utcnow(),
        org_id=org_id,
        kind="tenancy.api_key.created",
        target_id=api_key.id,
        payload={"name": api_key.name},
        actor_id=api_key.user_id,
        request_id=new_id(),
        app="api",
    )


def make_user_row(org_id: UUID, user: User) -> OutboxRow:
    return OutboxRow(
        id=new_id(),
        created_at=utcnow(),
        org_id=org_id,
        kind="tenancy.user.created",
        target_id=user.id,
        payload={"identity_id": str(user.identity_id)},
        actor_id=user.created_by,
        request_id=new_id(),
        app="cli",
    )


def make_session_row(org_id: UUID, session: Session) -> OutboxRow:
    """The row a revocation lands with; the snapshot never carries the token hash."""
    return OutboxRow(
        id=new_id(),
        created_at=utcnow(),
        org_id=org_id,
        kind="tenancy.session.revoked",
        target_id=session.id,
        payload={"user_id": str(session.user_id)},
        actor_id=session.user_id,
        request_id=new_id(),
        app="portal",
    )


def revocations_by(actor_id: UUID, org_id: UUID) -> Callable[[str, UUID], OutboxRow]:
    """The row a removal lands for each credential it revokes, as the manager
    builds it: the kind and the credential, ids only."""

    def revocation(kind: str, credential_id: UUID) -> OutboxRow:
        return OutboxRow(
            id=new_id(),
            created_at=utcnow(),
            org_id=org_id,
            kind=kind,
            target_id=credential_id,
            payload={},
            actor_id=actor_id,
            request_id=new_id(),
            app="portal",
        )

    return revocation


class TenancyStorageContract:
    @pytest.fixture
    def storage(self) -> TenancyStorageInterface:
        raise NotImplementedError("the concrete test class provides the storage")

    @pytest.fixture
    def outbox(self) -> OutboxStorageInterface:
        """The outbox the tenancy storage lands its rows in: the one the
        Postgres impl inserts into in the same commit, the one the root handed
        the memory impl. The concrete test class wires it."""
        raise NotImplementedError("the concrete test class provides the outbox")

    @pytest.fixture
    def markers(self) -> IdempotencyStorageInterface:
        """The same markers the tenancy storage fences its re-mint on: the one
        store for the Postgres impls, the one the root handed for the memory
        ones. The concrete test class wires it."""
        raise NotImplementedError("the concrete test class provides the markers")

    async def test_org_round_trip(self, storage: TenancyStorageInterface) -> None:
        org = make_org()
        await storage.write_org(org.id, org)
        assert await storage.read_org(org.id) == org
        assert await storage.read_org_by_slug(org.slug) == org
        assert org in await storage.read_orgs(limit=1000)

    async def test_the_counts_read_the_living_across_every_tenant(
        self, storage: TenancyStorageInterface
    ) -> None:
        """The platform's size: live orgs and live users, whichever tenant they
        are in. A deleted org and a removed member are out of the count and an
        org's rows stay counted while it is only soft-deleted."""
        assert await storage.count_orgs_and_users() == (0, 0)
        first, second = make_org("First"), make_org("Second")
        for org in (first, second):
            identity = make_identity()
            owner = make_user(identity.id, identity.email)
            await storage.create_org_with_owner(
                org.id, org, owner, make_membership(owner.id, Role.OWNER), identity
            )
        joiner = make_identity()
        member = make_user(joiner.id, joiner.email)
        await storage.write_identity(joiner)
        await storage.create_member(
            first.id, member, make_membership(member.id), (make_user_row(first.id, member),)
        )
        assert await storage.count_orgs_and_users() == (2, 3)

        await storage.write_user(
            first.id, member.model_copy(update={"deleted_at": utcnow(), "deleted_by": member.id})
        )
        await storage.write_org(
            second.id, second.model_copy(update={"deleted_at": utcnow(), "deleted_by": new_id()})
        )
        assert await storage.count_orgs_and_users() == (1, 2)

    async def test_the_tally_keeps_the_newest_count_of_the_platforms_size(
        self, storage: TenancyStorageInterface
    ) -> None:
        """One row, read back as written; a newer count replaces it and an
        older one, from a worker that counted first and wrote last, does not."""
        assert await storage.read_platform_size() is None
        at = utcnow()
        first = PlatformSize(
            tenants=3,
            users=5,
            events_last_24h=11,
            since=at - timedelta(hours=24),
            counted_at=at,
        )
        await storage.write_platform_size(first)
        assert await storage.read_platform_size() == first
        later = first.model_copy(
            update={
                "tenants": 4,
                "counted_at": at + timedelta(minutes=5),
                "since": at + timedelta(minutes=5) - timedelta(hours=24),
            }
        )
        await storage.write_platform_size(later)
        assert await storage.read_platform_size() == later
        await storage.write_platform_size(first)
        assert await storage.read_platform_size() == later

    async def test_an_org_write_lands_its_outbox_row_beside_it(
        self, storage: TenancyStorageInterface
    ) -> None:
        org = make_org()
        await storage.write_org(org.id, org)
        deleted = org.model_copy(update={"deleted_at": utcnow(), "deleted_by": new_id()})
        row = OutboxRow(
            id=new_id(),
            created_at=utcnow(),
            org_id=org.id,
            kind="tenancy.org.deleted",
            target_id=org.id,
            payload={"slug": org.slug},
            actor_id=new_id(),
            request_id=new_id(),
            app="portal",
        )
        await storage.write_org(org.id, deleted, (row,))
        assert await storage.read_org(org.id) == deleted
        assert await storage.read_org_by_slug(org.slug) is None

    async def test_a_write_lands_every_row_that_announces_it(
        self, storage: TenancyStorageInterface, outbox: OutboxStorageInterface
    ) -> None:
        """A write on a core entity takes the rows that announce it as a tuple,
        so they land in one statement with it. An entity change is one row; a
        write that also starts work carries a second of kind `work.<kind>`,
        because the queue is a role of its own and no statement reaches both."""
        org = make_org()
        await storage.write_org(org.id, org)
        user = make_user(make_identity().id)
        change = make_user_row(org.id, user)
        asked = change.model_copy(update={"id": new_id(), "kind": "work.noop"})
        await storage.create_member(org.id, user, make_membership(user.id), (change, asked))
        landed = [row.id for row in await claim_all(outbox) if row.target_id == user.id]
        assert sorted(landed) == sorted([change.id, asked.id])

    async def test_purge_tenant_takes_every_row_of_the_tenant_and_keeps_the_org(
        self, storage: TenancyStorageInterface
    ) -> None:
        org, other = make_org(), make_org("Other")
        await storage.write_org(org.id, org)
        await storage.write_org(other.id, other)
        for tenant in (org, other):
            user = make_user(make_identity().id)
            await storage.create_member(
                tenant.id, user, make_membership(user.id), (make_user_row(tenant.id, user),)
            )
            await storage.write_api_key(tenant.id, make_api_key(user.id, uuid4().hex))
            await storage.write_session(tenant.id, make_session(new_id(), user.id, uuid4().hex))
            await storage.create_socket_ticket(tenant.id, make_socket_ticket(user.id, uuid4().hex))
        assert await storage.purge_tenant(org.id, 10) == 5
        assert await storage.read_users(org.id, None, limit=10) == []
        assert await storage.read_memberships(org.id, limit=10) == []
        assert await storage.read_api_keys(org.id, None, limit=10) == []
        assert await storage.read_org(org.id) == org
        assert await storage.purge_tenant(org.id, 10) == 0
        # The other tenant is untouched.
        assert len(await storage.read_users(other.id, None, limit=10)) == 1
        assert len(await storage.read_memberships(other.id, limit=10)) == 1
        assert len(await storage.read_api_keys(other.id, None, limit=10)) == 1

    async def test_a_backlog_past_a_batch_goes_a_batch_at_a_time(
        self, storage: TenancyStorageInterface
    ) -> None:
        """Each kind of row goes a batch at a time: a call takes at most the
        batch of each, and the next call takes the rest."""
        org = make_org()
        await storage.write_org(org.id, org)
        cut = await drained(storage)
        user = make_user(make_identity().id)
        for _ in range(3):
            await storage.write_session(
                org.id,
                make_session(new_id(), user.id, uuid4().hex, ttl=before(cut, timedelta(days=1))),
            )
            await storage.create_socket_ticket(
                org.id,
                make_socket_ticket(user.id, uuid4().hex, ttl=before(cut, timedelta(days=1))),
            )
        assert await storage.purge_deleted(cut, cut, 2) == 4, "two of each"
        assert await storage.purge_deleted(cut, cut, 2) == 2, "the rest"
        assert await storage.purge_deleted(cut, cut, 2) == 0
        for _ in range(3):
            await storage.write_session(org.id, make_session(new_id(), user.id, uuid4().hex))
        assert await storage.purge_tenant(org.id, 2) == 2
        assert await storage.purge_tenant(org.id, 2) == 1
        assert await storage.purge_tenant(org.id, 2) == 0

    async def test_a_deleted_org_is_marked_purged_once_and_no_other(
        self, storage: TenancyStorageInterface
    ) -> None:
        live, gone, other = make_org("Live"), make_org("Gone"), make_org("Other")
        now = utcnow()
        deleted = gone.model_copy(update={"deleted_at": now, "deleted_by": gone.id})
        for org in (live, deleted, other):
            await storage.write_org(org.id, org)
        assert await storage.mark_org_purged(live.id, now) is False, "a live tenant"
        assert await storage.mark_org_purged(other.id, now) is False
        assert await storage.mark_org_purged(new_id(), now) is False, "an unknown one"
        assert await storage.mark_org_purged(deleted.id, now) is True
        marked = await storage.read_org(deleted.id)
        assert marked is not None and marked.purged_at == now
        assert await storage.mark_org_purged(deleted.id, now) is False, "once"
        assert (await storage.read_org(other.id)) == other, "another tenant is untouched"

    async def test_reads_are_tenant_scoped(self, storage: TenancyStorageInterface) -> None:
        org_a, org_b = make_org("A"), make_org("B")
        identity = make_identity()
        user = make_user(identity.id)
        await storage.write_user(org_a.id, user)
        assert await storage.read_user(org_a.id, user.id) == user
        assert await storage.read_user(org_b.id, user.id) is None
        assert await storage.read_users(org_b.id, None, limit=10) == []

    async def test_write_refuses_another_tenant(self, storage: TenancyStorageInterface) -> None:
        org_a, org_b = make_org("A"), make_org("B")
        user = make_user(make_identity().id)
        await storage.write_user(org_a.id, user)
        with pytest.raises(TenantMismatch):
            await storage.write_user(org_b.id, user.model_copy(update={"display_name": "Moved"}))
        assert (await storage.read_user(org_a.id, user.id)) == user

    async def test_the_org_read_and_write_are_tenant_scoped(
        self, storage: TenancyStorageInterface
    ) -> None:
        """An org is its own tenant, so `read_org` names the id twice and a
        tenant that holds no org reads nothing. The write is the fence: an org
        row is never moved under another tenant."""
        org, other = make_org("A"), make_org("B")
        await storage.write_org(org.id, org)
        assert await storage.read_org(other.id) is None
        with pytest.raises(TenantMismatch):
            await storage.write_org(other.id, org.model_copy(update={"name": "Stolen"}))
        assert await storage.read_org(org.id) == org

    async def test_the_membership_reads_and_writes_are_tenant_scoped(
        self, storage: TenancyStorageInterface
    ) -> None:
        org_a, org_b = make_org("A"), make_org("B")
        membership = make_membership(new_id())
        await storage.write_membership(org_a.id, membership)
        assert await storage.read_memberships(org_b.id, limit=10) == []
        assert await storage.read_membership_for_user(org_b.id, membership.user_id) is None
        with pytest.raises(TenantMismatch):
            await storage.write_membership(
                org_b.id, membership.model_copy(update={"role": Role.OWNER})
            )
        assert await storage.read_memberships(org_a.id, limit=10) == [membership]

    async def test_the_principal_is_read_whole_and_only_under_its_tenant(
        self, storage: TenancyStorageInterface
    ) -> None:
        """The org, the user, and the live membership a credential stands for,
        as the three single reads answer them; another tenant reads none of
        them, and an ended membership reads as none."""
        org, other = make_org("A"), make_org("B")
        user = make_user(make_identity().id)
        membership = make_membership(user.id)
        await storage.create_org_with_owner(org.id, org, user, membership)
        assert await storage.read_principal(org.id, user.id) == (org, user, membership)
        assert await storage.read_principal(other.id, user.id) == (None, None, None)
        ended = membership.model_copy(update={"deleted_at": utcnow(), "deleted_by": user.id})
        await storage.write_membership(org.id, ended)
        assert await storage.read_principal(org.id, user.id) == (org, user, None)

    async def test_the_member_count_is_the_tenants_live_memberships(
        self, storage: TenancyStorageInterface
    ) -> None:
        """Live memberships of this tenant, an ended one not among them,
        another tenant's never."""
        org_a, org_b = make_org("A"), make_org("B")
        kept, ended = make_membership(new_id()), make_membership(new_id())
        await storage.write_membership(org_a.id, kept)
        await storage.write_membership(org_a.id, ended)
        await storage.write_membership(
            org_a.id, ended.model_copy(update={"deleted_at": utcnow(), "deleted_by": new_id()})
        )
        assert await storage.count_members(org_a.id) == 1
        assert await storage.count_members(org_b.id) == 0

    async def test_the_owner_count_is_the_tenants_live_owners(
        self, storage: TenancyStorageInterface
    ) -> None:
        """The owners a leaving owner would leave behind: live owner
        memberships of this tenant, and no other tenant's."""
        org_a, org_b = make_org("A"), make_org("B")
        owner, member = make_membership(new_id(), Role.OWNER), make_membership(new_id())
        ended = make_membership(new_id(), Role.OWNER)
        for membership in (owner, member, ended):
            await storage.write_membership(org_a.id, membership)
        await storage.write_membership(
            org_a.id, ended.model_copy(update={"deleted_at": utcnow(), "deleted_by": new_id()})
        )
        await storage.write_membership(org_b.id, make_membership(new_id(), Role.OWNER))
        assert await storage.count_members(org_a.id, Role.OWNER) == 1
        assert await storage.count_members(org_a.id, Role.MEMBER) == 1
        assert await storage.count_members(org_a.id) == 2

    async def test_the_session_reads_and_writes_are_tenant_scoped(
        self, storage: TenancyStorageInterface
    ) -> None:
        """A session list is personal to a user inside a tenant, so both keys
        are in the query: the same user id under another tenant lists nothing,
        and a create or a revocation written from there lands nothing."""
        org_a, org_b = make_org("A"), make_org("B")
        user_id = new_id()
        session = make_session(new_id(), user_id, uuid4().hex)
        assert await storage.create_session(org_a.id, session)
        assert await storage.read_sessions(org_b.id, user_id, utcnow(), limit=10) == []
        assert await storage.read_session(org_b.id, session.id) is None
        assert not await storage.create_session(
            org_b.id, session.model_copy(update={"token_hash": uuid4().hex})
        )
        with pytest.raises(TenantMismatch):
            await storage.write_session(
                org_b.id, session.model_copy(update={"revoked_at": utcnow()})
            )
        assert await storage.read_session(org_a.id, session.id) == session
        assert await storage.read_sessions(org_a.id, user_id, utcnow(), limit=10) == [session]

    async def test_the_api_key_reads_the_page_and_the_writes_are_tenant_scoped(
        self, storage: TenancyStorageInterface
    ) -> None:
        """The key list pages newest first. The cursor of another tenant's page
        carries nothing across, with the owner filter on or off."""
        org_a, org_b = make_org("A"), make_org("B")
        user_id = new_id()
        keys = [make_api_key(user_id, uuid4().hex) for _ in range(3)]
        for key in keys:
            await storage.write_api_key(org_a.id, key)
        newest_first = sorted(keys, key=lambda k: k.id, reverse=True)
        assert await storage.read_api_keys(org_b.id, None, limit=10) == []
        assert await storage.read_api_keys(org_b.id, None, limit=10, user_id=user_id) == []
        assert await storage.read_api_keys(org_b.id, newest_first[0].id, limit=10) == []
        assert await storage.read_api_key(org_b.id, keys[0].id) is None
        with pytest.raises(TenantMismatch):
            await storage.write_api_key(org_b.id, keys[0].model_copy(update={"name": "stolen"}))
        assert await storage.read_api_keys(org_a.id, None, limit=10) == newest_first

    async def test_a_rerun_from_another_tenant_never_re_mints_the_secret(
        self, storage: TenancyStorageInterface, markers: IdempotencyStorageInterface
    ) -> None:
        """The re-mint is one conditional write, and the tenant is one of the
        conditions in its own `WHERE`. A rerun that presents the key id of
        another tenant, holding a marker of its own, is refused and the
        stored digest stays as it was."""
        org, other = make_org("A"), make_org("B")
        api_key = make_api_key(new_id(), uuid4().hex)
        attempt_id = new_id()
        await markers.write_record(org.id, make_marker(api_key, attempt_id))
        await markers.write_record(other.id, make_marker(api_key, attempt_id))
        assert await storage.issue_api_key(
            org.id, api_key, (make_key_row(org.id, api_key),), attempt_id
        ) == (api_key, True)
        rerun = api_key.model_copy(update={"key_hash": uuid4().hex, "updated_at": utcnow()})
        with pytest.raises(Conflict):
            await storage.issue_api_key(
                other.id, rerun, (make_key_row(other.id, rerun),), attempt_id
            )
        assert await storage.read_api_key(org.id, api_key.id) == api_key
        assert await storage.read_api_key(other.id, api_key.id) is None
        assert await storage.read_api_key_by_digest(rerun.key_hash) is None

    async def test_a_socket_ticket_is_never_written_under_another_tenant(
        self, storage: TenancyStorageInterface
    ) -> None:
        """Another tenant's create of the same id is the id already written:
        it reports, and nothing lands there."""
        org, other = make_org("A"), make_org("B")
        ticket = make_socket_ticket(new_id(), uuid4().hex)
        assert await storage.create_socket_ticket(org.id, ticket)
        elsewhere = ticket.model_copy(update={"ticket_hash": uuid4().hex})
        assert not await storage.create_socket_ticket(other.id, elsewhere)
        assert await storage.redeem_socket_ticket(elsewhere.ticket_hash, utcnow()) is None
        # The redemption still names the tenant that wrote it.
        assert await storage.redeem_socket_ticket(ticket.ticket_hash, utcnow()) == (org.id, ANY)

    async def test_a_member_is_never_created_on_another_tenants_row(
        self, storage: TenancyStorageInterface
    ) -> None:
        """Both creates land whole or not at all, and an id another tenant
        holds is a taken key: the rows beside it land nothing either."""
        org, other = make_org("A"), make_org("B")
        user = make_user(make_identity().id)
        membership = make_membership(user.id)
        await storage.create_member(org.id, user, membership, (make_user_row(org.id, user),))
        with pytest.raises(UniqueKeyTaken):
            await storage.create_member(
                other.id, user, make_membership(user.id), (make_user_row(other.id, user),)
            )
        assert await storage.read_user(other.id, user.id) is None
        assert await storage.read_memberships(other.id, limit=10) == []
        with pytest.raises(UniqueKeyTaken):
            await storage.create_org_with_owner(other.id, other, user, make_membership(user.id))
        assert await storage.read_org(other.id) is None
        assert await storage.read_user(org.id, user.id) == user
        assert await storage.read_membership_for_user(org.id, user.id) == membership

    async def test_the_retention_purge_takes_every_tenants_rows_past_the_cut(
        self, storage: TenancyStorageInterface
    ) -> None:
        """The purge runs once across tenants, in the system scope, so one call
        clears every tenant's dead rows, and leaves a row not yet past the
        cut, whatever its tenant."""
        org, other = make_org("A"), make_org("B")
        cut = await drained(storage)
        long_ago = cut - timedelta(days=1)
        dead: dict[UUID, tuple[UUID, UUID]] = {}
        for tenant in (org, other):
            user = make_user(make_identity().id)
            await storage.write_user(
                tenant.id,
                user.model_copy(update={"deleted_at": long_ago, "deleted_by": user.id}),
            )
            key = make_api_key(user.id, uuid4().hex).model_copy(
                update={"deleted_at": long_ago, "deleted_by": user.id}
            )
            await storage.write_api_key(tenant.id, key)
            dead[tenant.id] = (user.id, key.id)
        recent = make_user(make_identity().id)
        await storage.write_user(
            other.id, recent.model_copy(update={"deleted_at": cut, "deleted_by": recent.id})
        )
        assert await storage.purge_deleted(cut, cut, 10) == 4
        for tenant in (org, other):
            assert await storage.read_user(tenant.id, dead[tenant.id][0]) is None
            assert await storage.read_api_key(tenant.id, dead[tenant.id][1]) is None
        assert await storage.read_user(other.id, recent.id) is not None, "not past the cut"

    async def test_update_by_copy_and_write(self, storage: TenancyStorageInterface) -> None:
        org = make_org()
        user = make_user(make_identity().id)
        await storage.write_user(org.id, user)
        renamed = user.model_copy(update={"display_name": "Renamed", "updated_at": utcnow()})
        await storage.write_user(org.id, renamed)
        assert await storage.read_user(org.id, user.id) == renamed

    async def test_lists_sort_by_uuid_value_and_clamp(
        self, storage: TenancyStorageInterface
    ) -> None:
        org = make_org()
        users = [make_user(make_identity().id) for _ in range(3)]  # one live user per identity
        for user in reversed(users):
            await storage.write_user(org.id, user)
        listed = await storage.read_users(org.id, None, limit=10)
        assert listed == sorted(users, key=lambda u: u.id)
        assert len(await storage.read_users(org.id, None, limit=2)) == 2

    async def test_the_user_list_pages_after_a_cursor(
        self, storage: TenancyStorageInterface
    ) -> None:
        """A cursor is the id the previous page ended on, and the next page
        starts strictly after it, so every member is reachable however small
        the page is."""
        org = make_org()
        users = sorted((make_user(make_identity().id) for _ in range(5)), key=lambda u: u.id)
        for user in users:
            await storage.write_user(org.id, user)
        paged: list[User] = []
        after: UUID | None = None
        while page := await storage.read_users(org.id, after, limit=2):
            paged += page
            after = page[-1].id
        assert paged == users
        assert await storage.read_users(org.id, users[-1].id, limit=10) == []

    async def test_soft_deleted_users_are_hidden_from_lists(
        self, storage: TenancyStorageInterface
    ) -> None:
        org = make_org()
        user = make_user(make_identity().id)
        await storage.write_user(org.id, user)
        gone = user.model_copy(update={"deleted_at": utcnow(), "deleted_by": user.id})
        await storage.write_user(org.id, gone)
        assert await storage.read_users(org.id, None, limit=10) == []
        assert await storage.read_user(org.id, user.id) == gone

    async def test_a_write_never_brings_a_deleted_row_back(
        self, storage: TenancyStorageInterface
    ) -> None:
        """Every update is a read, a copy, and a write of the whole entity, so
        a delete that commits between the read and the write would be undone by
        a copy still carrying `deleted_at = None`, leaving a live user with no
        live membership: listed, unable to sign in, and past every sweep. There
        is no restore in this domain, so the write is refused instead."""
        org = make_org()
        user = make_user(make_identity().id)
        await storage.write_user(org.id, user)
        held = await storage.read_user(org.id, user.id)
        assert held is not None
        gone = user.model_copy(update={"deleted_at": utcnow(), "deleted_by": user.id})
        await storage.write_user(org.id, gone)
        renamed = held.model_copy(update={"display_name": "Renamed", "updated_at": utcnow()})
        with pytest.raises(RowDeleted):
            await storage.write_user(org.id, renamed)
        assert await storage.read_user(org.id, user.id) == gone
        assert await storage.read_users(org.id, None, limit=10) == []

    async def test_one_live_user_per_identity_in_a_tenant(
        self, storage: TenancyStorageInterface
    ) -> None:
        org, other_org = make_org(), make_org("Other")
        identity = make_identity()
        user = make_user(identity.id)
        await storage.write_user(org.id, user)
        with pytest.raises(UniqueKeyTaken):
            await storage.write_user(org.id, make_user(identity.id))
        # The same identity in another tenant, and again here once the first is gone.
        await storage.write_user(other_org.id, make_user(identity.id))
        gone = user.model_copy(update={"deleted_at": utcnow(), "deleted_by": user.id})
        await storage.write_user(org.id, gone)
        await storage.write_user(org.id, make_user(identity.id))

    async def test_identity_is_global(self, storage: TenancyStorageInterface) -> None:
        identity = make_identity()
        await storage.write_identity(identity)
        assert await storage.read_identity(identity.id) == identity
        assert await storage.read_identity_by_email_digest(email_digest(identity.email)) == identity
        assert (
            await storage.read_identity_by_email_digest(email_digest("nobody@example.test")) is None
        )

    # Every unique key the schema declares has a case here, so the memory impl
    # refuses what the engine refuses: the write raises UniqueKeyTaken and the
    # row that held the key is unchanged. An update by copy of the row that
    # holds the key passes.

    async def test_identity_email_is_unique(self, storage: TenancyStorageInterface) -> None:
        email = f"{uuid4().hex}@example.test"
        identity = make_identity(email)
        await storage.write_identity(identity)
        with pytest.raises(UniqueKeyTaken):
            await storage.write_identity(make_identity(email))
        assert await storage.read_identity_by_email_digest(email_digest(email)) == identity
        promoted = identity.model_copy(update={"operator_role": OperatorRole.READ})
        await storage.write_identity(promoted)
        assert await storage.read_identity(identity.id) == promoted

    async def test_org_slug_is_unique(self, storage: TenancyStorageInterface) -> None:
        org = make_org()
        await storage.write_org(org.id, org)
        other = make_org("Other").model_copy(update={"slug": org.slug})
        with pytest.raises(UniqueKeyTaken):
            await storage.write_org(other.id, other)
        assert await storage.read_org(other.id) is None
        assert await storage.read_org_by_slug(org.slug) == org
        renamed = org.model_copy(update={"name": "Renamed"})
        await storage.write_org(org.id, renamed)
        assert await storage.read_org_by_slug(org.slug) == renamed

    async def test_one_membership_per_user_in_a_tenant(
        self, storage: TenancyStorageInterface
    ) -> None:
        org, other_org = make_org(), make_org("Other")
        membership = make_membership(new_id())
        await storage.write_membership(org.id, membership)
        with pytest.raises(UniqueKeyTaken):
            await storage.write_membership(org.id, make_membership(membership.user_id))
        assert await storage.read_memberships(org.id, limit=10) == [membership]
        # The same user id in another tenant is another key.
        await storage.write_membership(other_org.id, make_membership(membership.user_id))
        promoted = membership.model_copy(update={"role": Role.ADMIN})
        await storage.write_membership(org.id, promoted)
        assert await storage.read_membership_for_user(org.id, membership.user_id) == promoted

    # A unique key on a soft-deletable table is unique among the living: a
    # deleted row frees its key, the same value is created again, and a second
    # live row is still refused.

    async def test_a_deleted_org_frees_its_slug(self, storage: TenancyStorageInterface) -> None:
        org = make_org()
        await storage.write_org(org.id, org)
        gone = org.model_copy(update={"deleted_at": utcnow(), "deleted_by": new_id()})
        await storage.write_org(org.id, gone)
        assert await storage.read_org_by_slug(org.slug) is None
        again = make_org().model_copy(update={"slug": org.slug})
        await storage.write_org(again.id, again)
        assert await storage.read_org_by_slug(org.slug) == again
        assert await storage.read_org(org.id) == gone
        other = make_org("Other").model_copy(update={"slug": org.slug})
        with pytest.raises(UniqueKeyTaken):
            await storage.write_org(other.id, other)
        assert await storage.read_org(other.id) is None

    async def test_an_ended_membership_frees_its_user(
        self, storage: TenancyStorageInterface
    ) -> None:
        org = make_org()
        membership = make_membership(new_id())
        await storage.write_membership(org.id, membership)
        ended = membership.model_copy(update={"deleted_at": utcnow(), "deleted_by": new_id()})
        await storage.write_membership(org.id, ended)
        assert await storage.read_membership_for_user(org.id, membership.user_id) is None
        again = make_membership(membership.user_id)
        await storage.write_membership(org.id, again)
        assert await storage.read_membership_for_user(org.id, membership.user_id) == again
        with pytest.raises(UniqueKeyTaken):
            await storage.write_membership(org.id, make_membership(membership.user_id))
        assert await storage.read_memberships(org.id, limit=10) == [again]

    async def test_session_token_hash_is_unique(self, storage: TenancyStorageInterface) -> None:
        org, other_org = make_org(), make_org("Other")
        token_hash = uuid4().hex
        session = make_session(new_id(), new_id(), token_hash)
        assert await storage.create_session(org.id, session)
        with pytest.raises(UniqueKeyTaken):
            await storage.create_session(other_org.id, make_session(new_id(), new_id(), token_hash))
        with pytest.raises(UniqueKeyTaken):
            await storage.write_session(other_org.id, make_session(new_id(), new_id(), token_hash))
        assert await storage.read_session_by_digest(token_hash) == (org.id, session)
        revoked = session.model_copy(update={"revoked_at": utcnow()})
        await storage.write_session(org.id, revoked)
        assert await storage.read_session(org.id, session.id) == revoked

    async def test_api_key_hash_is_unique(self, storage: TenancyStorageInterface) -> None:
        org, other_org = make_org(), make_org("Other")
        key_hash = uuid4().hex
        api_key = make_api_key(new_id(), key_hash)
        await storage.write_api_key(org.id, api_key)
        with pytest.raises(UniqueKeyTaken):
            await storage.write_api_key(other_org.id, make_api_key(new_id(), key_hash))
        # The create refuses it too, and lands nothing under the new id.
        minted = make_api_key(new_id(), key_hash)
        with pytest.raises(UniqueKeyTaken):
            await storage.issue_api_key(org.id, minted, (make_key_row(org.id, minted),), None)
        assert await storage.read_api_key(org.id, minted.id) is None
        assert await storage.read_api_key_by_digest(key_hash) == (org.id, api_key)
        renamed = api_key.model_copy(update={"name": "renamed"})
        await storage.write_api_key(org.id, renamed)
        assert await storage.read_api_key(org.id, api_key.id) == renamed

    async def test_socket_ticket_hash_is_unique(self, storage: TenancyStorageInterface) -> None:
        org, other_org = make_org(), make_org("Other")
        ticket_hash = uuid4().hex
        ticket = make_socket_ticket(new_id(), ticket_hash)
        await storage.create_socket_ticket(org.id, ticket)
        with pytest.raises(UniqueKeyTaken):
            await storage.create_socket_ticket(
                other_org.id, make_socket_ticket(new_id(), ticket_hash)
            )
        redeemed_at = utcnow()
        assert await storage.redeem_socket_ticket(ticket_hash, redeemed_at) == (
            org.id,
            ticket.model_copy(update={"redeemed_at": redeemed_at}),
        )

    async def test_a_session_is_created_once(self, storage: TenancyStorageInterface) -> None:
        """The create is the insert that reports: a second create of the id
        says so, and the row keeps the first credential's digest, expiry, and
        holder."""
        org = make_org()
        session = make_session(new_id(), new_id(), uuid4().hex)
        assert await storage.create_session(org.id, session)
        again = make_session(new_id(), new_id(), uuid4().hex).model_copy(
            update={"id": session.id, "expires_at": session.expires_at + timedelta(days=1)}
        )
        assert not await storage.create_session(org.id, again)
        assert await storage.read_session(org.id, session.id) == session
        assert await storage.read_session_by_digest(again.token_hash) is None
        assert await storage.read_session_by_digest(session.token_hash) == (org.id, session)

    async def test_a_socket_ticket_is_created_once(self, storage: TenancyStorageInterface) -> None:
        """The create is the insert that reports: a second create of the id
        says so, and only the first ticket redeems."""
        org = make_org()
        ticket = make_socket_ticket(new_id(), uuid4().hex)
        assert await storage.create_socket_ticket(org.id, ticket)
        again = make_socket_ticket(new_id(), uuid4().hex).model_copy(update={"id": ticket.id})
        assert not await storage.create_socket_ticket(org.id, again)
        assert await storage.redeem_socket_ticket(again.ticket_hash, utcnow()) is None
        redeemed_at = utcnow()
        assert await storage.redeem_socket_ticket(ticket.ticket_hash, redeemed_at) == (
            org.id,
            ticket.model_copy(update={"redeemed_at": redeemed_at}),
        )

    async def test_create_org_with_owner_lands_whole_or_not_at_all(
        self, storage: TenancyStorageInterface
    ) -> None:
        org = make_org()
        owner = make_user(make_identity().id)
        await storage.create_org_with_owner(
            org.id, org, owner, make_membership(owner.id, Role.OWNER)
        )
        assert await storage.read_org(org.id) == org
        assert await storage.read_user(org.id, owner.id) == owner
        assert (await storage.read_membership_for_user(org.id, owner.id)) is not None
        # The slug taken meanwhile: no org, no user, no membership of the loser.
        other = make_org("Other").model_copy(update={"slug": org.slug})
        loser = make_user(make_identity().id)
        with pytest.raises(UniqueKeyTaken):
            await storage.create_org_with_owner(
                other.id, other, loser, make_membership(loser.id, Role.OWNER)
            )
        assert await storage.read_org(other.id) is None
        assert await storage.read_user(other.id, loser.id) is None
        assert await storage.read_memberships(other.id, limit=10) == []

    async def test_create_org_with_owner_lands_the_identity_in_the_same_commit(
        self, storage: TenancyStorageInterface
    ) -> None:
        # A new identity lands with its tenant; when the slug is taken it does
        # not land at all, and an existing identity is not promoted either.
        org, identity = make_org(), make_identity()
        owner = make_user(identity.id)
        await storage.create_org_with_owner(
            org.id, org, owner, make_membership(owner.id, Role.OWNER), identity
        )
        assert await storage.read_identity(identity.id) == identity
        other = make_org("Other").model_copy(update={"slug": org.slug})
        newcomer = make_identity()
        loser = make_user(newcomer.id)
        with pytest.raises(UniqueKeyTaken):
            await storage.create_org_with_owner(
                other.id, other, loser, make_membership(loser.id, Role.OWNER), newcomer
            )
        assert await storage.read_identity(newcomer.id) is None
        promoted = identity.model_copy(update={"operator_role": OperatorRole.WRITE})
        again = make_user(identity.id)
        with pytest.raises(UniqueKeyTaken):
            await storage.create_org_with_owner(
                other.id, other, again, make_membership(again.id, Role.OWNER), promoted
            )
        assert await storage.read_identity(identity.id) == identity
        # From a free slug the promotion lands with the tenant.
        free = make_org("Free")
        await storage.create_org_with_owner(
            free.id, free, again, make_membership(again.id, Role.OWNER), promoted
        )
        assert await storage.read_identity(identity.id) == promoted

    async def test_one_personal_org_per_person_among_the_living(
        self, storage: TenancyStorageInterface
    ) -> None:
        identity = make_identity()
        mine = make_personal_org(identity.id)
        owner = make_user(identity.id)
        await storage.create_org_with_owner(
            mine.id, mine, owner, make_membership(owner.id, Role.OWNER), identity
        )
        stored = await storage.read_org(mine.id)
        assert stored == mine and stored is not None and stored.personal
        # A second one for the same person is refused, and lands nothing.
        second = make_personal_org(identity.id)
        again = make_user(identity.id)
        with pytest.raises(UniqueKeyTaken):
            await storage.create_org_with_owner(
                second.id, second, again, make_membership(again.id, Role.OWNER)
            )
        assert await storage.read_org(second.id) is None
        assert await storage.read_user(second.id, again.id) is None
        # A team org of the same person is no personal org, and another
        # person's personal org is theirs.
        team = make_org("Team")
        member = make_user(identity.id)
        await storage.create_org_with_owner(
            team.id, team, member, make_membership(member.id, Role.OWNER)
        )
        other = make_identity()
        theirs = make_personal_org(other.id, "Eve")
        eve = make_user(other.id)
        await storage.create_org_with_owner(
            theirs.id, theirs, eve, make_membership(eve.id, Role.OWNER), other
        )
        # Unique among the living: a deleted one frees the person's key.
        gone = utcnow()
        await storage.write_org(
            mine.id, mine.model_copy(update={"deleted_at": gone, "deleted_by": owner.id})
        )
        await storage.create_org_with_owner(
            second.id, second, again, make_membership(again.id, Role.OWNER)
        )
        assert await storage.read_org(second.id) == second

    async def test_a_new_persons_personal_org_lands_with_the_create_or_not_at_all(
        self, storage: TenancyStorageInterface
    ) -> None:
        taken = make_org("Taken")
        first = make_user(make_identity().id)
        await storage.create_org_with_owner(
            taken.id, taken, first, make_membership(first.id, Role.OWNER)
        )
        # Two tenants in one commit, each under its own fence: a slug taken in
        # the second leaves nothing of the first, nor the identity.
        newcomer = make_identity()
        personal = make_personal_org(newcomer.id)
        mine = make_user(newcomer.id)
        own = (personal, mine, make_membership(mine.id, Role.OWNER))
        clash = make_org("Clash").model_copy(update={"slug": taken.slug})
        owner = make_user(newcomer.id)
        with pytest.raises(UniqueKeyTaken):
            await storage.create_org_with_owner(
                clash.id, clash, owner, make_membership(owner.id, Role.OWNER), newcomer, own
            )
        assert await storage.read_identity(newcomer.id) is None
        assert await storage.read_org(personal.id) is None
        assert await storage.read_user(personal.id, mine.id) is None
        # From a free slug both tenants land, and the identity with them.
        team = make_org("Team")
        await storage.create_org_with_owner(
            team.id, team, owner, make_membership(owner.id, Role.OWNER), newcomer, own
        )
        assert await storage.read_identity(newcomer.id) == newcomer
        assert await storage.read_org(personal.id) == personal
        assert await storage.read_org(team.id) == team
        assert await storage.read_user(personal.id, mine.id) == mine
        assert await storage.read_user(team.id, owner.id) == owner
        places = await storage.read_memberships_by_identity(newcomer.id, 10)
        assert {p.org.id for p in places} == {personal.id, team.id}
        # The same for a member: a new person joins with their personal org.
        joiner = make_identity()
        theirs = make_personal_org(joiner.id, "Eve")
        eve_home = make_user(joiner.id)
        eve = make_user(joiner.id)
        eve_own = (theirs, eve_home, make_membership(eve_home.id, Role.OWNER))
        with pytest.raises(UniqueKeyTaken):
            await storage.create_member(
                team.id,
                eve,
                make_membership(owner.id),
                (make_user_row(team.id, eve),),
                joiner,
                eve_own,
            )
        assert await storage.read_org(theirs.id) is None
        assert await storage.read_identity(joiner.id) is None
        await storage.create_member(
            team.id, eve, make_membership(eve.id), (make_user_row(team.id, eve),), joiner, eve_own
        )
        assert await storage.read_org(theirs.id) == theirs
        assert await storage.read_user(team.id, eve.id) == eve

    async def test_create_member_lands_whole_or_not_at_all(
        self, storage: TenancyStorageInterface
    ) -> None:
        org = make_org()
        identity = make_identity()
        bob = make_user(identity.id)
        await storage.create_member(
            org.id, bob, make_membership(bob.id), (make_user_row(org.id, bob),)
        )
        assert await storage.read_user(org.id, bob.id) == bob
        assert (await storage.read_membership_for_user(org.id, bob.id)) is not None
        # The same identity added again meanwhile: the user key refuses it and
        # the membership does not land either.
        again = make_user(identity.id)
        with pytest.raises(UniqueKeyTaken):
            await storage.create_member(
                org.id, again, make_membership(again.id), (make_user_row(org.id, again),)
            )
        assert await storage.read_user(org.id, again.id) is None
        assert await storage.read_membership_for_user(org.id, again.id) is None
        # A membership the user already holds: the user does not land either.
        cid = make_user(make_identity().id)
        with pytest.raises(UniqueKeyTaken):
            await storage.create_member(
                org.id, cid, make_membership(bob.id), (make_user_row(org.id, cid),)
            )
        assert await storage.read_user(org.id, cid.id) is None
        assert len(await storage.read_memberships(org.id, limit=10)) == 1
        # A new identity lands with the member, or not at all.
        newcomer = make_identity()
        dan = make_user(newcomer.id)
        with pytest.raises(UniqueKeyTaken):
            await storage.create_member(
                org.id, dan, make_membership(bob.id), (make_user_row(org.id, dan),), newcomer
            )
        assert await storage.read_identity(newcomer.id) is None
        await storage.create_member(
            org.id, dan, make_membership(dan.id), (make_user_row(org.id, dan),), newcomer
        )
        assert await storage.read_identity(newcomer.id) == newcomer
        assert await storage.read_user(org.id, dan.id) == dan

    async def test_remove_member_lands_whole_or_not_at_all(
        self, storage: TenancyStorageInterface
    ) -> None:
        org, other = make_org(), make_org("Other")
        bob = make_user(make_identity().id)
        membership = make_membership(bob.id)
        await storage.create_member(org.id, bob, membership, (make_user_row(org.id, bob),))
        gone = utcnow()
        removed = bob.model_copy(update={"deleted_at": gone, "deleted_by": bob.id})
        ended = membership.model_copy(update={"deleted_at": gone, "deleted_by": bob.id})
        # A membership that is not there, or a user of another tenant: nothing lands.
        revoke = revocations_by(bob.id, org.id)
        with pytest.raises(NotFound):
            await storage.remove_member(
                org.id, removed, make_membership(bob.id), (make_user_row(org.id, bob),), revoke
            )
        # The two impls name the refusal differently (see
        # docs/runbooks/tenant-isolation.md); both refuse and both land nothing.
        # A row of another tenant is refused the way a row that is not there
        # is, by both impls: the answer says nothing about whether it exists
        # under someone else.
        with pytest.raises(NotFound):
            await storage.remove_member(
                other.id, removed, ended, (make_user_row(other.id, bob),), revoke
            )
        assert await storage.read_user(org.id, bob.id) == bob
        assert await storage.read_membership_for_user(org.id, bob.id) == membership
        assert await storage.read_users(other.id, None, limit=10) == []
        assert await storage.read_memberships(other.id, limit=10) == []
        await storage.remove_member(org.id, removed, ended, (make_user_row(org.id, bob),), revoke)
        assert await storage.read_user(org.id, bob.id) == removed
        assert await storage.read_membership_for_user(org.id, bob.id) is None
        assert await storage.read_users(org.id, None, limit=10) == []

    async def test_remove_member_revokes_every_credential_of_theirs_in_the_same_commit(
        self, storage: TenancyStorageInterface, outbox: OutboxStorageInterface
    ) -> None:
        """The member, their membership, their live sessions, and their keys
        end together, each credential with the row that announces it; another
        member's credentials, and the same person's in another tenant, stay."""
        org, other = make_org(), make_org("Other")
        identity = make_identity()
        bob, cid = make_user(identity.id), make_user(make_identity().id)
        bob_elsewhere = make_user(identity.id)
        membership = make_membership(bob.id)
        await storage.create_member(org.id, bob, membership, (make_user_row(org.id, bob),))
        await storage.create_member(org.id, cid, make_membership(cid.id), ())
        await storage.create_member(other.id, bob_elsewhere, make_membership(bob_elsewhere.id), ())
        live = make_session(identity.id, bob.id, uuid4().hex)
        dead = make_session(identity.id, bob.id, uuid4().hex)
        await storage.write_session(org.id, live)
        await storage.write_session(org.id, dead.model_copy(update={"revoked_at": utcnow()}))
        key = make_api_key(bob.id, uuid4().hex)
        await storage.write_api_key(org.id, key)
        cids = make_session(cid.id, cid.id, uuid4().hex)
        await storage.write_session(org.id, cids)
        elsewhere = make_session(identity.id, bob_elsewhere.id, uuid4().hex)
        await storage.write_session(other.id, elsewhere)

        gone = utcnow()
        removed = bob.model_copy(update={"deleted_at": gone, "deleted_by": cid.id})
        ended = membership.model_copy(update={"deleted_at": gone, "deleted_by": cid.id})
        rows = await storage.remove_member(
            org.id, removed, ended, (make_user_row(org.id, bob),), revocations_by(cid.id, org.id)
        )
        assert sorted((r.kind, r.target_id) for r in rows) == sorted(
            [("tenancy.session.revoked", live.id), ("tenancy.api_key.deleted", key.id)]
        )
        revoked = await storage.read_session(org.id, live.id)
        assert revoked is not None and revoked.revoked_at == gone and revoked.updated_by == cid.id
        deleted = await storage.read_api_key(org.id, key.id)
        assert deleted is not None and (deleted.deleted_at, deleted.deleted_by) == (gone, cid.id)
        assert await storage.read_session(org.id, cids.id) == cids
        assert await storage.read_session(other.id, elsewhere.id) == elsewhere
        ours = {r.id for r in rows}
        landed = {(r.org_id, r.kind, r.target_id) for r in await claim_all(outbox) if r.id in ours}
        assert landed == {
            (org.id, "tenancy.session.revoked", live.id),
            (org.id, "tenancy.api_key.deleted", key.id),
        }

    async def test_write_closed_org_ends_every_way_in_and_nothing_elsewhere(
        self, storage: TenancyStorageInterface, outbox: OutboxStorageInterface
    ) -> None:
        """Every member, their memberships, sessions, and keys, and every
        pending invitation of the tenant end together with the org's own
        change, each ended user and credential with the row that announces
        it. The same person in another tenant keeps everything, and so does
        that tenant. Presented under another tenant, nothing lands."""
        org, other = make_org(), make_org("Other")
        await storage.write_org(org.id, org)
        await storage.write_org(other.id, other)
        identity = make_identity()
        ann = make_user(identity.id)
        bob = make_user(make_identity().id)
        ann_elsewhere = make_user(identity.id)
        await storage.create_member(org.id, ann, make_membership(ann.id, Role.OWNER), ())
        await storage.create_member(org.id, bob, make_membership(bob.id), ())
        await storage.create_member(other.id, ann_elsewhere, make_membership(ann_elsewhere.id), ())
        anns = make_session(identity.id, ann.id, uuid4().hex)
        bobs = make_session(bob.id, bob.id, uuid4().hex)
        elsewhere = make_session(identity.id, ann_elsewhere.id, uuid4().hex)
        await storage.write_session(org.id, anns)
        await storage.write_session(org.id, bobs)
        await storage.write_session(other.id, elsewhere)
        key = make_api_key(bob.id, uuid4().hex)
        await storage.write_api_key(org.id, key)
        pending = make_invitation("dee@example.test")
        theirs = make_invitation("eve@example.test")
        await storage.write_invitation(org.id, pending)
        await storage.write_invitation(other.id, theirs)

        gone = utcnow()
        closed = org.model_copy(update={"updated_at": gone, "updated_by": ann.id})

        def member_row(user: User) -> OutboxRow:
            return make_user_row(org.id, user).model_copy(update={"kind": "tenancy.user.deleted"})

        def revocation(kind: str, credential_id: UUID, user_id: UUID) -> OutboxRow:
            return revocations_by(ann.id, org.id)(kind, credential_id)

        with pytest.raises(NotFound):
            await storage.write_closed_org(other.id, closed, (), member_row, revocation)
        assert await storage.count_members(org.id) == 2
        assert await storage.read_session(org.id, anns.id) == anns

        rows = await storage.write_closed_org(org.id, closed, (), member_row, revocation)
        assert sorted((r.kind, r.target_id) for r in rows) == sorted(
            [
                ("tenancy.user.deleted", ann.id),
                ("tenancy.user.deleted", bob.id),
                ("tenancy.session.revoked", anns.id),
                ("tenancy.session.revoked", bobs.id),
                ("tenancy.api_key.deleted", key.id),
            ]
        )
        assert rows[0].kind == rows[1].kind == "tenancy.user.deleted", "the users' rows first"
        assert await storage.read_org(org.id) == closed
        assert await storage.count_members(org.id) == 0
        assert await storage.read_users(org.id, None, limit=10) == []
        for user in (ann, bob):
            ended = await storage.read_user(org.id, user.id)
            assert ended is not None and (ended.deleted_at, ended.deleted_by) == (gone, ann.id)
            assert await storage.read_membership_for_user(org.id, user.id) is None
        for session in (anns, bobs):
            revoked = await storage.read_session(org.id, session.id)
            assert revoked is not None and revoked.revoked_at == gone
        deleted = await storage.read_api_key(org.id, key.id)
        assert deleted is not None and deleted.deleted_at == gone
        revoked_invitation = await storage.read_invitation(org.id, pending.id)
        assert revoked_invitation is not None
        assert revoked_invitation.state is InvitationState.REVOKED
        # The other tenant, and the same person there, keep everything.
        assert await storage.read_session(other.id, elsewhere.id) == elsewhere
        assert await storage.read_user(other.id, ann_elsewhere.id) == ann_elsewhere
        assert await storage.read_invitation(other.id, theirs.id) == theirs
        assert await storage.count_members(other.id) == 1
        ours = {r.id for r in rows}
        landed = {(r.org_id, r.target_id) for r in await claim_all(outbox) if r.id in ours}
        assert landed == {(org.id, r.target_id) for r in rows}
        # Closed once, it is not closed again.
        again = closed.model_copy(update={"deleted_at": gone, "deleted_by": ann.id})
        await storage.write_org(org.id, again)
        with pytest.raises(NotFound):
            await storage.write_closed_org(org.id, again, (), member_row, revocation)

    async def test_a_person_is_deleted_in_every_tenant_in_one_commit(
        self, storage: TenancyStorageInterface, outbox: OutboxStorageInterface
    ) -> None:
        """The identity, every user it is (a removed one too), their
        memberships, sessions, keys, and tickets, its sign-ins and its sign-in
        delay go together; each live credential lands the row that announces
        it, and the rows handed in land under their own tenants. Another
        person in the same tenants keeps everything."""
        identity, stays = make_identity(), make_identity()
        team, personal, left = make_org("Team"), make_org("Mine"), make_org("Left")
        bob = make_user(identity.id, identity.email)
        mine = make_user(identity.id, identity.email)
        old = make_user(identity.id, identity.email)
        cid = make_user(stays.id, stays.email)
        await storage.write_identity(identity)
        await storage.write_identity(stays)
        await storage.create_member(team.id, bob, make_membership(bob.id), ())
        await storage.create_member(personal.id, mine, make_membership(mine.id, Role.OWNER), ())
        await storage.create_member(left.id, old, make_membership(old.id), ())
        await storage.write_user(
            left.id, old.model_copy(update={"deleted_at": utcnow(), "deleted_by": old.id})
        )
        cids = make_membership(cid.id)
        await storage.create_member(team.id, cid, cids, ())
        live = make_session(identity.id, bob.id, uuid4().hex)
        dead = make_session(identity.id, bob.id, uuid4().hex).model_copy(
            update={"revoked_at": utcnow()}
        )
        sign_in = make_sign_in(identity.id, uuid4().hex)
        key = make_api_key(mine.id, uuid4().hex)
        ticket = make_socket_ticket(bob.id, uuid4().hex)
        cid_session = make_session(stays.id, cid.id, uuid4().hex)
        cid_key = make_api_key(cid.id, uuid4().hex)
        cid_ticket = make_socket_ticket(cid.id, uuid4().hex)
        await storage.write_api_key(team.id, cid_key)
        await storage.create_socket_ticket(team.id, cid_ticket)
        # An invitation to the person's address, one they accepted, and one
        # to somebody else.
        sent = make_invitation(identity.email)
        accepted = make_invitation("bob.old@example.test").model_copy(
            update={"state": InvitationState.ACCEPTED, "accepted_user_id": bob.id}
        )
        other = make_invitation("dee@example.test")
        await storage.write_invitation(left.id, sent)
        await storage.write_invitation(team.id, accepted)
        await storage.write_invitation(team.id, other)
        await storage.write_session(team.id, live)
        await storage.write_session(team.id, dead)
        await storage.write_session(EMPTY_UUID, sign_in)
        await storage.write_session(team.id, cid_session)
        await storage.write_api_key(personal.id, key)
        await storage.create_socket_ticket(team.id, ticket)
        await storage.record_failed_sign_in(email_digest(identity.email), utcnow())
        await storage.record_failed_sign_in(email_digest(stays.email), utcnow())

        removal = make_user_row(team.id, bob)

        def revocation(org_id: UUID, kind: str, credential_id: UUID) -> OutboxRow:
            return revocations_by(EMPTY_UUID, org_id)(kind, credential_id)

        rows = await storage.delete_person(identity.id, identity.email, (), (removal,), revocation)
        assert sorted((r.org_id, r.kind, r.target_id) for r in rows) == sorted(
            [
                (team.id, "tenancy.session.revoked", live.id),
                (personal.id, "tenancy.api_key.deleted", key.id),
            ]
        )
        assert await storage.read_identity(identity.id) is None
        assert await storage.read_sign_in_delay(email_digest(identity.email)) is None
        for org_id, user in ((team.id, bob), (personal.id, mine), (left.id, old)):
            assert await storage.read_user(org_id, user.id) is None
            assert await storage.read_membership_for_user(org_id, user.id) is None
        for org_id, session_id in ((team.id, live.id), (team.id, dead.id)):
            assert await storage.read_session(org_id, session_id) is None
        assert await storage.read_session_by_id(sign_in.id) is None
        assert await storage.read_api_key(personal.id, key.id) is None
        assert await storage.redeem_socket_ticket(ticket.ticket_hash, utcnow()) is None
        # Everyone else keeps what they had.
        assert await storage.read_identity(stays.id) == stays
        assert await storage.read_sign_in_delay(email_digest(stays.email)) is not None
        assert await storage.read_user(team.id, cid.id) == cid
        assert await storage.read_membership_for_user(team.id, cid.id) == cids
        assert await storage.read_session(team.id, cid_session.id) == cid_session
        assert await storage.read_api_key(team.id, cid_key.id) == cid_key
        assert await storage.read_invitation(left.id, sent.id) is None
        assert await storage.read_invitation(team.id, accepted.id) is None
        assert await storage.read_invitation(team.id, other.id) == other
        assert await storage.redeem_socket_ticket(cid_ticket.ticket_hash, utcnow()) is not None
        ours = {removal.id} | {r.id for r in rows}
        landed = {(r.org_id, r.kind, r.target_id) for r in await claim_all(outbox) if r.id in ours}
        assert landed == {
            (team.id, "tenancy.user.created", bob.id),
            (team.id, "tenancy.session.revoked", live.id),
            (personal.id, "tenancy.api_key.deleted", key.id),
        }
        # Gone already: nothing lands.
        with pytest.raises(NotFound):
            again = (make_user_row(team.id, bob),)
            await storage.delete_person(identity.id, identity.email, (), again, revocation)

    async def test_an_owner_who_leaves_never_leaves_a_tenant_with_none(
        self, storage: TenancyStorageInterface
    ) -> None:
        """The two owners of a tenant leave at once: the owner rows are locked,
        or the memory lock is held, so one goes and the other is refused with
        nothing landed."""
        org = make_org("Team")
        people = [make_identity(), make_identity()]
        users = [make_user(p.id, p.email) for p in people]
        for person, user in zip(people, users, strict=True):
            await storage.write_identity(person)
            await storage.create_member(org.id, user, make_membership(user.id, Role.OWNER), ())

        def revocation(org_id: UUID, kind: str, credential_id: UUID) -> OutboxRow:
            return revocations_by(EMPTY_UUID, org_id)(kind, credential_id)

        async def leave(person: Identity) -> Identity | None:
            try:
                await storage.delete_person(person.id, person.email, (org.id,), (), revocation)
            except Conflict:
                return None
            return person

        run = await race(*(leave(person) for person in people))
        assert len(run.admitted) == 1, run.summary()
        [stayed] = [p for p in people if p not in run.admitted]
        assert await storage.read_identity(stayed.id) == stayed
        assert await storage.count_members(org.id, Role.OWNER) == 1

    async def test_users_by_identity_span_tenants(self, storage: TenancyStorageInterface) -> None:
        identity = make_identity()
        org_a, org_b = make_org("A"), make_org("B")
        user_a, user_b = make_user(identity.id), make_user(identity.id)
        await storage.write_user(org_a.id, user_a)
        await storage.write_user(org_b.id, user_b)
        found = await storage.read_users_by_identity(identity.id, limit=10)
        expected = sorted([(org_a.id, user_a), (org_b.id, user_b)], key=lambda pair: pair[1].id)
        assert found == expected, "by user id"
        # The bound is in the statement, and both impls cut at the same row.
        assert await storage.read_users_by_identity(identity.id, limit=1) == expected[:1]

    async def test_create_org_with_owner_refuses_a_new_identity_on_a_held_email(
        self, storage: TenancyStorageInterface
    ) -> None:
        """A sign-up always brings a new identity; one whose email another
        identity holds meets the unique key, and neither it nor its tenant lands."""
        email = f"{uuid4().hex}@example.test"
        held = make_identity(email)
        await storage.write_identity(held)
        newcomer = make_identity(email)
        org = make_org()
        owner = make_user(newcomer.id, email)
        with pytest.raises(UniqueKeyTaken):
            await storage.create_org_with_owner(
                org.id, org, owner, make_membership(owner.id, Role.OWNER), newcomer
            )
        assert await storage.read_identity(newcomer.id) is None
        assert await storage.read_org(org.id) is None
        assert await storage.read_user(org.id, owner.id) is None
        assert await storage.read_identity_by_email_digest(email_digest(email)) == held

    async def test_the_memberships_of_an_identity_span_tenants_and_skip_the_gone(
        self, storage: TenancyStorageInterface
    ) -> None:
        identity = make_identity()
        places = []
        for name in ("A", "B", "C", "D"):
            org = make_org(name)
            user = make_user(identity.id)
            role = Role.OWNER if name == "A" else Role.MEMBER
            await storage.create_org_with_owner(org.id, org, user, make_membership(user.id, role))
            places.append((org, user, role))
        # Someone else's place is never listed.
        stranger_org = make_org("E")
        stranger = make_user(make_identity().id)
        await storage.create_org_with_owner(
            stranger_org.id, stranger_org, stranger, make_membership(stranger.id, Role.OWNER)
        )
        # A deleted org, and a removed user, are places nobody holds.
        gone_org, _, _ = places[1]
        await storage.write_org(
            gone_org.id,
            gone_org.model_copy(update={"deleted_at": utcnow(), "deleted_by": new_id()}),
        )
        left_org, left_user, _ = places[2]
        ended = await storage.read_membership_for_user(left_org.id, left_user.id)
        assert ended is not None
        now = utcnow()
        await storage.remove_member(
            left_org.id,
            left_user.model_copy(update={"deleted_at": now, "deleted_by": new_id()}),
            ended.model_copy(update={"deleted_at": now, "deleted_by": new_id()}),
            (),
            revocations_by(left_user.id, left_org.id),
        )
        live = sorted((places[0], places[3]), key=lambda place: place[1].id)
        found = await storage.read_memberships_by_identity(identity.id, limit=10)
        assert [(m.org.id, m.user.id, m.role) for m in found] == [
            (org.id, user.id, role) for org, user, role in live
        ], "the living, by user id"
        assert found[0].org == live[0][0] and found[0].user == live[0][1]
        # The bound and the cursor cut at the same rows in both impls.
        first = await storage.read_memberships_by_identity(identity.id, limit=1)
        assert first == found[:1]
        rest = await storage.read_memberships_by_identity(identity.id, 10, first[0].user.id)
        assert rest == found[1:]

    async def test_a_session_is_found_by_id_across_tenants(
        self, storage: TenancyStorageInterface
    ) -> None:
        org = make_org()
        session = make_session(new_id(), new_id(), uuid4().hex)
        await storage.write_session(org.id, session)
        assert await storage.read_session_by_id(session.id) == (org.id, session)
        assert await storage.read_session_by_id(new_id()) is None

    async def test_a_session_keeps_the_provider_session_behind_it(
        self, storage: TenancyStorageInterface
    ) -> None:
        org = make_org()
        session = make_session(new_id(), new_id(), uuid4().hex).model_copy(
            update={"provider_session_id": "session_01K5PROVIDER"}
        )
        await storage.write_session(org.id, session)
        found = await storage.read_session_by_digest(session.token_hash)
        assert found is not None and found[1].provider_session_id == "session_01K5PROVIDER"

    async def test_replace_session_ends_one_and_lands_the_other_in_one_commit(
        self, storage: TenancyStorageInterface, outbox: OutboxStorageInterface
    ) -> None:
        org_a, org_b = make_org("A"), make_org("B")
        identity_id = new_id()
        old = make_session(identity_id, new_id(), uuid4().hex)
        await storage.write_session(org_a.id, old)
        new = make_session(identity_id, new_id(), uuid4().hex)
        ended = old.model_copy(update={"revoked_at": utcnow()})
        row = make_session_row(org_a.id, ended)
        await storage.replace_session(org_b.id, new, org_a.id, ended, (row,))
        assert await storage.read_session(org_a.id, old.id) == ended
        assert await storage.read_session(org_b.id, new.id) == new
        assert await storage.read_session_by_digest(new.token_hash) == (org_b.id, new)
        landed = [r for r in await claim_all(outbox) if r.target_id == old.id]
        assert [(r.id, r.org_id) for r in landed] == [(row.id, org_a.id)]
        # An ended session cannot be ended again: two switches admit one.
        again = make_session(identity_id, new_id(), uuid4().hex)
        with pytest.raises(Conflict):
            await storage.replace_session(org_b.id, again, org_a.id, ended, ())
        assert await storage.read_session(org_b.id, again.id) is None

    async def test_two_replacements_of_one_session_admit_one(
        self, storage: TenancyStorageInterface
    ) -> None:
        """Two tabs switching on one session at once: the row lock, or the memory
        lock, lets one through and the other finds the session ended."""
        org_a, org_b = make_org("A"), make_org("B")
        old = make_session(new_id(), new_id(), uuid4().hex)
        await storage.write_session(org_a.id, old)
        ended = old.model_copy(update={"revoked_at": utcnow()})
        candidates = [make_session(old.identity_id, new_id(), uuid4().hex) for _ in range(3)]

        async def switch(new: Session) -> Session | None:
            try:
                await storage.replace_session(org_b.id, new, org_a.id, ended, ())
            except Conflict:
                return None
            return new

        run = await race(*(switch(new) for new in candidates))
        assert len(run.admitted) == 1, run.summary()
        landed = [await storage.read_session(org_b.id, new.id) for new in candidates]
        assert [s for s in landed if s is not None] == run.admitted

    async def test_replace_session_never_ends_another_tenants_session(
        self, storage: TenancyStorageInterface
    ) -> None:
        """The ended session is named with its tenant; named with another one,
        nothing is ended and nothing lands."""
        org_a, org_b = make_org("A"), make_org("B")
        old = make_session(new_id(), new_id(), uuid4().hex)
        await storage.write_session(org_a.id, old)
        new = make_session(old.identity_id, new_id(), uuid4().hex)
        ended = old.model_copy(update={"revoked_at": utcnow()})
        with pytest.raises(NotFound):
            await storage.replace_session(org_b.id, new, org_b.id, ended, ())
        assert await storage.read_session(org_a.id, old.id) == old
        assert await storage.read_session(org_b.id, new.id) is None
        assert await storage.read_session_by_digest(new.token_hash) is None

    async def test_replace_session_lands_nothing_when_the_new_token_is_taken(
        self, storage: TenancyStorageInterface
    ) -> None:
        org_a, org_b = make_org("A"), make_org("B")
        taken = make_session(new_id(), new_id(), uuid4().hex)
        await storage.write_session(org_b.id, taken)
        old = make_session(new_id(), new_id(), uuid4().hex)
        await storage.write_session(org_a.id, old)
        new = make_session(old.identity_id, new_id(), taken.token_hash)
        ended = old.model_copy(update={"revoked_at": utcnow()})
        with pytest.raises(UniqueKeyTaken):
            await storage.replace_session(org_b.id, new, org_a.id, ended, ())
        assert await storage.read_session(org_a.id, old.id) == old, "the old one still lives"
        assert await storage.read_session(org_b.id, new.id) is None

    async def test_exchange_sign_in_ends_it_and_lands_the_session_in_one_commit(
        self, storage: TenancyStorageInterface
    ) -> None:
        org = make_org()
        sign_in = make_sign_in(new_id(), uuid4().hex)
        await storage.write_session(EMPTY_UUID, sign_in)
        new = make_session(sign_in.identity_id, new_id(), uuid4().hex)
        ended = sign_in.model_copy(update={"revoked_at": utcnow()})
        await storage.exchange_sign_in(org.id, new, ended)
        assert await storage.read_session_by_id(sign_in.id) == (EMPTY_UUID, ended)
        assert await storage.read_session(org.id, new.id) == new
        assert await storage.read_session_by_digest(new.token_hash) == (org.id, new)
        # An ended sign-in cannot be exchanged again: a sign-in makes one session.
        again = make_session(sign_in.identity_id, new_id(), uuid4().hex)
        with pytest.raises(Conflict):
            await storage.exchange_sign_in(org.id, again, ended)
        assert await storage.read_session(org.id, again.id) is None
        assert await storage.read_session_by_digest(again.token_hash) is None

    async def test_two_exchanges_of_one_sign_in_admit_one(
        self, storage: TenancyStorageInterface
    ) -> None:
        """Clicks the browser delivered together, or a replayed request: the
        row lock, or the memory lock, lets one through and the others find
        the sign-in ended."""
        org = make_org()
        sign_in = make_sign_in(new_id(), uuid4().hex)
        await storage.write_session(EMPTY_UUID, sign_in)
        ended = sign_in.model_copy(update={"revoked_at": utcnow()})
        candidates = [make_session(sign_in.identity_id, new_id(), uuid4().hex) for _ in range(3)]

        async def exchange(new: Session) -> Session | None:
            try:
                await storage.exchange_sign_in(org.id, new, ended)
            except Conflict:
                return None
            return new

        run = await race(*(exchange(new) for new in candidates))
        assert len(run.admitted) == 1, run.summary()
        landed = [await storage.read_session(org.id, new.id) for new in candidates]
        assert [s for s in landed if s is not None] == run.admitted

    async def test_exchange_sign_in_never_ends_a_tenants_session(
        self, storage: TenancyStorageInterface
    ) -> None:
        """Only a row of the system scope is a sign-in. A tenant's session
        named in its place is not found there: nothing is ended and nothing
        lands, in that tenant or in another."""
        org_a, org_b = make_org("A"), make_org("B")
        held = make_session(new_id(), new_id(), uuid4().hex)
        await storage.write_session(org_a.id, held)
        new = make_session(held.identity_id, new_id(), uuid4().hex)
        ended = held.model_copy(update={"revoked_at": utcnow()})
        for org in (org_a, org_b):
            with pytest.raises(NotFound):
                await storage.exchange_sign_in(org.id, new, ended)
            assert await storage.read_session(org.id, new.id) is None
        assert await storage.read_session(org_a.id, held.id) == held
        assert await storage.read_session_by_digest(new.token_hash) is None

    async def test_exchange_sign_in_ends_nothing_when_the_new_token_is_taken(
        self, storage: TenancyStorageInterface
    ) -> None:
        org = make_org()
        taken = make_session(new_id(), new_id(), uuid4().hex)
        await storage.write_session(org.id, taken)
        sign_in = make_sign_in(new_id(), uuid4().hex)
        await storage.write_session(EMPTY_UUID, sign_in)
        new = make_session(sign_in.identity_id, new_id(), taken.token_hash)
        ended = sign_in.model_copy(update={"revoked_at": utcnow()})
        with pytest.raises(UniqueKeyTaken):
            await storage.exchange_sign_in(org.id, new, ended)
        assert await storage.read_session_by_id(sign_in.id) == (EMPTY_UUID, sign_in), "still live"
        assert await storage.read_session(org.id, new.id) is None

    async def test_exchange_sign_in_lands_an_operator_token_in_the_system_scope(
        self, storage: TenancyStorageInterface
    ) -> None:
        """A sign-in with its second factor mints one operator token: the
        token lands beside the sign-in, in the system scope, and the sign-in
        ends in the same commit, so a second mint with it lands nothing."""
        sign_in = make_sign_in(new_id(), uuid4().hex)
        await storage.write_session(EMPTY_UUID, sign_in)
        token = make_operator_token(sign_in.identity_id, uuid4().hex)
        ended = sign_in.model_copy(update={"revoked_at": utcnow()})
        await storage.exchange_sign_in(EMPTY_UUID, token, ended)
        assert await storage.read_session_by_id(sign_in.id) == (EMPTY_UUID, ended)
        assert await storage.read_session_by_digest(token.token_hash) == (EMPTY_UUID, token)
        again = make_operator_token(sign_in.identity_id, uuid4().hex)
        with pytest.raises(Conflict):
            await storage.exchange_sign_in(EMPTY_UUID, again, ended)
        assert await storage.read_session_by_digest(again.token_hash) is None

    async def test_an_identitys_live_operator_tokens_are_read_newest_first(
        self, storage: TenancyStorageInterface
    ) -> None:
        """Only the one identity's, only tokens, only live ones, a page at a
        time: a revoked or expired token, a sign-in, a session of the same
        person, and another operator's token are none of them."""
        identity_id, other_id = new_id(), new_id()
        live = [make_operator_token(identity_id, uuid4().hex) for _ in range(3)]
        revoked = make_operator_token(identity_id, uuid4().hex).model_copy(
            update={"revoked_at": utcnow()}
        )
        expired = make_operator_token(identity_id, uuid4().hex, ttl=timedelta(seconds=-1))
        others = [
            revoked,
            expired,
            make_sign_in(identity_id, uuid4().hex),
            make_operator_token(other_id, uuid4().hex),
        ]
        for row in (*live, *others):
            await storage.write_session(EMPTY_UUID, row)
        await storage.write_session(new_id(), make_session(identity_id, new_id(), uuid4().hex))
        now = utcnow()
        newest_first = live[::-1]
        assert await storage.read_operator_tokens(identity_id, now, None, 10) == newest_first
        first = await storage.read_operator_tokens(identity_id, now, None, 2)
        assert first == newest_first[:2]
        rest = await storage.read_operator_tokens(identity_id, now, first[-1].id, 2)
        assert rest == newest_first[2:]
        assert await storage.read_operator_tokens(new_id(), now, None, 10) == []

    async def test_disabling_an_operator_ends_its_operator_credentials_in_one_commit(
        self, storage: TenancyStorageInterface, outbox: OutboxStorageInterface
    ) -> None:
        """The entry, its audit row, and the end of every live token and every
        sign-in that verified a second factor. A plain sign-in and a tenant
        session of the same person are theirs, not the operator's, and stay."""
        identity = make_identity(operator_role=OperatorRole.WRITE)
        await storage.write_identity(identity)
        now = utcnow()
        tokens = [make_operator_token(identity.id, uuid4().hex) for _ in range(2)]
        verified = make_sign_in(identity.id, uuid4().hex).model_copy(
            update={"second_factor_at": now}
        )
        plain = make_sign_in(identity.id, uuid4().hex)
        expired = make_operator_token(identity.id, uuid4().hex, ttl=timedelta(seconds=-1))
        someone_elses = make_operator_token(new_id(), uuid4().hex)
        for row in (*tokens, verified, plain, expired, someone_elses):
            await storage.write_session(EMPTY_UUID, row)
        org = make_org()
        session = make_session(identity.id, new_id(), uuid4().hex)
        await storage.write_session(org.id, session)
        disabled = identity.model_copy(update={"operator_role": None})
        row = OutboxRow(
            id=new_id(),
            created_at=now,
            org_id=EMPTY_UUID,
            kind="tenancy.operator.disabled",
            target_id=identity.id,
            payload={},
            actor_id=EMPTY_UUID,
            request_id=new_id(),
            app="cli",
        )
        assert await storage.disable_operator(disabled, (row,), now) == 3
        assert await storage.read_identity(identity.id) == disabled
        assert [r.org_id for r in await claim_all(outbox) if r.id == row.id] == [EMPTY_UUID]
        for ended in (*tokens, verified):
            found = await storage.read_session_by_id(ended.id)
            assert found is not None and found[1].revoked_at == now, ended.credential_kind
            assert found[1].updated_by == EMPTY_UUID
        for kept in (plain, expired, someone_elses):
            assert await storage.read_session_by_id(kept.id) == (EMPTY_UUID, kept)
        assert await storage.read_session(org.id, session.id) == session
        assert await storage.read_operator_tokens(identity.id, now, None, 10) == []
        # Nothing is left to end; a gone identity is NotFound and lands nothing.
        assert await storage.disable_operator(disabled, (), now) == 0
        with pytest.raises(NotFound):
            await storage.disable_operator(make_identity(), (), now)

    async def test_membership_for_user(self, storage: TenancyStorageInterface) -> None:
        org = make_org()
        membership = make_membership(new_id())
        await storage.write_membership(org.id, membership)
        assert await storage.read_membership_for_user(org.id, membership.user_id) == membership
        assert await storage.read_membership_for_user(org.id, new_id()) is None
        assert await storage.read_memberships(org.id, limit=5) == [membership]

    async def test_the_membership_list_pages_by_user_id(
        self, storage: TenancyStorageInterface
    ) -> None:
        """Memberships are listed by user id, not by their own id, so a page of
        them covers the members a page of users ending on the same id does.
        The user ids here are minted newest first, against the membership ids."""
        org = make_org()
        user_ids = [new_id() for _ in range(5)][::-1]
        memberships = [make_membership(user_id) for user_id in user_ids]
        for membership in memberships:
            await storage.write_membership(org.id, membership)
        by_user = sorted(memberships, key=lambda m: m.user_id)
        paged = []
        after: UUID | None = None
        while page := await storage.read_memberships(org.id, 2, after):
            paged += page
            after = page[-1].user_id
        assert paged == by_user
        assert await storage.read_memberships(org.id, 10, by_user[-1].user_id) == []

    async def test_an_ended_membership_is_hidden_from_reads_and_purged_past_retention(
        self, storage: TenancyStorageInterface
    ) -> None:
        org = make_org()
        cut = await drained(storage)
        live, ended = make_membership(new_id()), make_membership(new_id())
        await storage.write_membership(org.id, live)
        await storage.write_membership(org.id, ended)
        await storage.write_membership(
            org.id,
            ended.model_copy(update={"deleted_at": cut - timedelta(days=1), "deleted_by": live.id}),
        )
        assert await storage.read_memberships(org.id, limit=10) == [live]
        assert await storage.read_membership_for_user(org.id, ended.user_id) is None
        assert await storage.read_membership_for_user(org.id, live.user_id) == live
        assert await storage.purge_deleted(cut, cut, 10) == 1  # the ended membership
        assert await storage.read_memberships(org.id, limit=10) == [live]
        assert await storage.purge_deleted(cut, cut, 10) == 0

    async def test_a_session_write_lands_its_outbox_row_beside_it(
        self, storage: TenancyStorageInterface
    ) -> None:
        """A revocation is a session write with a handoff: the row and its
        outbox row land together, as a user's or a key's do."""
        org = make_org()
        identity = make_identity()
        user = make_user(identity.id)
        session = make_session(
            identity.id, user.id, uuid4().hex
        )  # unique across runs of a shared database
        await storage.write_session(org.id, session)
        revoked = session.model_copy(update={"revoked_at": utcnow()})
        await storage.write_session(org.id, revoked, (make_session_row(org.id, revoked),))
        assert await storage.read_session(org.id, session.id) == revoked

    async def test_session_lookup_by_hash_returns_the_tenant(
        self, storage: TenancyStorageInterface
    ) -> None:
        org = make_org()
        token_hash = uuid4().hex
        session = make_session(new_id(), new_id(), token_hash)
        await storage.write_session(org.id, session)
        assert await storage.read_session_by_digest(token_hash) == (org.id, session)
        assert await storage.read_session(org.id, session.id) == session
        assert await storage.read_session_by_digest("missing") is None

    async def test_a_credential_is_read_with_the_identity_it_proves(
        self, storage: TenancyStorageInterface
    ) -> None:
        """The digest lookup and the identity behind the credential in one
        read, whether the credential is the system scope's or a tenant's; a
        credential whose identity is gone reads with none."""
        identity = make_identity()
        await storage.write_identity(identity)
        sign_in = make_sign_in(identity.id, uuid4().hex)
        await storage.write_session(EMPTY_UUID, sign_in)
        org = make_org()
        session = make_session(identity.id, new_id(), uuid4().hex)
        await storage.write_session(org.id, session)
        orphan = make_sign_in(new_id(), uuid4().hex)
        await storage.write_session(EMPTY_UUID, orphan)
        assert await storage.read_session_with_identity_by_digest(sign_in.token_hash) == (
            EMPTY_UUID,
            sign_in,
            identity,
        )
        assert await storage.read_session_with_identity_by_digest(session.token_hash) == (
            org.id,
            session,
            identity,
        )
        assert await storage.read_session_with_identity_by_digest(orphan.token_hash) == (
            EMPTY_UUID,
            orphan,
            None,
        )
        assert await storage.read_session_with_identity_by_digest("missing") is None

    async def test_sessions_of_a_user_are_the_live_ones_newest_first(
        self, storage: TenancyStorageInterface
    ) -> None:
        org = make_org()
        identity_id, user_id = new_id(), new_id()
        now = utcnow()
        sessions = [make_session(identity_id, user_id, uuid4().hex) for _ in range(3)]
        for session in reversed(sessions):
            await storage.write_session(org.id, session)
        await storage.write_session(org.id, make_session(identity_id, new_id(), uuid4().hex))
        listed = await storage.read_sessions(org.id, user_id, now, limit=10)
        assert listed == sorted(sessions, key=lambda s: s.id, reverse=True)
        assert len(await storage.read_sessions(org.id, user_id, now, limit=2)) == 2
        revoked = sessions[0].model_copy(update={"revoked_at": now})
        await storage.write_session(org.id, revoked)
        assert revoked not in await storage.read_sessions(org.id, user_id, now, limit=10)
        assert await storage.read_session(org.id, revoked.id) == revoked
        # Expiry is the storage's filter too, before the clamp: a page of dead
        # sessions never hides a live one.
        expired = [
            make_session(identity_id, user_id, uuid4().hex, ttl=timedelta(seconds=-1))
            for _ in range(3)
        ]
        for session in expired:
            await storage.write_session(org.id, session)
        live = make_session(identity_id, user_id, uuid4().hex)
        await storage.write_session(org.id, live)
        assert await storage.read_sessions(org.id, user_id, utcnow(), limit=1) == [live]
        assert await storage.read_sessions(org.id, user_id, now - timedelta(hours=2), limit=10) == [
            live,
            *expired[::-1],
            sessions[2],
            sessions[1],
        ]

    async def test_api_keys_are_listed_newest_first_and_filtered_by_owner_before_the_clamp(
        self, storage: TenancyStorageInterface
    ) -> None:
        org = make_org()
        ann, bob = new_id(), new_id()
        anns = [make_api_key(ann, uuid4().hex) for _ in range(2)]
        bobs = make_api_key(bob, uuid4().hex)
        for key in (*anns, bobs):
            await storage.write_api_key(org.id, key)
        assert await storage.read_api_keys(org.id, None, limit=10) == [bobs, anns[1], anns[0]]
        assert await storage.read_api_keys(org.id, None, limit=2) == [bobs, anns[1]]
        assert await storage.read_api_keys(org.id, None, limit=1, user_id=ann) == [anns[1]]
        assert await storage.read_api_keys(org.id, None, limit=10, user_id=bob) == [bobs]
        assert await storage.read_api_keys(org.id, None, limit=10, user_id=new_id()) == []

    async def test_the_api_key_list_pages_after_a_cursor(
        self, storage: TenancyStorageInterface
    ) -> None:
        """The key list reads newest first, so its next page is what sorts
        below the cursor; the owner filter travels with it."""
        org = make_org()
        ann = new_id()
        anns = [make_api_key(ann, uuid4().hex) for _ in range(5)]
        for key in anns:
            await storage.write_api_key(org.id, key)
        newest_first = sorted(anns, key=lambda k: k.id, reverse=True)
        paged: list[ApiKey] = []
        after: UUID | None = None
        while page := await storage.read_api_keys(org.id, after, limit=2):
            paged += page
            after = page[-1].id
        assert paged == newest_first
        assert await storage.read_api_keys(org.id, newest_first[-1].id, limit=10) == []
        assert (
            await storage.read_api_keys(org.id, newest_first[0].id, limit=2, user_id=ann)
            == (newest_first[1:3])
        )

    async def test_issue_api_key_creates_once_and_reissues_the_secret_on_a_rerun(
        self, storage: TenancyStorageInterface, markers: IdempotencyStorageInterface
    ) -> None:
        org = make_org()
        user_id = new_id()
        first_hash, second_hash = uuid4().hex, uuid4().hex
        api_key = make_api_key(user_id, first_hash)
        attempt_id = new_id()
        await markers.write_record(org.id, make_marker(api_key, attempt_id))
        assert await storage.issue_api_key(
            org.id, api_key, (make_key_row(org.id, api_key),), attempt_id
        ) == (
            api_key,
            True,
        )
        # The rerun presents the same id with a new digest, and a name and a
        # clock of its own; only the digest (and the update stamp) lands. The
        # marker still holds the attempt making it, so it is admitted.
        later = utcnow() + timedelta(seconds=1)
        rerun = api_key.model_copy(
            update={"key_hash": second_hash, "name": "renamed", "updated_at": later}
        )
        stored, created = await storage.issue_api_key(
            org.id, rerun, (make_key_row(org.id, rerun),), attempt_id
        )
        assert created is False
        assert (stored.id, stored.name, stored.created_at) == (
            api_key.id,
            api_key.name,
            api_key.created_at,
        )
        assert (stored.key_hash, stored.updated_at) == (second_hash, later)
        assert await storage.read_api_key(org.id, api_key.id) == stored
        assert await storage.read_api_key_by_digest(first_hash) is None
        assert await storage.read_api_key_by_digest(second_hash) == (org.id, stored)
        # Another issuer presenting the id is refused and changes nothing.
        other = rerun.model_copy(update={"user_id": new_id(), "key_hash": uuid4().hex})
        with pytest.raises(Conflict):
            await storage.issue_api_key(org.id, other, (make_key_row(org.id, other),), attempt_id)
        assert await storage.read_api_key(org.id, api_key.id) == stored
        # A request that carried no key holds no marker, so it never re-mints.
        keyless = rerun.model_copy(update={"key_hash": uuid4().hex})
        with pytest.raises(Conflict):
            await storage.issue_api_key(org.id, keyless, (make_key_row(org.id, keyless),), None)
        assert await storage.read_api_key(org.id, api_key.id) == stored

    async def test_a_rerun_never_re_mints_a_revoked_key(
        self, storage: TenancyStorageInterface, markers: IdempotencyStorageInterface
    ) -> None:
        """A create retried after the key was revoked must not put a live
        secret back on a dead row and answer 201 with it, marker or no
        marker."""
        org = make_org()
        user_id = new_id()
        api_key = make_api_key(user_id, uuid4().hex)
        attempt_id = new_id()
        await markers.write_record(org.id, make_marker(api_key, attempt_id))
        await storage.issue_api_key(org.id, api_key, (make_key_row(org.id, api_key),), attempt_id)
        revoked = api_key.model_copy(update={"deleted_at": utcnow(), "deleted_by": user_id})
        await storage.write_api_key(org.id, revoked)
        rerun = api_key.model_copy(update={"key_hash": uuid4().hex, "updated_at": utcnow()})
        with pytest.raises(Conflict):
            await storage.issue_api_key(org.id, rerun, (make_key_row(org.id, rerun),), attempt_id)
        assert await storage.read_api_key(org.id, api_key.id) == revoked
        assert await storage.read_api_key_by_digest(rerun.key_hash) is None

    async def test_a_rerun_never_re_mints_a_key_the_marker_no_longer_holds(
        self, storage: TenancyStorageInterface, markers: IdempotencyStorageInterface
    ) -> None:
        """An attempt that ran past the idempotency marker's pending lease is a
        zombie: the retry that took the marker over already handed its key to
        the caller, and the zombie's rerun must not overwrite it. Both attempts
        stamped the same `created_at`, which is why no ordering can tell them
        apart and only the marker can."""
        org = make_org()
        user_id = new_id()
        stalled_attempt, winning_attempt = new_id(), new_id()
        stalled = make_api_key(user_id, uuid4().hex)
        winner = stalled.model_copy(update={"key_hash": uuid4().hex})
        assert winner.created_at == stalled.created_at
        marker = make_marker(stalled, stalled_attempt)
        await markers.write_record(org.id, marker)
        # The first attempt lands the row, then stalls before its outcome.
        await storage.issue_api_key(
            org.id, stalled, (make_key_row(org.id, stalled),), stalled_attempt
        )
        # The retry takes the marker over past the lease and reruns on the same
        # id: its re-mint is admitted, and its secret is the one the caller has.
        taken = await markers.take_over_pending(
            org.id, user_id, marker.key, lease_bound(utcnow()), winning_attempt
        )
        assert taken is not None
        await storage.issue_api_key(
            org.id, winner, (make_key_row(org.id, winner),), winning_attempt
        )
        # The zombie wakes. Nothing about the two rows can be ordered; the
        # marker it no longer holds is what refuses it.
        with pytest.raises(Conflict):
            await storage.issue_api_key(
                org.id, stalled, (make_key_row(org.id, stalled),), stalled_attempt
            )
        live = await storage.read_api_key_by_digest(winner.key_hash)
        assert live is not None and live[1].id == winner.id
        assert await storage.read_api_key_by_digest(stalled.key_hash) is None

    async def test_a_rerun_whose_clock_ran_ahead_is_refused_too(
        self, storage: TenancyStorageInterface, markers: IdempotencyStorageInterface
    ) -> None:
        """The zombie's clock is ahead of the retry's, so of the two rows it is
        the one stamped later. Skew past the pending lease is what let it
        through when the fence was an ordering; the marker it lost is not."""
        org = make_org()
        user_id = new_id()
        stalled_attempt, winning_attempt = new_id(), new_id()
        winner = make_api_key(user_id, uuid4().hex)
        stalled = winner.model_copy(
            update={
                "key_hash": uuid4().hex,
                "created_at": winner.created_at + timedelta(minutes=5),
            }
        )
        marker = make_marker(stalled, stalled_attempt)
        await markers.write_record(org.id, marker)
        # The first attempt stalls before it writes anything; the retry takes
        # the marker over and lands the row and the secret the caller holds.
        taken = await markers.take_over_pending(
            org.id, user_id, marker.key, lease_bound(utcnow()), winning_attempt
        )
        assert taken is not None
        await storage.issue_api_key(
            org.id, winner, (make_key_row(org.id, winner),), winning_attempt
        )
        with pytest.raises(Conflict):
            await storage.issue_api_key(
                org.id, stalled, (make_key_row(org.id, stalled),), stalled_attempt
            )
        assert await storage.read_api_key_by_digest(winner.key_hash) == (org.id, winner)
        assert await storage.read_api_key_by_digest(stalled.key_hash) is None

    async def test_a_finished_marker_no_longer_admits_a_re_mint(
        self, storage: TenancyStorageInterface, markers: IdempotencyStorageInterface
    ) -> None:
        """The fence is the marker's liveness, not its existence: once the
        outcome is stored the secret reached the caller, and nothing re-mints
        over it."""
        org = make_org()
        user_id = new_id()
        api_key = make_api_key(user_id, uuid4().hex)
        attempt_id = new_id()
        marker = make_marker(api_key, attempt_id)
        await markers.write_record(org.id, marker)
        await storage.issue_api_key(org.id, api_key, (make_key_row(org.id, api_key),), attempt_id)
        await markers.finish_pending(org.id, user_id, marker.key, attempt_id, 201, "{}")
        rerun = api_key.model_copy(update={"key_hash": uuid4().hex, "updated_at": utcnow()})
        with pytest.raises(Conflict):
            await storage.issue_api_key(org.id, rerun, (make_key_row(org.id, rerun),), attempt_id)
        assert await storage.read_api_key_by_digest(api_key.key_hash) == (org.id, api_key)
        assert await storage.read_api_key_by_digest(rerun.key_hash) is None

    async def test_api_key_lookup_by_hash_returns_the_tenant(
        self, storage: TenancyStorageInterface
    ) -> None:
        org = make_org()
        key_hash = uuid4().hex
        api_key = make_api_key(new_id(), key_hash)
        await storage.write_api_key(org.id, api_key)
        assert await storage.read_api_key_by_digest(key_hash) == (org.id, api_key)
        assert await storage.read_api_keys(org.id, None, limit=10) == [api_key]
        revoked = api_key.model_copy(update={"deleted_at": utcnow(), "deleted_by": new_id()})
        await storage.write_api_key(org.id, revoked)
        assert await storage.read_api_keys(org.id, None, limit=10) == []

    async def test_a_socket_ticket_is_consumed_by_exactly_one_redeemer(
        self, storage: TenancyStorageInterface
    ) -> None:
        # Five sockets present the one ticket. The consume is one conditional
        # write, so exactly one of them is let in. See contracts/racing.py for
        # what each impl's run of this proves.
        org = make_org()
        ticket = make_socket_ticket(new_id(), uuid4().hex)
        await storage.create_socket_ticket(org.id, ticket)
        redeemed_at = utcnow()
        run = await race(
            *(storage.redeem_socket_ticket(ticket.ticket_hash, redeemed_at) for _ in range(5))
        )
        assert len(run.admitted) == 1, run.summary()
        assert run.admitted[0] == (org.id, ticket.model_copy(update={"redeemed_at": redeemed_at}))
        assert await storage.redeem_socket_ticket(ticket.ticket_hash, utcnow()) is None
        assert await storage.redeem_socket_ticket("missing", utcnow()) is None

    async def test_failed_sign_ins_made_at_once_are_each_counted(
        self, storage: TenancyStorageInterface
    ) -> None:
        # Guesses at one email arrive together; the count moves in the
        # statement, so none of them is lost to a read written back by
        # another. The email need not belong to anyone.
        digest = email_digest(f"{uuid4().hex}@example.test")
        assert await storage.read_sign_in_delay(digest) is None
        failed_at = utcnow()
        await race(*(storage.record_failed_sign_in(digest, failed_at) for _ in range(5)))
        counted = await storage.read_sign_in_delay(digest)
        assert counted is not None
        assert (counted.failures, counted.last_failed_at) == (5, failed_at)
        await storage.clear_failed_sign_ins(digest)
        assert await storage.read_sign_in_delay(digest) is None

    async def test_the_sweep_purges_a_run_that_ended_before_the_bound(
        self, storage: TenancyStorageInterface
    ) -> None:
        old, recent = (email_digest(f"{uuid4().hex}@example.test") for _ in range(2))
        await storage.record_failed_sign_in(old, utcnow() - timedelta(days=40))
        await storage.record_failed_sign_in(recent, utcnow())
        assert await storage.purge_sign_in_delays(utcnow() - timedelta(days=30), 1000) >= 1
        assert await storage.read_sign_in_delay(old) is None
        assert await storage.read_sign_in_delay(recent) is not None

    async def test_the_email_digest_finds_the_identity_the_rule_names(
        self, storage: TenancyStorageInterface
    ) -> None:
        """The database computes the digest of the stored address, folded, and
        the rule computes it of the given one, folded; the two agree in any
        spelling, non-ASCII included."""
        email = f"zo\u00eb-{uuid4().hex[:8]}@example.test"
        identity = make_identity(email)
        await storage.write_identity(identity)
        assert await storage.read_identity_by_email_digest(email_digest(email)) == identity
        assert await storage.read_identity_by_email_digest(email_digest(email.upper())) == identity
        other = email.replace("zo", "zoe", 1)
        assert await storage.read_identity_by_email_digest(email_digest(other)) is None

    async def test_one_identity_holds_an_address_in_any_case(
        self, storage: TenancyStorageInterface
    ) -> None:
        """The unique index is on the folded digest, so a writer that keeps a
        second spelling of an address, one that does not fold among them,
        meets it, and nothing lands."""
        email = f"dee-{uuid4().hex[:8]}@example.test"
        identity = make_identity(email)
        await storage.write_identity(identity)
        second = make_identity(email.upper())
        with pytest.raises(UniqueKeyTaken):
            await storage.write_identity(second)
        assert await storage.read_identity(second.id) is None

    async def test_an_identity_write_lands_its_audit_row_under_the_system_scope(
        self, storage: TenancyStorageInterface, outbox: OutboxStorageInterface
    ) -> None:
        identity = make_identity()
        await storage.write_identity(identity)
        changed = identity.model_copy(update={"operator_role": OperatorRole.READ})
        row = OutboxRow(
            id=new_id(),
            created_at=utcnow(),
            org_id=EMPTY_UUID,
            kind="tenancy.operator.granted",
            target_id=identity.id,
            payload={},
            actor_id=new_id(),
            request_id=new_id(),
            app="cli",
        )
        await storage.write_identity(changed, (row,))
        assert await storage.read_identity(identity.id) == changed
        assert [r.org_id for r in await claim_all(outbox) if r.id == row.id] == [EMPTY_UUID]

    async def test_a_time_zone_is_written_alone(self, storage: TenancyStorageInterface) -> None:
        """The person's time zone lands on their identity and nothing else
        moves; an identity that does not exist takes none."""
        identity = make_identity()
        await storage.write_identity(identity)
        now = utcnow()
        assert await storage.write_time_zone(identity.id, "Europe/Istanbul", now)
        stored = await storage.read_identity(identity.id)
        assert stored is not None and stored.time_zone == "Europe/Istanbul"
        assert stored == identity.model_copy(
            update={"time_zone": "Europe/Istanbul", "updated_at": now}
        )
        assert await storage.write_time_zone(identity.id, "America/Lima", now)
        again = await storage.read_identity(identity.id)
        assert again is not None and again.time_zone == "America/Lima"
        assert not await storage.write_time_zone(new_id(), "UTC", now), "no such identity"

    async def test_a_totp_secret_is_minted_confirmed_and_each_step_used_once(
        self, storage: TenancyStorageInterface
    ) -> None:
        identity = make_identity()
        await storage.write_identity(identity)
        now = utcnow()
        assert not await storage.confirm_totp(identity.id, 10, now), "no secret yet"
        assert not await storage.use_totp_step(identity.id, 10), "nothing is enrolled"
        assert await storage.write_totp_secret(identity.id, "v1.first", now)
        assert await storage.write_totp_secret(identity.id, "v1.second", now), "unconfirmed"
        assert await storage.confirm_totp(identity.id, 10, now)
        assert not await storage.confirm_totp(identity.id, 11, now), "confirmed once"
        assert not await storage.write_totp_secret(identity.id, "v1.third", now), "enrolled"
        stored = await storage.read_identity(identity.id)
        assert stored is not None and stored.totp_enrolled
        assert (stored.totp_secret, stored.totp_last_step) == ("v1.second", 10)
        assert stored.totp_confirmed_at == now
        # A step is accepted once, and never one before the last.
        assert not await storage.use_totp_step(identity.id, 10)
        assert not await storage.use_totp_step(identity.id, 9)
        run = await race(*(storage.use_totp_step(identity.id, 11) for _ in range(5)))
        assert run.outcomes.count(True) == 1, run.summary()
        assert not await storage.write_totp_secret(new_id(), "v1.x", now), "no such identity"

    async def test_the_principal_read_records_a_sessions_use_in_its_own_tenant_only(
        self, storage: TenancyStorageInterface
    ) -> None:
        """A session's use rides the read of its principal: under another
        tenant, or for another user, it records nothing, and a revoked
        session is left as it is."""
        org, other = make_org(), make_org("Other")
        user = make_user(make_identity().id)
        membership = make_membership(user.id)
        await storage.create_org_with_owner(org.id, org, user, membership)
        session = make_session(user.identity_id, user.id, uuid4().hex)
        await storage.write_session(org.id, session)
        seen = utcnow()
        assert await storage.read_principal(other.id, user.id, (session.id, seen)) == (
            None,
            None,
            None,
        )
        await storage.read_principal(org.id, new_id(), (session.id, seen))
        assert await storage.read_session(org.id, session.id) == session
        found = await storage.read_principal(org.id, user.id, (session.id, seen))
        assert found == (org, user, membership)
        touched = await storage.read_session(org.id, session.id)
        assert touched is not None and touched.last_seen_at == seen
        revoked = touched.model_copy(update={"revoked_at": seen})
        await storage.write_session(org.id, revoked)
        await storage.read_principal(org.id, user.id, (session.id, seen + timedelta(minutes=5)))
        assert await storage.read_session(org.id, session.id) == revoked

    async def test_purge_removes_members_with_their_memberships_and_revoked_keys(
        self, storage: TenancyStorageInterface
    ) -> None:
        org = make_org()
        await storage.write_org(org.id, org)
        cut = await drained(storage)
        gone, kept = make_user(make_identity().id), make_user(make_identity().id)
        await storage.write_user(
            org.id,
            gone.model_copy(update={"deleted_at": cut - timedelta(days=1), "deleted_by": gone.id}),
        )
        await storage.write_user(org.id, kept)
        await storage.write_membership(org.id, make_membership(gone.id))
        await storage.write_membership(org.id, make_membership(kept.id))
        old_key = make_api_key(kept.id, uuid4().hex).model_copy(
            update={"deleted_at": cut - timedelta(days=1), "deleted_by": kept.id}
        )
        expired_key = make_api_key(kept.id, uuid4().hex).model_copy(
            update={"expires_at": cut - timedelta(days=1)}
        )
        live_key = make_api_key(kept.id, uuid4().hex)
        await storage.write_api_key(org.id, old_key)
        await storage.write_api_key(org.id, expired_key)
        await storage.write_api_key(org.id, live_key)
        dead_sessions = [
            make_session(
                new_id(), kept.id, uuid4().hex, ttl=before(cut, timedelta(days=1))
            ).model_copy(update={"revoked_at": cut - timedelta(days=2)}),
            make_session(new_id(), kept.id, uuid4().hex, ttl=before(cut, timedelta(days=1))),
        ]
        live_sessions = [
            make_session(new_id(), kept.id, uuid4().hex),
            make_session(new_id(), kept.id, uuid4().hex).model_copy(update={"revoked_at": cut}),
            # Revoked long ago and not expired yet: it goes once its expiry is
            # past the cut, at most a session's lifetime later.
            make_session(new_id(), kept.id, uuid4().hex).model_copy(
                update={"revoked_at": cut - timedelta(days=1)}
            ),
        ]
        for session in (*dead_sessions, *live_sessions):
            await storage.write_session(org.id, session)
        spent_tickets = [
            make_socket_ticket(kept.id, uuid4().hex, ttl=before(cut, timedelta(days=1))).model_copy(
                update={"redeemed_at": cut - timedelta(days=1)}
            ),
            make_socket_ticket(kept.id, uuid4().hex, ttl=before(cut, timedelta(days=1))),
        ]
        fresh_tickets = [
            make_socket_ticket(kept.id, uuid4().hex),
            make_socket_ticket(kept.id, uuid4().hex).model_copy(update={"redeemed_at": cut}),
        ]
        # Tickets keep a retention of their own: one expired within it stays.
        recent_ticket = make_socket_ticket(
            kept.id, uuid4().hex, ttl=before(cut, timedelta(hours=1))
        )
        await storage.create_socket_ticket(org.id, recent_ticket)
        for ticket in (*spent_tickets, *fresh_tickets):
            await storage.create_socket_ticket(org.id, ticket)
        # The user, its membership, two keys, two sessions, two tickets.
        assert await storage.purge_deleted(cut, cut - timedelta(hours=2), 10) == 8
        for session in dead_sessions:
            assert await storage.read_session(org.id, session.id) is None
        for session in live_sessions:
            assert await storage.read_session(org.id, session.id) == session
        for ticket in spent_tickets:
            assert await storage.redeem_socket_ticket(ticket.ticket_hash, utcnow()) is None
        assert (await storage.redeem_socket_ticket(fresh_tickets[0].ticket_hash, utcnow())) == (
            org.id,
            ANY,
        )
        assert await storage.read_user(org.id, gone.id) is None
        assert await storage.read_membership_for_user(org.id, gone.id) is None
        assert await storage.read_user(org.id, kept.id) == kept
        assert await storage.read_membership_for_user(org.id, kept.id) is not None
        assert await storage.read_api_key(org.id, old_key.id) is None
        assert await storage.read_api_key(org.id, expired_key.id) is None
        assert await storage.read_api_key(org.id, live_key.id) == live_key
        assert await storage.purge_deleted(cut, cut - timedelta(hours=2), 10) == 0
        assert await storage.purge_deleted(cut, cut, 10) == 1, "the recent ticket"

    # Invitations: a tenant's rows.

    async def test_an_invitation_round_trips_and_is_found_three_ways(
        self, storage: TenancyStorageInterface
    ) -> None:
        org = make_org()
        await storage.write_org(org.id, org)
        invitation = make_invitation("ann@example.test")
        await storage.write_invitation(org.id, invitation)
        assert await storage.read_invitation(org.id, invitation.id) == invitation
        found = await storage.read_invitation_by_provider_id(
            org.id, invitation.provider_invitation_id
        )
        assert found == invitation
        assert await storage.read_pending_invitation(org.id, "ann@example.test") == invitation
        assert await storage.read_pending_invitation(org.id, "bob@example.test") is None
        accepted = invitation.model_copy(update={"state": InvitationState.ACCEPTED})
        await storage.write_invitation(org.id, accepted)
        assert await storage.read_pending_invitation(org.id, "ann@example.test") is None
        assert await storage.read_invitation(org.id, invitation.id) == accepted

    async def test_the_invitation_reads_and_writes_are_tenant_scoped(
        self, storage: TenancyStorageInterface
    ) -> None:
        org, other = make_org("A"), make_org("B")
        invitation = make_invitation("ann@example.test")
        await storage.write_invitation(org.id, invitation)
        assert await storage.read_invitation(other.id, invitation.id) is None
        assert (
            await storage.read_invitation_by_provider_id(
                other.id, invitation.provider_invitation_id
            )
            is None
        )
        assert await storage.read_pending_invitation(other.id, "ann@example.test") is None
        assert await storage.read_invitations(other.id, None, limit=10) == []
        with pytest.raises(TenantMismatch):
            await storage.write_invitation(
                other.id, invitation.model_copy(update={"state": InvitationState.REVOKED})
            )
        assert await storage.read_invitation(org.id, invitation.id) == invitation

    async def test_pending_invitations_list_newest_first_after_a_cursor(
        self, storage: TenancyStorageInterface
    ) -> None:
        org = make_org()
        made = [make_invitation(f"p{n}@example.test") for n in range(4)]
        for invitation in made:
            await storage.write_invitation(org.id, invitation)
        closed = made[1].model_copy(update={"state": InvitationState.REVOKED})
        await storage.write_invitation(org.id, closed)
        pending = [made[3], made[2], made[0]]
        assert await storage.read_invitations(org.id, None, limit=10) == pending
        first = await storage.read_invitations(org.id, None, limit=2)
        assert first == pending[:2]
        assert await storage.read_invitations(org.id, first[-1].id, limit=2) == pending[2:]

    async def test_an_invitation_keeps_its_two_unique_keys(
        self, storage: TenancyStorageInterface
    ) -> None:
        org, other = make_org("A"), make_org("B")
        first = make_invitation("ann@example.test")
        await storage.write_invitation(org.id, first)
        # One pending invitation per address in a tenant.
        with pytest.raises(UniqueKeyTaken):
            await storage.write_invitation(org.id, make_invitation("ann@example.test"))
        # The address is free in another tenant, and once the first closes.
        await storage.write_invitation(other.id, make_invitation("ann@example.test"))
        await storage.write_invitation(
            org.id, first.model_copy(update={"state": InvitationState.REVOKED})
        )
        await storage.write_invitation(org.id, make_invitation("ann@example.test"))
        # The provider's id names one invitation, across every tenant.
        with pytest.raises(UniqueKeyTaken):
            await storage.write_invitation(
                other.id,
                make_invitation(
                    "cat@example.test", provider_invitation_id=first.provider_invitation_id
                ),
            )

    async def test_a_member_lands_with_the_invitation_they_accept_or_not_at_all(
        self, storage: TenancyStorageInterface
    ) -> None:
        org, other = make_org(), make_org("Other")
        await storage.write_org(org.id, org)
        invitation = make_invitation("dan@example.test", Role.ADMIN)
        await storage.write_invitation(org.id, invitation)
        newcomer = make_identity("dan@example.test")
        dan = make_user(newcomer.id, newcomer.email)
        accepted = invitation.model_copy(
            update={"state": InvitationState.ACCEPTED, "accepted_user_id": dan.id}
        )
        # Another tenant's invitation lands nothing, the member included.
        with pytest.raises(NotFound):
            await storage.create_member(
                other.id,
                dan,
                make_membership(dan.id),
                (make_user_row(other.id, dan),),
                newcomer,
                invitation=accepted,
            )
        assert await storage.read_identity(newcomer.id) is None
        assert await storage.read_user(other.id, dan.id) is None
        await storage.create_member(
            org.id,
            dan,
            make_membership(dan.id, Role.ADMIN),
            (make_user_row(org.id, dan),),
            newcomer,
            invitation=accepted,
        )
        assert await storage.read_user(org.id, dan.id) == dan
        assert await storage.read_invitation(org.id, invitation.id) == accepted

    async def test_purge_takes_closed_and_expired_invitations_and_purge_tenant_all(
        self, storage: TenancyStorageInterface
    ) -> None:
        org, other = make_org(), make_org("Other")
        cut = await drained(storage)
        old = cut - timedelta(days=1)
        revoked = make_invitation("a@example.test").model_copy(
            update={"state": InvitationState.REVOKED, "updated_at": old}
        )
        expired = make_invitation("b@example.test", expires_in=before(cut, timedelta(days=2)))
        open_ = make_invitation("c@example.test")
        just_closed = make_invitation("d@example.test").model_copy(
            update={"state": InvitationState.ACCEPTED}
        )
        for invitation in (revoked, expired, open_, just_closed):
            await storage.write_invitation(org.id, invitation)
        theirs = make_invitation("a@example.test").model_copy(
            update={"state": InvitationState.REVOKED, "updated_at": old}
        )
        await storage.write_invitation(other.id, theirs)
        kept = make_invitation("e@example.test")
        await storage.write_invitation(other.id, kept)
        assert await storage.purge_deleted(cut, cut, 10) == 3, "every tenant's"
        assert await storage.read_invitation(org.id, revoked.id) is None
        assert await storage.read_invitation(org.id, expired.id) is None
        assert await storage.read_invitation(other.id, theirs.id) is None
        assert await storage.read_invitation(org.id, open_.id) == open_
        assert await storage.read_invitation(org.id, just_closed.id) == just_closed
        assert await storage.purge_tenant(org.id, 10) == 2
        assert await storage.read_invitation(org.id, open_.id) is None
        assert await storage.read_invitation(other.id, kept.id) == kept, "per tenant"

    # The identity provider's link.

    async def test_an_identity_is_found_by_its_issuer_and_subject(
        self, storage: TenancyStorageInterface
    ) -> None:
        identity = make_identity().model_copy(
            update={"issuer": "https://issuer.test", "subject": f"user_{uuid4().hex}"}
        )
        await storage.write_identity(identity)
        assert identity.subject is not None
        found = await storage.read_identity_by_issuer_subject(
            "https://issuer.test", identity.subject
        )
        assert found == identity
        assert (
            await storage.read_identity_by_issuer_subject("https://other.test", identity.subject)
            is None
        )
        assert (
            await storage.read_identity_by_issuer_subject("https://issuer.test", "nobody") is None
        )

    async def test_one_identity_per_subject_of_an_issuer(
        self, storage: TenancyStorageInterface
    ) -> None:
        subject = f"user_{uuid4().hex}"
        first = make_identity().model_copy(
            update={"issuer": "https://issuer.test", "subject": subject}
        )
        await storage.write_identity(first)
        second = make_identity().model_copy(
            update={"issuer": "https://issuer.test", "subject": subject}
        )
        with pytest.raises(UniqueKeyTaken):
            await storage.write_identity(second)
        assert await storage.read_identity(second.id) is None
        # The same subject under another issuer is another person, and an
        # identity with no subject never collides.
        await storage.write_identity(second.model_copy(update={"issuer": "https://other.test"}))
        await storage.write_identity(make_identity())
        await storage.write_identity(make_identity())
