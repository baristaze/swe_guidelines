"""Every role's migrated schema agrees with the ORM metadata, the latest
revision of every role downgrades and upgrades again, the logins are safe to
make twice, a migration behind a held lock gives up within its bound, a data
migration passes the fence it runs under, the personal org backfill gives
every person one, the due date backfill gives every task with a due time its
date, a task's rank and position follow each other for the builds before this
one, and the address fold folds every address and stops on two that fold to
one."""

import asyncio
import time
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from contracts.event_storage import make_event
from contracts.factories import (
    make_identity,
    make_invitation,
    make_membership,
    make_org,
    make_personal_org,
    make_user,
)
from contracts.task_storage import bump, make_task
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from acme.om.base import new_id
from acme.om.events.storage.impl.postgres import EventStoragePostgresImpl
from acme.om.exceptions import UniqueKeyTaken
from acme.om.opcontext import Role
from acme.om.storage.impl.pg_base import LoginSessions, set_scope
from acme.om.storage.migrate import (
    RUN_AGAIN,
    VERSION_TABLE,
    backfill,
    check,
    downgrade,
    ensure_logins_at,
    head,
    main,
    upgrade,
)
from acme.om.storage.roles import DatabaseRole
from acme.om.storage.settings import MigrationSettings
from acme.om.tasks.storage.impl.postgres import TasksStoragePostgresImpl
from acme.om.tasks.types.task import Task
from acme.om.tenancy.rules import MAX_SLUG_LENGTH, SLUG_PATTERN, email_digest
from acme.om.tenancy.storage.impl.postgres import TenancyStoragePostgresImpl
from acme.om.tenancy.types.identity import Identity
from acme.om.tenancy.types.invitation import InvitationState
from acme.om.tenancy.types.issued import OrgMembership

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("role", list(DatabaseRole))
async def test_orm_and_schema_agree(migrated: dict[DatabaseRole, str], role: DatabaseRole) -> None:
    assert await check(role, migrated[role]) == []


@pytest.mark.parametrize("role", list(DatabaseRole))
async def test_latest_revision_round_trips(
    migrated: dict[DatabaseRole, str], role: DatabaseRole
) -> None:
    if head(role) is None:
        pytest.skip(f"role {role.value} has no migrations yet")
    await downgrade(role, migrated[role], "-1")
    await upgrade(role, migrated[role])
    assert await check(role, migrated[role]) == []


async def test_ensure_logins_runs_again_on_a_migrated_database(
    migration_settings: MigrationSettings, migrated: dict[DatabaseRole, str]
) -> None:
    """The deploy runs it before every migrate, so the second run over a
    database it already shaped changes nothing and fails nothing."""
    settings = migration_settings
    await ensure_logins_at(settings.master_url(), settings.login_passwords())
    for role in DatabaseRole:
        assert await check(role, migrated[role]) == []


