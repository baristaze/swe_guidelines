"""The operator plane over the memory impls: what the allowlist entry lets
an operator do, the reads of one named tenant and the trail they leave, the
platform's size and the sweep's tally of it, and the creates that share
their implementation with the seeding commands."""

import logging
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from contracts.event_storage import make_event
from contracts.second_factor import TOTP_KEY, SteppingClock, enrolled_operator

from acme.infra.cache import CacheScope
from acme.infra.impl.local import InfraLocalImpl
from acme.integrations.identity.absent import IdentityProviderAbsentImpl
from acme.om.base import EMPTY_UUID, new_id, utcnow
from acme.om.context import (
    AppContext,
    AppType,
    OperatorContext,
    OperatorPermission,
    OperatorRole,
    RequestContext,
    Role,
)
from acme.om.events.storage.impl.memory import EventStorageMemoryImpl
from acme.om.exceptions import (
    Conflict,
    NotAuthenticated,
    NotAuthorized,
    NotFound,
    PersonalOrgFixed,
    ValidationFailed,
)
from acme.om.idempotency.storage.impl.memory import IdempotencyStorageMemoryImpl
from acme.om.idempotency.types.attempt import Attempt
from acme.om.outbox.impl.relay import OutboxRelayImpl
from acme.om.outbox.storage.impl.memory import OutboxStorageMemoryImpl
from acme.om.outbox.types.row import OutboxRow
from acme.om.tenancy.impl.manager import TenancyManagerImpl, TenancyOptions
from acme.om.tenancy.impl.operator import TenancyOperatorManagerImpl, TenancyOperatorOptions
from acme.om.tenancy.rules import email_digest
from acme.om.tenancy.storage.impl.memory import TenancyStorageMemoryImpl
from acme.om.tenancy.types.membership import Membership
from acme.om.tenancy.types.org import Org
from acme.om.tenancy.types.user import User
from acme.om.work.types.work_item import WorkKind, work_row_kind

APP = AppContext(type=AppType.CLI, version="cli@test")
OPERATOR_LOG = "acme.om.tenancy.impl.operator"
SAME_ROWS_EXCEPT = frozenset({"id", "created_at", "updated_at", "created_by", "updated_by"})
"""What two creates of the same tenant differ in: the ids they minted and the
instants they ran; who made a row is compared on its own."""


def request() -> RequestContext:
    return RequestContext(request_id=new_id(), app=APP)


class RecordingRelay(OutboxRelayImpl):
    """The real relay, with every row it was handed kept for the assertions."""

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self.rows: list[OutboxRow] = []

    async def relay_all(self, org_id: UUID, rows: Sequence[OutboxRow]) -> bool:
        self.rows.extend(rows)
        return await super().relay_all(org_id, rows)


class Plane:
    """Both entry points over one storage: the tenant manager the seeding
    commands call, and the operator manager the routes call."""

    def __init__(self, tmp_path: Path) -> None:
        infra = InfraLocalImpl(tmp_path)
        self.outbox = OutboxStorageMemoryImpl()
        self.events = EventStorageMemoryImpl()
        self.storage = TenancyStorageMemoryImpl(self.outbox, IdempotencyStorageMemoryImpl())
        relay = RecordingRelay(self.outbox, self.events, infra.get_topics())
        self.relay = relay
        self.clock = SteppingClock()
        self.manager = TenancyManagerImpl(
            self.storage,
            relay,
            infra.get_cache(CacheScope.REALTIME_TICKET),
            TenancyOptions(dev_sign_in=True, totp_encryption_key=TOTP_KEY),
            self.clock,
            identity_provider=IdentityProviderAbsentImpl(),
        )
        self.operator = TenancyOperatorManagerImpl(
            self.storage,
            self.events,
            relay,
            TenancyOperatorOptions(max_limit=3, totp_encryption_key=TOTP_KEY),
            self.clock,
        )

    async def worker_deletes(self, org_id: UUID, admin: OperatorContext) -> Org:
        """The last step of the `DELETE_ORG` an operator's deletion asked for,
        as the worker runs it: under the operator's name, once the providers
        are done."""
        work = await self.manager.service_context(request(), org_id, admin.identity_id)
        deleted = await self.manager.delete_closed_org(work)
        assert deleted is not None
        return deleted

    async def admit(self, role: OperatorRole, email: str) -> OperatorContext:
        """An operator with the given role, seeded into an org of their own and
        signed in with a second factor."""
        await self.manager.bootstrap(
            request(), email, email.split("@")[0], email, "Op", operator_role=role
        )
        admin, _ = await enrolled_operator(self.manager, self.operator, self.clock, email)
        return admin


