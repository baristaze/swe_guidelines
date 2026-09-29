"""The hot reads, held by a live Postgres: the `mine` scope, the done list and
the archive, the cleanup's read of the archivable tasks, the sweep's read of
the tenants with a chore due, the re-mint's fence, the idempotency,
invitations, and Slack purges, and the sweep's three gauges each read the
index made for them.

The plans are read off the statements the storage impls send, captured as
they go to the driver, and explained under the scope the statement ran in, by
the login that scope runs on, with row-level security in force. Each is
explained twice: with the values it was sent with, and as the generic plan a
prepared statement reaches after five runs, in which every value is a
parameter. A partial index whose predicate names a bound value serves the
first and never the second.

The tenant is seeded with enough rows, across enough people, for the planner
to choose between indexes on statistics, and sequential scans are switched
off, as in the purge suite: the question is which index serves the
statement, not whether a small table is cheaper read whole.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from contracts.factories import make_api_key
from contracts.idempotency_storage import attempt_minted_at, make_record
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from acme.om.base import EMPTY_UUID, new_id, utcnow
from acme.om.idempotency.storage.impl.postgres import IdempotencyStoragePostgresImpl
from acme.om.outbox.storage.impl.postgres import OutboxStoragePostgresImpl
from acme.om.slack.storage.impl.postgres import SlackStoragePostgresImpl
from acme.om.storage.impl.pg_base import LoginSessions, set_scope
from acme.om.storage.impl.postgres import login_sessions
from acme.om.storage.roles import DatabaseRole
from acme.om.storage.settings import MigrationSettings
from acme.om.tasks.storage.impl.postgres import TasksStoragePostgresImpl
from acme.om.tasks.types.filter import TaskCursor, TaskFilter
from acme.om.tasks.types.task import TaskScope
from acme.om.tenancy.storage.impl.postgres import TenancyStoragePostgresImpl
from acme.om.work.storage.impl.postgres import WorkStoragePostgresImpl

pytestmark = pytest.mark.integration

Statement = tuple[str, Any]
Watched = tuple[LoginSessions, list[AsyncEngine]]

PEOPLE = 1000
"""How many people the tenant's tasks spread over, so the generic plan's
estimate for one person (one in PEOPLE) is a small share, as in a large team."""

TENANTS = 20
"""How many tenants the markers spread over, so the generic plan's estimate
for one tenant is a share of the table, as in production."""


@pytest.fixture
async def watched(
    migration_settings: MigrationSettings, migrated: dict[DatabaseRole, str]
) -> AsyncIterator[Watched]:
    """The sessions the storage roots build, with their engines in hand so a
    case can read the statements that go through them."""
    sessions, engines = login_sessions(
        migration_settings.role_urls(),
        migration_settings.role_pools(),
        system_urls=migration_settings.system_role_urls(),
    )
    yield sessions, list(engines.values())
    for engine in engines.values():
        await engine.dispose()


async def sent(
    engines: list[AsyncEngine], call: Callable[[], Awaitable[object]]
) -> list[Statement]:
    """The statements `call` sends, but for the scope each transaction opens with."""
    captured: list[Statement] = []

    def record(conn: Any, cursor: Any, statement: str, parameters: Any, *_: Any) -> None:
        if "set_config" not in statement:
            captured.append((statement, parameters))

    for engine in engines:
        event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        await call()
    finally:
        for engine in engines:
            event.remove(engine.sync_engine, "before_cursor_execute", record)
    return captured


def literal(value: Any) -> str:
    """A bound value spelled as a literal an EXECUTE takes; its type comes
    from the prepared statement's cast."""
    if value is None:
        return "NULL"
    if isinstance(value, int):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