async def test_a_migration_behind_a_held_lock_gives_up_within_its_bound(
    migrated: dict[DatabaseRole, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A transaction holds core's version table, which every run of core's
    chain reads first, so it stands for a lock on any table a migration
    touches. With the bound at one second, `migrate upgrade` gives up after
    that second, applies nothing, and exits RUN_AGAIN, which the deploy's
    pre-rollout task runs again on. Once the transaction ends, the same
    command applies the revision. Without the bound, the run waited for as
    long as the transaction stayed open."""
    core = migrated[DatabaseRole.CORE]
    await downgrade(DatabaseRole.CORE, core, "-1")
    before = await on_core(core, f"SELECT version_num FROM core.{VERSION_TABLE}")
    monkeypatch.setenv("ACME_DATABASE_MIGRATION_LOCK_TIMEOUT_SECONDS", "1")
    holder = create_async_engine(core)
    try:
        async with holder.begin() as held:
            await held.execute(text(f"LOCK TABLE core.{VERSION_TABLE} IN ACCESS EXCLUSIVE MODE"))
            started = time.monotonic()
            code = await asyncio.to_thread(main, ["upgrade", "--role", "core"])
            waited = time.monotonic() - started
    finally:
        await holder.dispose()
    assert code == RUN_AGAIN
    # The second of waiting, and what it costs to open a connection and read
    # the chain around it.
    assert 1.0 <= waited < 3.0
    assert "core: a lock was not granted within 1 s" in capsys.readouterr().err
    assert await on_core(core, f"SELECT version_num FROM core.{VERSION_TABLE}") == before
    assert await asyncio.to_thread(main, ["upgrade", "--role", "core"]) == 0
    assert await check(DatabaseRole.CORE, core) == []


async def seed_two_tenants(pg_sessions: LoginSessions) -> None:
    events = EventStoragePostgresImpl(pg_sessions)
    for org in (new_id(), new_id()):
        await events.append_events(org, [make_event(org)])


def forced(connection: Connection) -> bool:
    return connection.exec_driver_sql(
        "SELECT relforcerowsecurity FROM pg_class WHERE oid = 'activity.events'::regclass"
    ).scalar_one()


COUNT = "SELECT count(*) FROM activity.events"
UPDATE = "UPDATE activity.events SET kind = kind"


async def test_a_backfill_under_the_fence_touches_every_tenant(
    pg_sessions: LoginSessions, migrated: dict[DatabaseRole, str]
) -> None:
    """A migration names no tenant, and FORCE binds the migration login that
    owns the table, so a bare UPDATE matches no row and succeeds; its count
    says zero too, and zero equals zero. Seeded with two tenants' rows, the
    backfill counts and touches both, and the fence is back after it."""
    await seed_two_tenants(pg_sessions)
    engine = create_async_engine(migrated[DatabaseRole.ACTIVITY])
    try:
        async with engine.connect() as connection:

            def bare(sync: Connection) -> tuple[int, int]:
                expected = sync.exec_driver_sql(COUNT).scalar_one()
                return expected, sync.exec_driver_sql(UPDATE).rowcount

            assert await connection.run_sync(bare) == (0, 0)
            await connection.rollback()

            def fenced(sync: Connection) -> tuple[int, bool]:
                touched = backfill(sync, DatabaseRole.ACTIVITY, "events", COUNT, UPDATE)
                return touched, forced(sync)

            assert await connection.run_sync(fenced) == (2, True)
            await connection.rollback()
    finally:
        await engine.dispose()


async def test_a_backfill_that_misses_rows_fails_and_keeps_the_fence(
    pg_sessions: LoginSessions, migrated: dict[DatabaseRole, str]
) -> None:
    await seed_two_tenants(pg_sessions)
    engine = create_async_engine(migrated[DatabaseRole.ACTIVITY])
    try:
        async with engine.connect() as connection:
            partial = (
                "UPDATE activity.events SET kind = kind WHERE seq = 1"
                " AND org_id = (SELECT org_id FROM activity.events ORDER BY org_id LIMIT 1)"
            )
            with pytest.raises(RuntimeError, match="touched 1 rows of 2"):
                await connection.run_sync(
                    lambda sync: backfill(sync, DatabaseRole.ACTIVITY, "events", COUNT, partial)
                )
            await connection.rollback()
            assert await connection.run_sync(forced) is True
    finally:
        await engine.dispose()


# The personal org backfill.


BEFORE_BACKFILL = "202609250000"
"""The revision that adds the columns; the one after it gives every existing
person their personal org. The test steps back to here by name, never by a
count: later revisions sit on top of the backfill, the step back takes them
down with it, and the upgrade to the head brings them back."""


async def person_without_a_place(
    storage: TenancyStoragePostgresImpl, email: str, name: str | None
) -> Identity:
    """A person the way the release before the personal org made one: an
    identity, and a team org they own when they have a name."""
    identity = make_identity(email)
    if name is None:
        await storage.write_identity(identity)
        return identity
    org = make_org(name)
    owner = make_user(identity.id, email).model_copy(update={"display_name": name})
    await storage.create_org_with_owner(
        org.id, org, owner, make_membership(owner.id, Role.OWNER), identity
    )
    return identity


def fenced(connection: Connection) -> list[bool]:
    return list(
        connection.exec_driver_sql(
            "SELECT relforcerowsecurity FROM pg_class WHERE oid IN"
            " ('core.orgs'::regclass, 'core.users'::regclass, 'core.memberships'::regclass)"
        ).scalars()
    )


async def test_the_backfill_gives_every_person_one_personal_org(
    pg_sessions: LoginSessions, migrated: dict[DatabaseRole, str]
) -> None:
    """People of two tenants and none, one who has a personal org already, and
    a platform identity: the backfill makes one personal org for each person
    who has none, in their name, with their user and the owner membership, and
    none for the platform. A second run finds nobody, and the fence is back."""
    storage = TenancyStoragePostgresImpl(pg_sessions)
    ann = await person_without_a_place(storage, "ann@example.test", "Ann")
    bob = await person_without_a_place(storage, "bob@example.test", "Bob Ó'Brien")
    nameless = await person_without_a_place(storage, "cid@example.test", None)
    platform = await person_without_a_place(storage, "smoke@platform.acme.invalid", None)
    dee = make_identity("dee@example.test")
    home = make_personal_org(dee.id)
    dee_user = make_user(dee.id, "dee@example.test")
    await storage.create_org_with_owner(
        home.id, home, dee_user, make_membership(dee_user.id, Role.OWNER), dee
    )
    before = await storage.count_orgs()

    await downgrade(DatabaseRole.CORE, migrated[DatabaseRole.CORE], BEFORE_BACKFILL)
    await upgrade(DatabaseRole.CORE, migrated[DatabaseRole.CORE])

    async def personal(identity: Identity) -> list[OrgMembership]:
        places = await storage.read_memberships_by_identity(identity.id, 10)
        return [p for p in places if p.org.personal]

    for identity, org_name, shown in (
        (ann, "Ann", "Ann"),
        (bob, "Bob Ó'Brien", "Bob Ó'Brien"),
        (nameless, "Personal", "cid"),
    ):
        [place] = await personal(identity)
        assert place.org.name == org_name and place.user.display_name == shown
        assert place.role is Role.OWNER and place.org.personal_identity_id == identity.id
        assert SLUG_PATTERN.fullmatch(place.org.slug) and len(place.org.slug) <= MAX_SLUG_LENGTH
        assert place.org.created_by == place.user.id == place.user.created_by
    assert (await personal(bob))[0].org.slug.startswith("bob-")
    assert [p.org.id for p in await personal(dee)] == [home.id]
    assert await storage.read_memberships_by_identity(platform.id, 10) == []
    assert await storage.count_orgs() == before + 3

    # Idempotent: run again, and nobody is left to do.
    await downgrade(DatabaseRole.CORE, migrated[DatabaseRole.CORE], BEFORE_BACKFILL)
    await upgrade(DatabaseRole.CORE, migrated[DatabaseRole.CORE])
    assert await storage.count_orgs() == before + 3
    # The rest of the chain again, so the suite goes on at the head.
    await upgrade(DatabaseRole.CORE, migrated[DatabaseRole.CORE])
    assert await check(DatabaseRole.CORE, migrated[DatabaseRole.CORE]) == []
    engine = create_async_engine(migrated[DatabaseRole.CORE])
    try:
        async with engine.connect() as connection:
            assert await connection.run_sync(fenced) == [True, True, True]
    finally:
        await engine.dispose()


# The due date backfill.


BEFORE_DUE_DATE_BACKFILL = "202609261600"
"""The revision that adds `tasks.due_on`; the one after it fills it from the
due time. Stepped back to by name, as above."""


def raw(connection: Connection, sql: str) -> list[tuple[object, ...]]:
    """A statement over every tenant's tasks: the fence lifted for it and put
    back, in the caller's transaction, as a migration does."""
    connection.exec_driver_sql("ALTER TABLE core.tasks NO FORCE ROW LEVEL SECURITY")
    result = connection.exec_driver_sql(sql)
    rows = [tuple(row) for row in result] if result.returns_rows else []
    connection.exec_driver_sql("ALTER TABLE core.tasks FORCE ROW LEVEL SECURITY")
    return rows


async def on_core(url: str, sql: str) -> list[tuple[object, ...]]:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            return await connection.run_sync(lambda sync: raw(sync, sql))
    finally:
        await engine.dispose()


async def test_the_due_date_backfill_takes_the_utc_date_in_every_tenant(
    pg_sessions: LoginSessions, migrated: dict[DatabaseRole, str]
) -> None:
    """Tasks of two tenants, as the release before wrote them: a due time and
    no due date. The backfill gives each the UTC date of its due time and
    leaves a task without one alone. The due time leaves the table later
    (202609290000), so the downgrade brings it back empty and then gives the
    release before a due time on each due date, and the fence is back after
    both."""
    core = migrated[DatabaseRole.CORE]
    storage = TasksStoragePostgresImpl(pg_sessions)
    ann_org, zoe_org = new_id(), new_id()
    late = make_task("late in Lima")
    plain = make_task("no due time")
    other = make_task("another tenant's")
    for org, task in ((ann_org, late), (ann_org, plain), (zoe_org, other)):
        assert await storage.create_task(org, task, ())

    await downgrade(DatabaseRole.CORE, core, BEFORE_DUE_DATE_BACKFILL)
    await on_core(
        core,
        "UPDATE core.tasks SET due_on = NULL, remind_at = CASE title"
        " WHEN 'late in Lima' THEN timestamptz '2030-09-30 23:30:00-05'"
        " WHEN 'another tenant''s' THEN timestamptz '2030-10-02 08:00:00+00' END",
    )
    await upgrade(DatabaseRole.CORE, core)

    async def due(task: Task, org: UUID) -> date | None:
        stored = await storage.read_task(org, task.id)
        assert stored is not None
        return stored.due_on

    assert await due(late, ann_org) == date(2030, 10, 1), "the UTC date of the due time"
    assert await due(other, zoe_org) == date(2030, 10, 2), "the other tenant's too"
    assert await due(plain, ann_org) is None

    # A due date moved at the head is the one the downgrade reads.
    moved = late.model_copy(update={"due_on": date(2030, 12, 24), "version": late.version + 1})
    await storage.update_task(ann_org, moved, late.version, ())

    # A downgrade restores a due time on every due date: nine in the morning
    # of it, UTC, since the old column comes back empty.
    await downgrade(DatabaseRole.CORE, core, BEFORE_DUE_DATE_BACKFILL)
    rows = await on_core(core, "SELECT title, remind_at FROM core.tasks")
    restored = {title: at for title, at in rows}
    assert restored == {
        "late in Lima": datetime(2030, 12, 24, 9, tzinfo=UTC),
        "no due time": None,
        "another tenant's": datetime(2030, 10, 2, 9, tzinfo=UTC),
    }

    await upgrade(DatabaseRole.CORE, core)
    assert await check(DatabaseRole.CORE, core) == []
    engine = create_async_engine(core)
    try:
        async with engine.connect() as connection:
            assert await connection.run_sync(
                lambda sync: sync.exec_driver_sql(
                    "SELECT relforcerowsecurity FROM pg_class WHERE oid = 'core.tasks'::regclass"
                ).scalar_one()
            )
    finally:
        await engine.dispose()


# The rank, filled from the position and kept for the build before it.


BEFORE_RANK = "202610030000"
"""The revision before the one that adds `tasks.rank`."""

POSITIONS = (0.1, 1e-05, -2.5, 3.0000000000000004, 12345678.9, 0.30000000000000004)
"""Floats whose text is not their first fifteen digits, and one that no
decimal of fifteen digits tells from its neighbour."""


async def as_a_build_before(sessions: LoginSessions, org: UUID, sql: str, **values: object) -> None:
    """A statement a build before this one sends, under the runtime login and
    the tenant's scope."""
    async with sessions[DatabaseRole.CORE]() as session:
        await set_scope(session, org, None, None)
        await session.execute(text(sql), {"org": org, **values})
        await session.commit()


async def test_the_rank_is_the_position_digit_for_digit_and_follows_the_release_before(
    pg_sessions: LoginSessions, migrated: dict[DatabaseRole, str]
) -> None:
    core = migrated[DatabaseRole.CORE]
    storage = TasksStoragePostgresImpl(pg_sessions)
    ann, zoe = new_id(), new_id()
    tasks = [make_task(f"p{index}", rank=position) for index, position in enumerate(POSITIONS)]
    for index, task in enumerate(tasks):
        assert await storage.create_task(ann if index % 2 else zoe, task, ())

    # The fill: every existing row, every tenant, its rank the float's text.
    await downgrade(DatabaseRole.CORE, core, BEFORE_RANK)
    await upgrade(DatabaseRole.CORE, core)
    rows = await on_core(core, "SELECT title, position, rank FROM core.tasks ORDER BY title")
    assert [(position, rank) for _, position, rank in rows] == [
        (position, Decimal(repr(position))) for position in POSITIONS
    ]

    # The build before the rank creates a task: no rank, and the trigger
    # gives it the one its position names, which this release reads it at.
    created = new_id()
    await as_a_build_before(
        pg_sessions,
        ann,
        "INSERT INTO core.tasks (id, org_id, created_at, updated_at, created_by, updated_by,"
        " title, notes, status, position, version)"
        " VALUES (:id, :org, now(), now(), :id, :id, 'before', '', 'open', -7.25, 1)",
        id=created,
    )
    stored = await storage.read_task(ann, created)
    assert stored is not None and stored.rank == Decimal("-7.25")

    # That build moves it by the position alone, and edits a title.
    await as_a_build_before(
        pg_sessions,
        ann,
        "UPDATE core.tasks SET position = 0.15, version = 2 WHERE id = :id",
        id=created,
    )
    await as_a_build_before(
        pg_sessions, ann, "UPDATE core.tasks SET title = 'renamed' WHERE id = :id", id=created
    )
    stored = await storage.read_task(ann, created)
    assert stored is not None and (stored.rank, stored.title) == (Decimal("0.15"), "renamed")
    places = await storage.read_open_places(ann, None, None, limit=10)
    assert [rank for rank, _ in places] == sorted(
        [Decimal("0.15"), *(Decimal(repr(p)) for i, p in enumerate(POSITIONS) if i % 2)]
    )

    # This release writes the rank alone; the position takes the rank's
    # float, and the rank stays as written.
    exact = Decimal("0.1500000000000000000000001")
    moved = stored.model_copy(update={"rank": exact, "version": stored.version + 1})
    await storage.update_task(ann, moved, stored.version, ())
    stored = await storage.read_task(ann, created)
    assert stored is not None and stored.rank == exact
    rows = await on_core(core, f"SELECT position FROM core.tasks WHERE id = '{created}'")
    assert rows == [(0.15,)]

    # Downgraded to before the rank, that build reads the position this
    # release kept.
    await downgrade(DatabaseRole.CORE, core, BEFORE_RANK)
    rows = await on_core(core, f"SELECT position FROM core.tasks WHERE id = '{created}'")
    assert rows == [(0.15,)]
    await upgrade(DatabaseRole.CORE, core)
    assert await check(DatabaseRole.CORE, core) == []


# The position, kept the rank's float for the release before, which reads it.


BEFORE_POSITION_TRIGGER = "202610170000"
"""The revision before the one that keeps the position from the rank."""

LONG = Decimal("0.1500000000000000000000001")
"""A rank whose float, 0.15, holds only its first digits."""


async def places(core: str) -> dict[str, tuple[object, ...]]:
    """Each task's rank and position, by title, as the database holds them."""
    rows = await on_core(core, "SELECT title, rank, position FROM core.tasks")
    return {str(title): (rank, position) for title, rank, position in rows}


async def test_the_position_is_the_ranks_float_on_every_row_either_release_writes(
    pg_sessions: LoginSessions, migrated: dict[DatabaseRole, str]
) -> None:
    """This release names no position. The release before names the rank
    and the position, its float, in every write, and reads the position as a
    number. Each writes over the other's rows, and each reads every row at
    the rank the other wrote."""
    core = migrated[DatabaseRole.CORE]
    storage = TasksStoragePostgresImpl(pg_sessions)
    org = new_id()

    # This release creates a task and moves it. It writes the rank alone,
    # and the position takes the rank's float each time.
    mine = make_task("mine", rank="-2.5")
    assert await storage.create_task(org, mine, ())
    assert (await places(core))["mine"] == (Decimal("-2.5"), -2.5)
    mine = await bump(storage, org, mine, rank=LONG)
    assert (await places(core))["mine"] == (LONG, 0.15)

    # The release before creates a task and moves it: both stay as written.
    theirs = new_id()
    await as_a_build_before(
        pg_sessions,
        org,
        "INSERT INTO core.tasks (id, org_id, created_at, updated_at, created_by, updated_by,"
        " title, notes, status, rank, position, version)"
        " VALUES (:id, :org, now(), now(), :id, :id, 'theirs', '', 'open', -3, -3.0, 1)",
        id=theirs,
    )
    await as_a_build_before(
        pg_sessions,
        org,
        "UPDATE core.tasks SET rank = :rank, position = :position, version = 2 WHERE id = :id",
        id=theirs,
        rank=LONG + Decimal("1e-25"),
        position=float(LONG),
    )
    assert (await places(core))["theirs"] == (LONG + Decimal("1e-25"), 0.15)

    # The release before moves this release's task to the place it holds: it
    # names the same rank and that rank's float, which the row holds, so the
    # rank keeps every digit. Then it edits the title, naming both as read.
    for sql in (
        "UPDATE core.tasks SET rank = :rank, position = :position, version = 3 WHERE id = :id",
        "UPDATE core.tasks SET title = 'mine, edited', rank = :rank, position = :position,"
        " version = 4 WHERE id = :id",
    ):
        await as_a_build_before(pg_sessions, org, sql, id=mine.id, rank=LONG, position=float(LONG))
    assert (await places(core))["mine, edited"] == (LONG, 0.15)

    # This release reads both at their ranks: every digit counts, though the
    # two floats tie.
    assert await storage.read_open_places(org, None, None, limit=10) == [
        (LONG, mine.id),
        (LONG + Decimal("1e-25"), theirs),
    ]

    # Downgraded, the release before reads the position as this release left
    # it, and writes it itself.
    await downgrade(DatabaseRole.CORE, core, BEFORE_POSITION_TRIGGER)
    assert sorted((await places(core)).values()) == [
        (LONG, 0.15),
        (LONG + Decimal("1e-25"), 0.15),
    ]
    await upgrade(DatabaseRole.CORE, core)
    assert await check(DatabaseRole.CORE, core) == []


# The address fold.


BEFORE_ADDRESS_FOLD = "202610200000"
"""The revision before the one that folds every stored address."""


def fences(connection: Connection) -> list[bool]:
    return list(
        connection.exec_driver_sql(
            "SELECT relforcerowsecurity FROM pg_class WHERE oid IN"
            " ('core.users'::regclass, 'core.invitations'::regclass)"
        ).scalars()
    )


async def test_the_address_fold_stops_on_two_that_fold_to_one_and_then_folds_every_address(
    pg_sessions: LoginSessions, migrated: dict[DatabaseRole, str]
) -> None:
    """Rows the release before wrote, as typed, in two tenants. Two
    identities that fold to one stop the migration, which names both and
    changes nothing; so do two pending invitations of one org. Once a person
    settled each, every identity, user, and invitation is folded, in both
    tenants; any spelling finds the identity, a second spelling meets the
    unique index, and the fence is back."""
    core = migrated[DatabaseRole.CORE]
    storage = TenancyStoragePostgresImpl(pg_sessions)
    tail = new_id().hex[:8]
    await downgrade(DatabaseRole.CORE, core, BEFORE_ADDRESS_FOLD)

    dee = make_identity(f"Dee-{tail}@Example.test")
    twin = make_identity(f"dee-{tail}@example.TEST")
    for identity in (dee, twin):
        await storage.write_identity(identity)
    ajax, globex = make_org("Ajax"), make_org("Globex")
    users = []
    for org in (ajax, globex):
        user = make_user(dee.id, dee.email)
        await storage.create_org_with_owner(
            org.id, org, user, make_membership(user.id, Role.OWNER), None
        )
        users.append((org, user))
    asked = make_invitation(f"Bob-{tail}@Example.test")
    again = make_invitation(f"BOB-{tail}@example.test")
    elsewhere = make_invitation(f"Cat-{tail}@Example.test")
    await storage.write_invitation(ajax.id, asked)
    await storage.write_invitation(ajax.id, again)
    await storage.write_invitation(globex.id, elsewhere)

    with pytest.raises(DBAPIError, match="identities whose addresses fold to one") as stopped:
        await upgrade(DatabaseRole.CORE, core)
    assert str(dee.id) in str(stopped.value) and str(twin.id) in str(stopped.value)
    assert (await storage.read_identity(dee.id)) == dee, "nothing changed"

    # A person settles it: the second address was another person's after all.
    await storage.write_identity(twin.model_copy(update={"email": f"deb-{tail}@example.test"}))
    with pytest.raises(DBAPIError, match="pending invitations of one org") as stopped:
        await upgrade(DatabaseRole.CORE, core)
    assert str(asked.id) in str(stopped.value) and str(again.id) in str(stopped.value)

    await storage.write_invitation(
        ajax.id, again.model_copy(update={"state": InvitationState.REVOKED})
    )
    await upgrade(DatabaseRole.CORE, core)

    folded = await storage.read_identity(dee.id)
    assert folded is not None and folded.email == f"dee-{tail}@example.test"
    for org, user in users:
        stored = await storage.read_user(org.id, user.id)
        assert stored is not None and stored.email == f"dee-{tail}@example.test"
    for org, invitation, address in (
        (ajax, asked, f"bob-{tail}@example.test"),
        (ajax, again, f"bob-{tail}@example.test"),
        (globex, elsewhere, f"cat-{tail}@example.test"),
    ):
        stored_invitation = await storage.read_invitation(org.id, invitation.id)
        assert stored_invitation is not None and stored_invitation.email == address
    for spelled in (f"DEE-{tail}@EXAMPLE.TEST", f"dee-{tail}@example.test"):
        assert await storage.read_identity_by_email_digest(email_digest(spelled)) == folded
    with pytest.raises(UniqueKeyTaken):
        await storage.write_identity(make_identity(f"Dee-{tail}@example.test"))

    assert await check(DatabaseRole.CORE, core) == []
    engine = create_async_engine(core)
    try:
        async with engine.connect() as connection:
            assert await connection.run_sync(fences) == [True, True]
    finally:
        await engine.dispose()