@pytest.fixture
def plane(tmp_path: Path) -> Plane:
    return Plane(tmp_path)


@pytest.fixture
async def writer(plane: Plane) -> OperatorContext:
    return await plane.admit(OperatorRole.WRITE, "root@example.test")


@pytest.fixture
async def reader(plane: Plane) -> OperatorContext:
    return await plane.admit(OperatorRole.READ, "sup@example.test")


def rows_of(entity: Org | User | Membership) -> dict[str, object]:
    return entity.model_dump(exclude=set(SAME_ROWS_EXCEPT))


async def test_a_read_operator_reads_and_is_refused_every_write(
    plane: Plane, reader: OperatorContext, writer: OperatorContext
) -> None:
    org = await plane.operator.create_org(writer, "Ajax", "ajax", "ann@example.test", "Ann")
    assert reader.permissions == {OperatorPermission.READ}
    first = await plane.operator.get_orgs(reader, None, 10)
    rest = await plane.operator.get_orgs(reader, first.items[-1].id, 10)
    assert first.has_more and not rest.has_more
    every = first.items + rest.items
    assert {o.slug for o in every if not o.personal} == {"ajax", "root", "sup"}
    # And the personal org of each of the three people who own them.
    assert len([o for o in every if o.personal]) == 3
    assert (await plane.operator.get_org(reader, org.id)).slug == "ajax"
    await plane.operator.tally_size()
    assert (await plane.operator.size(reader)).tenants == 6
    for write in (
        plane.operator.create_org(reader, "Other", "other", "bob@example.test", "Bob"),
        plane.operator.add_member(reader, org.id, "bob@example.test", "Bob", Role.MEMBER),
        plane.operator.delete_org(reader, org.id),
    ):
        with pytest.raises(NotAuthorized, match="operator lacks write"):
            await write
    # Nothing landed: the refusal came before the write.
    assert await plane.storage.read_org_by_slug("other") is None
    assert (
        await plane.storage.read_identity_by_email_digest(email_digest("bob@example.test")) is None
    )
    assert (await plane.storage.read_org(org.id)) == org


async def test_create_org_lands_the_rows_bootstrap_lands(
    plane: Plane, writer: OperatorContext
) -> None:
    """One implementation, two entry points: the org the operator plane
    creates and the org the seeding command creates are the same rows, the
    owner's identity, user, and owner membership included, apart from the ids
    and instants each minted."""
    seeded_ctx, seeded = await plane.manager.bootstrap(
        request(), "Ajax", "ajax", "ann@example.test", "Ann"
    )
    created = await plane.operator.create_org(writer, "Ajax", "ajax-2", "bob@example.test", "Ann")
    assert rows_of(created) == {**rows_of(seeded), "slug": "ajax-2"}
    seeded_owner = (await plane.storage.read_users(seeded.id, None, 10))[0]
    created_owner = (await plane.storage.read_users(created.id, None, 10))[0]
    assert rows_of(created_owner) == {
        **rows_of(seeded_owner),
        "identity_id": created_owner.identity_id,
        "email": "bob@example.test",
    }
    assert seeded_owner.id == seeded_ctx.user_id
    # Both orgs are their owner's own: the owner is the tenant's first actor.
    assert (seeded.created_by, created.created_by) == (seeded_owner.id, created_owner.id)
    for org, owner in ((seeded, seeded_owner), (created, created_owner)):
        membership = await plane.storage.read_membership_for_user(org.id, owner.id)
        assert membership is not None and membership.role is Role.OWNER
        assert membership.created_by == owner.id
        identity = await plane.storage.read_identity(owner.identity_id)
        assert identity is not None and identity.operator_role is None
        assert identity.email == owner.email
    # And both owners sign in the same way.
    login = await plane.manager.dev_sign_in(request(), "bob@example.test")
    assert [m.org.id for m in login.memberships if not m.org.personal] == [created.id]
    # Each new owner came with a personal org, the same way.
    personal = [m.org for m in login.memberships if m.org.personal]
    assert [(o.name, o.personal_identity_id) for o in personal] == [
        ("Ann", created_owner.identity_id)
    ]