async def plans(
    watched: Watched,
    org_id: UUID,
    call: Callable[[], Awaitable[object]],
    naming: str,
    role: DatabaseRole = DatabaseRole.CORE,
) -> tuple[str, str]:
    """The plans of the one statement `call` sends that names `naming`: with
    its values, and generic, the plan of the statement prepared with every
    value a parameter. The system scope is explained as the system login, as
    it runs. EXPLAIN runs nothing."""
    sessions, engines = watched
    sql, parameters = next(s for s in await sent(engines, call) if naming in s[0])
    logins = sessions.system if org_id == EMPTY_UUID else sessions
    found: list[str] = []
    for generic in (False, True):
        async with logins[role]() as session:
            await set_scope(session, org_id, None, None)
            await session.execute(text("SET LOCAL enable_seqscan = off"))
            connection = await session.connection()
            if generic:
                await session.execute(text("SET LOCAL plan_cache_mode = force_generic_plan"))
                await connection.exec_driver_sql(f"PREPARE hot_read AS {sql}")
                values = ", ".join(literal(value) for value in parameters)
                # A statement with no value is executed with no parentheses.
                arguments = f"({values})" if values else ""
                rows = await connection.exec_driver_sql(f"EXPLAIN EXECUTE hot_read{arguments}")
            else:
                rows = await connection.exec_driver_sql(f"EXPLAIN {sql}", parameters)
            found.append("\n".join(row[0] for row in rows))
            if generic:
                await connection.exec_driver_sql("DEALLOCATE hot_read")
            await session.rollback()
    return found[0], found[1]


def served(found: str, *indexes: str) -> bool:
    return all(index in found for index in indexes) and "Seq Scan" not in found


async def analyze(migrated: dict[DatabaseRole, str], *tables: str) -> None:
    """Statistics, as the migration login that owns the tables."""
    engine = create_async_engine(migrated[DatabaseRole.CORE])
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f"ANALYZE {', '.join(tables)}"))
    finally:
        await engine.dispose()


async def seed_tasks(sessions: LoginSessions, org: UUID) -> list[UUID]:
    """Open, done, and archived tasks across PEOPLE people: assigned to one
    of them, or unassigned and made by one of them. Returns the people."""
    people = [new_id() for _ in range(PEOPLE)]
    async with sessions[DatabaseRole.CORE]() as session:
        await set_scope(session, org, None, None)
        for status, shelf, count in (
            ("open", "NULL", 3000),
            ("done", "NULL", 3000),
            ("done", "now() - interval '200 days' - g * interval '1 hour'", 6000),
        ):
            await session.execute(
                text(
                    "INSERT INTO core.tasks (id, org_id, created_at, updated_at, created_by,"
                    " updated_by, title, notes, status, assignee_id, rank, version,"
                    " archived_at)"
                    " SELECT uuidv7(), :org, now(), now() - g * interval '1 minute',"
                    " p.a[1 + g % :n], p.a[1 + g % :n], 'Task', '', :status,"
                    " CASE WHEN g % 3 > 0 THEN p.a[1 + (g * 7) % :n] END, g, 1,"
                    f" {shelf} FROM generate_series(1, :count) g,"
                    " (SELECT CAST(:people AS uuid[]) AS a) p"
                ),
                {"org": org, "people": people, "n": PEOPLE, "status": status, "count": count},
            )
        await session.commit()
    return people


async def test_the_mine_scope_reads_the_callers_tasks_alone(
    watched: Watched, migrated: dict[DatabaseRole, str]
) -> None:
    """A member with no task reads two short index ranges, one per arm of the
    OR, and not every open or done task of the tenant."""
    sessions = watched[0]
    org = new_id()
    await seed_tasks(sessions, org)
    await analyze(migrated, "core.tasks")
    tasks = TasksStoragePostgresImpl(sessions)
    mine = TaskFilter(scope=TaskScope.MINE, user_id=new_id())
    both = ("ix_tasks_org_id_assignee_id_status", "ix_tasks_org_id_created_by_status")
    for call in (
        lambda: tasks.read_open_tasks(org, mine, None, 51),
        lambda: tasks.count_open_tasks(org, mine),
        lambda: tasks.read_recent_open_tasks(org, mine, 5),
        lambda: tasks.read_done_tasks(org, mine, None, 51),
    ):
        custom, generic = await plans(watched, org, call, "core.tasks")
        assert served(custom, *both), custom
        assert served(generic, *both), generic


