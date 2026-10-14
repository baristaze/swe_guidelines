"""The tenancy storage contract, split by the part of the manager that
calls each method. This module holds the cases of the methods the manager
calls itself: the principal and the credential reads behind each
transition, the socket tickets, the operator plane's rows, and the sweep's
purges. It also holds what every tenancy contract module shares: the
fixtures a concrete test class provides, the rows a write lands with, and
the cut a purge case owns. Each delegate's cases are a module of their own,
`tenancy_<delegate>_storage.py`, and the test class of each backend takes
every one of them.

The cases named in a module's `CROSS_TENANT_CASES` are the tenant fence's
evidence: each one presents another tenant's identifier and asserts that
nothing is found and nothing changes. The negative control that says what
they catch is in `docs/runbooks/tenant-isolation.md`."""

from collections.abc import Callable
from datetime import datetime, timedelta
from unittest.mock import ANY
from uuid import UUID, uuid4

import pytest

from acme.om.base import EMPTY_UUID, new_id, utcnow
from acme.om.context import OperatorRole, Role
from acme.om.exceptions import NotFound, UniqueKeyTaken
from acme.om.idempotency.storage import IdempotencyStorageInterface
from acme.om.outbox.storage import OutboxStorageInterface
from acme.om.outbox.types.row import OutboxRow
from acme.om.tenancy.rules import email_digest
from acme.om.tenancy.storage import TenancyStorageInterface
from acme.om.tenancy.types.auth_session import AuthSession
from acme.om.tenancy.types.invitation import InvitationState
from acme.om.tenancy.types.size import SystemSize
from acme.om.tenancy.types.user import User