async def test_add_member_lands_the_rows_the_seeding_command_lands(
    plane: Plane, writer: OperatorContext
) -> None:
    """The member the operator adds and the member the command adds are the
    same rows, announced by the same row kind; the one difference is who made
    them, the org's creator on the seeding path and the operator's identity
    on the operator plane, which has no user in the tenant."""
    _, org = await plane.manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    _, seeded, created_first = await plane.manager.add_member(
        request(), "ajax", "bob@example.test", "Bob", Role.ADMIN
    )
    added = await plane.operator.add_member(writer, org.id, "cat@example.test", "Bob", Role.ADMIN)
    assert created_first
    assert rows_of(added) == {
        **rows_of(seeded),
        "identity_id": added.identity_id,
        "email": "cat@example.test",
    }
    assert seeded.created_by == org.created_by and added.created_by == writer.identity_id
    for user, actor in ((seeded, org.created_by), (added, writer.identity_id)):
        membership = await plane.storage.read_membership_for_user(org.id, user.id)
        assert membership is not None and membership.role is Role.ADMIN
        assert (membership.created_by, membership.updated_by) == (actor, actor)
    announced = await plane.events.read_after(org.id, 0, 10)
    assert [(e.kind, e.target_id, e.actor_id, e.app) for e in announced] == [
        ("tenancy.user.created", seeded.id, org.created_by, "cli"),
        ("tenancy.user.created", added.id, writer.identity_id, "cli"),
    ]
    # An operator's add of a person who is a member already is the member as they are.
    again = await plane.operator.add_member(writer, org.id, "cat@example.test", "Cat", Role.VIEWER)
    assert again == added


async def test_the_creates_refuse_what_the_commands_refuse_and_a_little_more(
    plane: Plane, writer: OperatorContext
) -> None:
    org = await plane.operator.create_org(writer, "Ajax", "ajax", "ann@example.test", "Ann")
    with pytest.raises(Conflict):
        await plane.operator.create_org(writer, "Ajax", "ajax", "bob@example.test", "Bob")
    for role in (Role.OWNER, Role.SERVICE):
        with pytest.raises(ValidationFailed):
            await plane.operator.add_member(writer, org.id, "bob@example.test", "Bob", role)
    with pytest.raises(NotFound):
        await plane.operator.add_member(writer, new_id(), "bob@example.test", "Bob", Role.MEMBER)
    await plane.operator.delete_org(writer, org.id)
    with pytest.raises(NotFound):
        await plane.operator.add_member(writer, org.id, "bob@example.test", "Bob", Role.MEMBER)
    assert (
        await plane.storage.read_identity_by_email_digest(email_digest("bob@example.test")) is None
    )