async def test_each_shelf_of_done_tasks_pages_in_its_own_index(
    watched: Watched, migrated: dict[DatabaseRole, str]
) -> None:
    """The done list and the archive each walk their own index in the order
    they page in, the first page and a page after a cursor alike, so no page
    sorts; and the cleanup's read of the archivable tasks never walks the
    archive."""
    sessions = watched[0]
    org = new_id()
    people = await seed_tasks(sessions, org)
    await analyze(migrated, "core.tasks")
    tasks = TasksStoragePostgresImpl(sessions)
    team = TaskFilter(scope=TaskScope.TEAM, user_id=people[0])
    cursor = TaskCursor(updated_at=utcnow() - timedelta(days=1), id=new_id())
    for index, call in (
        (
            "ix_tasks_org_id_status_updated_at_id_unarchived",
            lambda: tasks.read_done_tasks(org, team, None, 51),
        ),
        (
            "ix_tasks_org_id_status_updated_at_id_unarchived",
            lambda: tasks.read_done_tasks(org, team, cursor, 51),
        ),
        (
            "ix_tasks_org_id_status_updated_at_id_archived",
            lambda: tasks.read_archived_tasks(org, team, None, 51),
        ),
        (
            "ix_tasks_org_id_status_updated_at_id_archived",
            lambda: tasks.read_archived_tasks(org, team, cursor, 51),
        ),
    ):
        custom, generic = await plans(watched, org, call, "core.tasks")
        for found in (custom, generic):
            assert served(found, index), found
            assert "Sort" not in found, found
    for limit in (1, 500):
        custom, generic = await plans(
            watched,
            org,
            lambda limit=limit: tasks.read_archivable(org, utcnow() - timedelta(days=90), limit),
            "core.tasks",
        )
        assert served(custom, "ix_tasks_org_id_status_updated_at_id_unarchived"), custom
        assert served(generic, "ix_tasks_org_id_status_updated_at_id_unarchived"), generic


async def test_the_read_of_the_tenants_with_a_chore_due_walks_two_partial_indexes(
    watched: Watched, migrated: dict[DatabaseRole, str]
) -> None:
    """The sweep's one read a pass across tenants reads the archivable tasks as
    a range of the done shelf by its last change, and the long ranks from
    their own index, and never the open or archived tasks of any tenant, as
    the system login in the system scope, the first page and a page after a
    tenant. It is planned with its values, as the purges across tenants are,
    so the plan with its values is the one it runs."""
    sessions = watched[0]
    orgs = [new_id() for _ in range(TENANTS)]
    await seed_tasks(sessions, orgs[0])
    async with sessions.system[DatabaseRole.CORE]() as session:
        await set_scope(session, EMPTY_UUID, None, None)
        # Open and done tasks over every tenant: one done task in fifty is
        # past the cut, and one open task in fifty has a long rank.
        await session.execute(
            text(
                "INSERT INTO core.tasks (id, org_id, created_at, updated_at, created_by,"
                " updated_by, title, notes, status, rank, version)"
                " SELECT uuidv7(), t.a[1 + g % :n], now(),"
                " CASE WHEN g % 100 = 0 THEN now() - interval '200 days'"
                " ELSE now() - g * interval '1 minute' END,"
                " gen_random_uuid(), gen_random_uuid(), 'Task', '',"
                " CASE WHEN g % 2 = 0 THEN 'done' ELSE 'open' END,"
                " CASE WHEN g % 100 = 1 THEN g + 0.0000000000000000000000001 ELSE g END, 1"
                " FROM generate_series(1, 20000) g, (SELECT CAST(:orgs AS uuid[]) AS a) t"
            ),
            {"orgs": orgs, "n": TENANTS},
        )
        await session.commit()
    await analyze(migrated, "core.tasks")
    tasks = TasksStoragePostgresImpl(sessions)
    cut = utcnow() - timedelta(days=90)
    for after in (None, orgs[0]):

        async def read(after: UUID | None = after) -> list[UUID]:
            return await tasks.read_tenants_with_chores(cut, after, 100)

        statements = [sql for sql, _ in await sent(watched[1], read)]
        assert "SET LOCAL plan_cache_mode = force_custom_plan" in statements[0], statements
        custom, _ = await plans(watched, EMPTY_UUID, read, "UNION")
        assert served(
            custom, "ix_tasks_updated_at_org_id_done_unarchived", "ix_tasks_org_id_rank_long"
        ), custom