from contracts.factories import (
    make_api_key,
    make_auth_session,
    make_identity,
    make_invitation,
    make_membership,
    make_operator_token,
    make_org,
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


INTERFACE = TenancyStorageInterface
"""The storage interface whose cross-tenant cases this suite holds."""

CROSS_TENANT_CASES: frozenset[str] = frozenset(
    {
        "create_socket_ticket",
        "mark_org_purged",
        "purge_tenant",
        "read_principal",
    }
)
"""The methods of `TenancyStorageInterface` whose case presenting another
tenant's identifier is in this module. `test_storage_exceptions.py` adds
up the sets of every module that names the interface and holds them to
every method that takes a tenant, so a new method arrives with its case."""


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


def make_auth_session_row(org_id: UUID, auth_session: AuthSession) -> OutboxRow:
    """The row a revocation lands with; the snapshot never carries the token hash."""
    return OutboxRow(
        id=new_id(),
        created_at=utcnow(),
        org_id=org_id,
        kind="tenancy.auth_session.revoked",
        target_id=auth_session.id,
        payload={"user_id": str(auth_session.user_id)},
        actor_id=auth_session.user_id,
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


class TenancyStorageFixtures:
    """The fixtures every tenancy contract class takes, which the
    concrete test class of each backend provides."""

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


class TenancyStorageContract(TenancyStorageFixtures):
    async def test_the_counts_read_the_living_across_every_tenant(
        self, storage: TenancyStorageInterface
    ) -> None:
        """The system's size: live orgs and live users, whichever tenant they
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

    async def test_the_tally_keeps_the_newest_count_of_the_systems_size(
        self, storage: TenancyStorageInterface
    ) -> None:
        """One row, read back as written; a newer count replaces it and an
        older one, from a worker that counted first and wrote last, does not."""
        assert await storage.read_system_size() is None
        at = utcnow()
        first = SystemSize(
            tenants=3,
            users=5,
            change_events_last_24h=11,
            since=at - timedelta(hours=24),
            counted_at=at,
        )
        await storage.write_system_size(first)
        assert await storage.read_system_size() == first
        later = first.model_copy(
            update={
                "tenants": 4,
                "counted_at": at + timedelta(minutes=5),
                "since": at + timedelta(minutes=5) - timedelta(hours=24),
            }
        )
        await storage.write_system_size(later)
        assert await storage.read_system_size() == later
        await storage.write_system_size(first)
        assert await storage.read_system_size() == later

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
            await storage.write_auth_session(
                tenant.id, make_auth_session(new_id(), user.id, uuid4().hex)
            )
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
            await storage.write_auth_session(
                org.id,
                make_auth_session(
                    new_id(), user.id, uuid4().hex, ttl=before(cut, timedelta(days=1))
                ),
            )
            await storage.create_socket_ticket(
                org.id,
                make_socket_ticket(user.id, uuid4().hex, ttl=before(cut, timedelta(days=1))),
            )
        assert await storage.purge_deleted(cut, cut, 2) == 4, "two of each"
        assert await storage.purge_deleted(cut, cut, 2) == 2, "the rest"
        assert await storage.purge_deleted(cut, cut, 2) == 0
        for _ in range(3):
            await storage.write_auth_session(
                org.id, make_auth_session(new_id(), user.id, uuid4().hex)
            )
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

    # Each unique key the schema declares on these rows has a case here, so
    # the memory impl refuses what the engine refuses: the write raises
    # UniqueKeyTaken and the row that held the key is unchanged. An update by
    # copy of the row that holds the key passes.

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
            await storage.write_auth_session(EMPTY_UUID, row)
        await storage.write_auth_session(
            new_id(), make_auth_session(identity_id, new_id(), uuid4().hex)
        )
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
            await storage.write_auth_session(EMPTY_UUID, row)
        org = make_org()
        session = make_auth_session(identity.id, new_id(), uuid4().hex)
        await storage.write_auth_session(org.id, session)
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
            found = await storage.read_auth_session_by_id(ended.id)
            assert found is not None and found[1].revoked_at == now, ended.credential_kind
            assert found[1].updated_by == EMPTY_UUID
        for kept in (plain, expired, someone_elses):
            assert await storage.read_auth_session_by_id(kept.id) == (EMPTY_UUID, kept)
        assert await storage.read_auth_session(org.id, session.id) == session
        assert await storage.read_operator_tokens(identity.id, now, None, 10) == []
        # Nothing is left to end; a gone identity is NotFound and lands nothing.
        assert await storage.disable_operator(disabled, (), now) == 0
        with pytest.raises(NotFound):
            await storage.disable_operator(make_identity(), (), now)

    async def test_session_lookup_by_hash_returns_the_tenant(
        self, storage: TenancyStorageInterface
    ) -> None:
        org = make_org()
        token_hash = uuid4().hex
        session = make_auth_session(new_id(), new_id(), token_hash)
        await storage.write_auth_session(org.id, session)
        assert await storage.read_auth_session_by_digest(token_hash) == (org.id, session)
        assert await storage.read_auth_session(org.id, session.id) == session
        assert await storage.read_auth_session_by_digest("missing") is None

    async def test_a_credential_is_read_with_the_identity_it_proves(
        self, storage: TenancyStorageInterface
    ) -> None:
        """The digest lookup and the identity behind the credential in one
        read, whether the credential is the system scope's or a tenant's; a
        credential whose identity is gone reads with none."""
        identity = make_identity()
        await storage.write_identity(identity)
        sign_in = make_sign_in(identity.id, uuid4().hex)
        await storage.write_auth_session(EMPTY_UUID, sign_in)
        org = make_org()
        session = make_auth_session(identity.id, new_id(), uuid4().hex)
        await storage.write_auth_session(org.id, session)
        orphan = make_sign_in(new_id(), uuid4().hex)
        await storage.write_auth_session(EMPTY_UUID, orphan)
        assert await storage.read_auth_session_with_identity_by_digest(sign_in.token_hash) == (
            EMPTY_UUID,
            sign_in,
            identity,
        )
        assert await storage.read_auth_session_with_identity_by_digest(session.token_hash) == (
            org.id,
            session,
            identity,
        )
        assert await storage.read_auth_session_with_identity_by_digest(orphan.token_hash) == (
            EMPTY_UUID,
            orphan,
            None,
        )
        assert await storage.read_auth_session_with_identity_by_digest("missing") is None

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

    async def test_the_sweep_purges_a_run_that_ended_before_the_bound(
        self, storage: TenancyStorageInterface
    ) -> None:
        old, recent = (email_digest(f"{uuid4().hex}@example.test") for _ in range(2))
        await storage.record_failed_sign_in(old, utcnow() - timedelta(days=40))
        await storage.record_failed_sign_in(recent, utcnow())
        assert await storage.purge_sign_in_delays(utcnow() - timedelta(days=30), 1000) >= 1
        assert await storage.read_sign_in_delay(old) is None
        assert await storage.read_sign_in_delay(recent) is not None

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
        session = make_auth_session(user.identity_id, user.id, uuid4().hex)
        await storage.write_auth_session(org.id, session)
        seen = utcnow()
        assert await storage.read_principal(other.id, user.id, (session.id, seen)) == (
            None,
            None,
            None,
        )
        await storage.read_principal(org.id, new_id(), (session.id, seen))
        assert await storage.read_auth_session(org.id, session.id) == session
        found = await storage.read_principal(org.id, user.id, (session.id, seen))
        assert found == (org, user, membership)
        touched = await storage.read_auth_session(org.id, session.id)
        assert touched is not None and touched.last_seen_at == seen
        revoked = touched.model_copy(update={"revoked_at": seen})
        await storage.write_auth_session(org.id, revoked)
        await storage.read_principal(org.id, user.id, (session.id, seen + timedelta(minutes=5)))
        assert await storage.read_auth_session(org.id, session.id) == revoked

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
            make_auth_session(
                new_id(), kept.id, uuid4().hex, ttl=before(cut, timedelta(days=1))
            ).model_copy(update={"revoked_at": cut - timedelta(days=2)}),
            make_auth_session(new_id(), kept.id, uuid4().hex, ttl=before(cut, timedelta(days=1))),
        ]
        live_sessions = [
            make_auth_session(new_id(), kept.id, uuid4().hex),
            make_auth_session(new_id(), kept.id, uuid4().hex).model_copy(
                update={"revoked_at": cut}
            ),
            # Revoked long ago and not expired yet: it goes once its expiry is
            # past the cut, at most a session's lifetime later.
            make_auth_session(new_id(), kept.id, uuid4().hex).model_copy(
                update={"revoked_at": cut - timedelta(days=1)}
            ),
        ]
        for session in (*dead_sessions, *live_sessions):
            await storage.write_auth_session(org.id, session)
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
            assert await storage.read_auth_session(org.id, session.id) is None
        for session in live_sessions:
            assert await storage.read_auth_session(org.id, session.id) == session
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