async def test_an_operator_deletes_a_team_org_as_its_owner_does(
    plane: Plane, writer: OperatorContext
) -> None:
    """One commit closes the org for everyone in it and asks for `DELETE_ORG`,
    every row under the operator's identity; the org waits, live and empty,
    for the queue, which deletes it under the operator's name (ADR 0042)."""
    org = await plane.operator.create_org(writer, "Ajax", "ajax", "ann@example.test", "Ann")
    bob = await plane.operator.add_member(writer, org.id, "bob@example.test", "Bob", Role.MEMBER)
    # The org's organization at the identity provider, as an invitation makes it.
    await plane.storage.write_org(org.id, org.model_copy(update={"provider_org_id": "org_ajax"}))
    login = await plane.manager.dev_sign_in(request(), "bob@example.test")
    issued = await plane.manager.exchange_login(
        await plane.manager.authenticate_login(request(), login.token), org.id
    )
    session = await plane.manager.authenticate(request(), issued.token)
    plane.relay.rows.clear()

    closed = await plane.operator.delete_org(writer, org.id)

    # Nobody is in it, and no credential reaches it, from the answer on.
    assert await plane.storage.count_members(org.id) == 0
    with pytest.raises(NotAuthenticated):
        await plane.manager.authenticate(request(), issued.token)
    login = await plane.manager.dev_sign_in(request(), "bob@example.test")
    assert org.id not in {m.org.id for m in login.memberships}
    # The org is live, let go of its provider organization, until the queue runs.
    stored = await plane.storage.read_org(org.id)
    assert stored == closed
    assert closed.deleted_at is None and closed.provider_org_id is None
    assert closed.updated_by == writer.identity_id
    # Every row names the operator, who has no user in the tenant.
    assert {row.actor_id for row in plane.relay.rows} == {writer.identity_id}
    kinds = {(row.kind, row.target_id) for row in plane.relay.rows}
    assert ("tenancy.user.deleted", bob.id) in kinds
    assert ("tenancy.session.revoked", session.security.credential_id) in kinds
    work = [r for r in plane.relay.rows if r.kind == work_row_kind(WorkKind.DELETE_ORG)]
    assert len(work) == 1
    assert (work[0].org_id, work[0].target_id) == (org.id, org.id)
    assert work[0].payload == {"provider_org_id": "org_ajax"}
    # A repeat before the queue has run answers the org and asks for nothing.
    plane.relay.rows.clear()
    assert await plane.operator.delete_org(writer, org.id) == closed
    assert plane.relay.rows == []
    # Nobody joins a closed org.
    with pytest.raises(NotFound):
        await plane.operator.add_member(writer, org.id, "cat@example.test", "Cat", Role.MEMBER)

    deleted = await plane.worker_deletes(org.id, writer)
    assert deleted.deleted_by == writer.identity_id
    with pytest.raises(NotFound):
        await plane.operator.delete_org(writer, org.id)


async def test_an_operator_never_deletes_a_personal_org_and_a_reader_never_deletes(
    plane: Plane, writer: OperatorContext, reader: OperatorContext
) -> None:
    """A personal org goes only with its person's account (ADR 0041)."""
    org = await plane.operator.create_org(writer, "Ajax", "ajax", "ann@example.test", "Ann")
    login = await plane.manager.dev_sign_in(request(), "ann@example.test")
    home = next(m.org for m in login.memberships if m.org.personal)
    with pytest.raises(PersonalOrgFixed):
        await plane.operator.delete_org(writer, home.id)
    with pytest.raises(NotAuthorized):
        await plane.operator.delete_org(reader, org.id)
    assert await plane.storage.count_members(org.id) == 1
    assert await plane.storage.count_members(home.id) == 1


async def test_a_rerun_of_a_create_returns_the_row_as_stored(
    plane: Plane, writer: OperatorContext
) -> None:
    """The attempt a retried request runs under names the id the create uses,
    so the rerun of a create that landed finds its row instead of refusing the
    slug it took or adding the person twice."""
    attempt = Attempt(target_id=new_id(), attempt_id=new_id())
    org = await plane.operator.create_org(
        writer, "Ajax", "ajax", "ann@example.test", "Ann", attempt
    )
    assert org.id == attempt.target_id
    rerun = await plane.operator.create_org(
        writer, "Ajax", "ajax", "ann@example.test", "Ann", attempt
    )
    assert rerun == org
    member_attempt = Attempt(target_id=new_id(), attempt_id=new_id())
    added = await plane.operator.add_member(
        writer, org.id, "bob@example.test", "Bob", Role.MEMBER, member_attempt
    )
    assert added.id == member_attempt.target_id
    assert (
        await plane.operator.add_member(
            writer, org.id, "bob@example.test", "Bob", Role.MEMBER, member_attempt
        )
        == added
    )
    assert len(await plane.storage.read_users(org.id, None, 10)) == 2


async def seed_events(plane: Plane, org_id: UUID, count: int) -> None:
    """Events in the tenant's stream, appended to the storage the relay
    appends to, one at a time."""
    for _ in range(count):
        await plane.events.append_events(org_id, [make_event(org_id)])