async def test_the_pending_markers_serve_the_purge_and_the_re_mint_fence(
    watched: Watched, migrated: dict[DatabaseRole, str]
) -> None:
    """The purge reads the finished markers by birth and the abandoned
    attempts through the index over the pending markers alone; the re-mint's
    fence reads the one marker its attempt holds through the same index."""
    sessions = watched[0]
    orgs = [new_id() for _ in range(TENANTS)]
    org = orgs[0]
    people = [new_id() for _ in range(PEOPLE)]
    async with sessions[DatabaseRole.CORE]() as session:
        for tenant in orgs:
            # Every tenant's markers: finished ones, and 50 pending, in flight.
            await set_scope(session, tenant, None, None)
            await session.execute(
                text(
                    "INSERT INTO core.idempotency_records (id, org_id, user_id, key,"
                    " request_digest, status, body, created_at, target_id, attempt_id)"
                    " SELECT uuidv7(), :org, p.a[1 + g % :n],"
                    " 'key-' || g, 'd', CASE WHEN g > 50 THEN 201 END,"
                    " CASE WHEN g > 50 THEN '{}' END, now() - interval '1 hour',"
                    " gen_random_uuid(), CASE WHEN g <= 50 THEN uuidv7() END"
                    " FROM generate_series(1, 3000) g, (SELECT CAST(:people AS uuid[]) AS a) p"
                ),
                {"org": tenant, "people": people, "n": PEOPLE},
            )
        await session.commit()
    await analyze(migrated, "core.idempotency_records")
    markers = IdempotencyStoragePostgresImpl(sessions)
    abandoned = attempt_minted_at(utcnow() - timedelta(minutes=30))
    custom, generic = await plans(
        watched,
        EMPTY_UUID,
        lambda: markers.purge_records(utcnow() - timedelta(days=1), abandoned, 1000),
        "core.idempotency_records",
    )
    both = ("ix_idempotency_records_created_at", "ix_idempotency_records_attempt_id")
    assert served(custom, *both), custom
    assert served(generic, *both), generic

    tenancy = TenancyStoragePostgresImpl(sessions)
    key = make_api_key(people[0], new_id().hex)
    attempt = new_id()
    await tenancy.issue_api_key(org, key, (), attempt)
    marker = make_record(user_id=people[0], key="remint")
    await markers.write_record(
        org, marker.model_copy(update={"target_id": key.id, "attempt_id": attempt})
    )
    again = key.model_copy(update={"key_hash": new_id().hex})
    custom, generic = await plans(
        watched,
        org,
        lambda: tenancy.issue_api_key(org, again, (), attempt),
        "UPDATE core.api_keys",
    )
    assert served(custom, "ix_idempotency_records_attempt_id"), custom
    assert served(generic, "ix_idempotency_records_attempt_id"), generic


async def test_the_invitations_and_slack_purges_read_their_indexes(watched: Watched) -> None:
    """The retention purges read across tenants through an index that leads
    with the retention column, and the purge of a tenant reads that tenant's
    rows through one that leads with org_id: neither reads the whole table."""
    sessions = watched[0]
    org = new_id()
    # A cut no row of this database is behind, so the capture deletes nothing.
    cut = utcnow() - timedelta(days=36500)
    tenancy = TenancyStoragePostgresImpl(sessions)
    slack = SlackStoragePostgresImpl(sessions)
    for scope, call, naming, index in (
        (
            EMPTY_UUID,
            lambda: tenancy.purge_deleted(cut, cut, 1000),
            "core.invitations",
            ("ix_invitations_updated_at", "ix_invitations_expires_at"),
        ),
        (
            org,
            lambda: tenancy.purge_tenant(org, 1000),
            "core.invitations",
            ("ix_invitations_org_id_expires_at",),
        ),
        (
            EMPTY_UUID,
            lambda: slack.purge(cut, 1000),
            "core.slack_installations",
            ("ix_slack_installations_deleted_at",),
        ),
        (
            org,
            lambda: slack.purge_tenant(org, 1000),
            "core.slack_installations",
            ("ix_slack_installations_org_id_deleted_at",),
        ),
    ):
        custom, generic = await plans(watched, scope, call, naming)
        assert served(custom, *index), custom
        assert served(generic, *index), generic


async def test_the_sweeps_gauges_read_one_index_entry_or_one_range(
    watched: Watched, migrated: dict[DatabaseRole, str]
) -> None:
    """The oldest ready item is the first entry of the index of queued items,
    parked ones among them, and not every queued item on every lane; the
    items failed of late are a range of the status index; the oldest pending
    outbox row is the head of the done-at index, where done_at is null, and
    never the done rows behind it."""
    sessions = watched[0]
    async with sessions.system[DatabaseRole.QUEUE]() as session:
        await set_scope(session, EMPTY_UUID, None, None)
        # Done items mostly, a tenth failed, and a queued tenth, most of it
        # parked for later, over three lanes and TENANTS tenants.
        await session.execute(
            text(
                "INSERT INTO queue.work_items (id, org_id, created_at, updated_at, created_by,"
                " kind, target_id, idempotency_key, payload, lane, status, available_at,"
                " attempts, max_attempts, updated_by, request_id)"
                " SELECT uuidv7(), t.a[1 + g % :n], now(), now() - g * interval '1 second',"
                " gen_random_uuid(), 'NOOP', gen_random_uuid(), gen_random_uuid(), '{}',"
                " (ARRAY['default', 'region:a', 'region:b'])[1 + g % 3],"
                " CASE WHEN g % 10 < 8 THEN 'done' WHEN g % 10 = 8 THEN 'failed'"
                " ELSE 'queued' END,"
                " CASE WHEN g % 100 = 9 THEN now() - g * interval '1 second'"
                " ELSE now() + interval '1 day' END,"
                " 1, 3, gen_random_uuid(), gen_random_uuid()"
                " FROM generate_series(1, 30000) g, (SELECT CAST(:orgs AS uuid[]) AS a) t"
            ),
            {"orgs": [new_id() for _ in range(TENANTS)], "n": TENANTS},
        )
        await session.commit()
    async with sessions.system[DatabaseRole.CORE]() as session:
        await set_scope(session, EMPTY_UUID, None, None)
        # Done rows mostly, and a few pending and failed.
        await session.execute(
            text(
                "INSERT INTO core.outbox_rows (id, org_id, created_at, kind, target_id,"
                " payload, actor_id, request_id, app, done_at, attempts, failed_at)"
                " SELECT uuidv7(), t.a[1 + g % :n], now() - g * interval '1 second',"
                " 'tasks.task.created', gen_random_uuid(), '{}', gen_random_uuid(),"
                " gen_random_uuid(), 'portal',"
                " CASE WHEN g % 100 > 1 THEN now() END, 0,"
                " CASE WHEN g % 100 = 1 THEN now() END"
                " FROM generate_series(1, 30000) g, (SELECT CAST(:orgs AS uuid[]) AS a) t"
            ),
            {"orgs": [new_id() for _ in range(TENANTS)], "n": TENANTS},
        )
        await session.commit()
    await analyze(migrated, "core.outbox_rows", "queue.work_items")
    work = WorkStoragePostgresImpl(sessions)
    outbox = OutboxStoragePostgresImpl(sessions)
    for call, naming, role, index, first_entry in (
        (
            lambda: work.oldest_ready_at(utcnow()),
            "queue.work_items",
            DatabaseRole.QUEUE,
            "ix_work_items_available_at_queued",
            True,
        ),
        (
            lambda: work.count_failed_since(utcnow() - timedelta(minutes=15)),
            "queue.work_items",
            DatabaseRole.QUEUE,
            "ix_work_items_status_updated_at",
            False,
        ),
        (
            outbox.oldest_pending_at,
            "core.outbox_rows",
            DatabaseRole.CORE,
            "ix_outbox_rows_done_at_id",
            False,
        ),
    ):
        custom, generic = await plans(watched, EMPTY_UUID, call, naming, role)
        assert served(custom, index), custom
        assert served(generic, index), generic
        if first_entry:
            # A walk in the index's order that stops at its first row.
            assert "Limit" in custom and "Limit" in generic, custom + generic
            assert "Bitmap" not in custom + generic, custom + generic