async def test_the_reads_of_one_tenant_page_the_tenants_rows_and_leave_a_trail(
    plane: Plane, reader: OperatorContext, caplog: pytest.LogCaptureFixture
) -> None:
    """Every read names the tenant, pages the way the tenant's own lists page,
    with the clamp on the page and the lookahead past it, and never crosses
    into another tenant. Each one logs one line naming the tenant and the
    operator and nothing of what was read."""
    _, org = await plane.manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    _, other = await plane.manager.bootstrap(
        request(), "Other", "other", "otto@example.test", "Otto"
    )
    for email in ("bob@example.test", "cat@example.test", "dan@example.test"):
        await plane.manager.add_member(request(), "ajax", email, "M", Role.MEMBER)
    await seed_events(plane, org.id, 6)
    await seed_events(plane, other.id, 2)

    with caplog.at_level(logging.INFO, logger=OPERATOR_LOG):
        assert (await plane.operator.get_org(reader, org.id)) == org
        with pytest.raises(NotFound):
            await plane.operator.get_org(reader, new_id())

        first = await plane.operator.get_members(reader, org.id, None, limit=10)
        assert len(first.items) == 3 and first.has_more  # the clamp is the page
        rest = await plane.operator.get_members(reader, org.id, first.items[-1].id, limit=3)
        assert len(rest.items) == 1 and not rest.has_more
        assert {u.email for u in first.items + rest.items} == {
            "ann@example.test",
            "bob@example.test",
            "cat@example.test",
            "dan@example.test",
        }

        events = await plane.operator.get_events(reader, org.id, 0, limit=100)
        assert [e.seq for e in events] == [1, 2, 3]  # the clamp, as the tenant's own read
        assert [e.seq for e in await plane.operator.get_events(reader, org.id, 3, 3)] == [
            4,
            5,
            6,
        ]
        assert {e.org_id for e in events} == {org.id}
        assert [e.seq for e in await plane.operator.get_events(reader, org.id, 100, 3)] == []

    trail = [r.getMessage() for r in caplog.records if r.name == OPERATOR_LOG]
    assert trail == [
        f"operator {reader.identity_id} read {what} of org {org.id}"
        for what in (
            "org",
            "members",
            "members",
            "events",
            "events",
            "events",
        )
    ]
    assert not any("example.test" in line for line in trail)


async def test_the_sweeps_tally_counts_the_living_and_the_last_day(
    plane: Plane, reader: OperatorContext, writer: OperatorContext
) -> None:
    _, org = await plane.manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    await plane.manager.add_member(request(), "ajax", "bob@example.test", "B", Role.MEMBER)
    await seed_events(plane, org.id, 2)
    before = utcnow()
    tally = await plane.operator.tally_size()
    # The two operators' own orgs count, as does each operator's user in them,
    # and every person's personal org and their user there.
    assert (tally.tenants, tally.users) == (7, 8)
    assert tally.events_last_24h == 3
    assert before <= tally.counted_at <= utcnow()
    assert tally.counted_at - tally.since == timedelta(hours=24)  # the window ends at the count
    assert await plane.operator.size(reader) == tally
    await plane.operator.delete_org(writer, org.id)
    # The read is the tally, not a count: nothing moves until the next one.
    assert (await plane.operator.size(reader)).tenants == 7
    await plane.worker_deletes(org.id, writer)
    assert (await plane.operator.size(reader)).tenants == 7
    await plane.operator.tally_size()
    assert (await plane.operator.size(reader)).tenants == 6


async def test_the_size_is_unavailable_until_the_sweep_has_counted_it(
    plane: Plane, reader: OperatorContext
) -> None:
    with pytest.raises(NotFound, match="not counted yet"):
        await plane.operator.size(reader)


async def test_the_seeding_path_is_unchanged_by_the_shared_implementation(plane: Plane) -> None:
    """The command's `add_member` still runs under the org's creator: the
    role is capped at the creator's, the service role is refused by name, and
    a deleted org is not found."""
    _, org = await plane.manager.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    with pytest.raises(ValidationFailed):
        await plane.manager.add_member(request(), "ajax", "bob@example.test", "Bob", Role.SERVICE)
    ctx, owner, created = await plane.manager.add_member(
        request(), "ajax", "bob@example.test", "Bob", Role.OWNER
    )
    assert created and ctx.user_id == org.created_by and owner.created_by == org.created_by
    assert EMPTY_UUID not in {owner.created_by, owner.updated_by}
