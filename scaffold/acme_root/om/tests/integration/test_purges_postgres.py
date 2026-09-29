"""The purges and the sweep's requeue of expired leases, held by a live
Postgres: each one's statements are served by an index, and a row another
transaction holds is skipped, not waited on. Every namespace's purge past its
retention runs across tenants in the system scope, each statement on an index
that leads with the retention column; the purge of one tenant past its own
retention reads that tenant's rows by an index that org_id leads.

The plans are read off the statements the storage impls send, captured as
they go to the driver, so what is explained is the purge itself and not a
copy of it. Each is explained with sequential scans switched off: on a
table this small the planner would read the heap whatever indexes exist,
and the question here is whether an index can serve the statement at all.
A predicate no index serves, such as `done_at < x OR failed_at < x` with no
index on `failed_at`, still plans a sequential scan then.

Whether the plan cache keeps an index is a second question, asked of tables
with rows and statistics. The driver prepares each statement once per
connection, and after five runs Postgres may keep a generic plan. So a purge
runs as the sweep runs it, on one connection: a backlog first, then a pass
with nothing to purge, and the plan read is the one that connection holds
for the idle pass. Sequential scans stay on there.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from contracts.event_storage import append_one, make_event
from contracts.event_storage import drained as drained_events
from contracts.factories import make_session, make_socket_ticket
from contracts.idempotency_storage import drained as drained_records
from contracts.idempotency_storage import make_record
from contracts.slack_storage import drained as drained_slack
from contracts.slack_storage import make_post, posted_at
from contracts.task_storage import make_task, seed
from contracts.tenancy_storage import before
from contracts.tenancy_storage import drained as drained_tenancy
from contracts.work_storage import make_item
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from acme.om.base import EMPTY_UUID, new_id, utcnow
from acme.om.billing.storage.impl.postgres import BillingStoragePostgresImpl
from acme.om.events.storage.impl.postgres import EventStoragePostgresImpl
from acme.om.idempotency.storage.impl.postgres import IdempotencyStoragePostgresImpl
from acme.om.idempotency.types.attempt import lease_bound
from acme.om.media.storage.impl.postgres import MediaStoragePostgresImpl
from acme.om.orchestrations.storage.impl.postgres import OrchestrationsStoragePostgresImpl
from acme.om.outbox.storage.impl.postgres import OutboxStoragePostgresImpl
from acme.om.slack.storage.impl.postgres import SlackStoragePostgresImpl
from acme.om.storage.impl.pg_base import LoginSessions, set_scope
from acme.om.storage.impl.postgres import login_sessions
from acme.om.storage.roles import DatabaseRole
from acme.om.storage.settings import MigrationSettings
from acme.om.tasks.storage.impl.postgres import TasksStoragePostgresImpl
from acme.om.tenancy.storage.impl.postgres import TenancyStoragePostgresImpl
from acme.om.work.storage.impl.postgres import WorkStoragePostgresImpl
from acme.om.work.types.work_item import WorkKind, WorkStatus

pytestmark = pytest.mark.integration

Statement = tuple[str, Any]


@pytest.fixture
async def watched(
    migration_settings: MigrationSettings, migrated: dict[DatabaseRole, str]
) -> AsyncIterator[tuple[LoginSessions, list[AsyncEngine]]]:
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
    """The statements `call` sends, but for the scope and the plan-cache
    setting a transaction opens with."""
    captured: list[Statement] = []

    def record(conn: Any, cursor: Any, statement: str, parameters: Any, *_: Any) -> None:
        if "set_config" not in statement and "plan_cache_mode" not in statement:
            captured.append((statement, parameters))

    for engine in engines:
        event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        await call()
    finally:
        for engine in engines:
            event.remove(engine.sync_engine, "before_cursor_execute", record)
    return captured


async def plan(
    sessions: LoginSessions, role: DatabaseRole, org_id: UUID, statement: Statement
) -> str:
    """The plan of one captured statement under the scope it ran in, with
    sequential scans switched off. EXPLAIN runs nothing."""
    factories = sessions.system if org_id == EMPTY_UUID else sessions
    async with factories[role]() as session:
        await set_scope(session, org_id, None, None)
        await session.execute(text("SET LOCAL enable_seqscan = off"))
        connection = await session.connection()
        sql, parameters = statement
        rows = await connection.exec_driver_sql(f"EXPLAIN {sql}", parameters)
        await session.rollback()
        return "\n".join(row[0] for row in rows)


async def plans(
    watched: tuple[LoginSessions, list[AsyncEngine]],
    role: DatabaseRole,
    org_id: UUID,
    call: Callable[[], Awaitable[object]],
) -> list[str]:
    sessions, engines = watched
    return [await plan(sessions, role, org_id, s) for s in await sent(engines, call)]


def served(found: str, index: str) -> bool:
    return index in found and "Seq Scan" not in found


async def test_the_outbox_purge_is_two_statements_each_on_its_own_index(
    watched: tuple[LoginSessions, list[AsyncEngine]],
) -> None:
    outbox = OutboxStoragePostgresImpl(watched[0])
    done, failed = await plans(
        watched, DatabaseRole.CORE, EMPTY_UUID, lambda: outbox.purge_done(utcnow(), 1000)
    )
    assert served(done, "ix_outbox_rows_done_at_id"), done
    assert served(failed, "ix_outbox_rows_failed_at"), failed


async def settled_and_leased(sessions: LoginSessions, owner_url: str) -> None:
    """A queue of one tenant: settled items, most of them changed after the
    cut the case reads with, so the cut is what picks a batch, and a few
    live leases. Then ANALYZE under the owner, which the statistics need."""
    org = new_id()
    async with sessions[DatabaseRole.QUEUE]() as session:
        await set_scope(session, org, None, None)
        await session.execute(
            text(
                "INSERT INTO queue.work_items (id, org_id, created_at, updated_at, created_by,"
                " updated_by, kind, target_id, idempotency_key, request_id, payload, lane,"
                " status, available_at, lease_expires_at, attempts, max_attempts)"
                " SELECT gen_random_uuid(), :org, now(),"
                " now() + (2900 - g) * interval '20 minutes', :org, :org, 'NOOP',"
                " gen_random_uuid(), gen_random_uuid(), gen_random_uuid(),"
                " '{}', 'default', CASE WHEN g % 50 = 0 THEN 'claimed' ELSE 'done' END, now(),"
                " CASE WHEN g % 50 = 0 THEN now() + interval '1 minute' END, 1, 5"
                " FROM generate_series(1, 3000) g"
            ),
            {"org": org},
        )
        await session.commit()
    engine = create_async_engine(owner_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("ANALYZE queue.work_items"))
    finally:
        await engine.dispose()


ANCIENT = timedelta(days=36500)
"""How far back a case's cut stands: no row of this database is behind it, so
a statement captured with it deletes nothing, and a case's own rows behind it
are the case's."""


async def test_every_purge_across_tenants_reads_an_index_led_by_its_retention(
    watched: tuple[LoginSessions, list[AsyncEngine]],
) -> None:
    """Each statement of each namespace's purge past its retention, as the
    sweep sends it once a pass for every tenant, in the system scope."""
    sessions = watched[0]
    cut = utcnow() - ANCIENT
    system = EMPTY_UUID

    async def across(role: DatabaseRole, call: Callable[[], Awaitable[object]]) -> list[str]:
        return await plans(watched, role, system, call)

    (tasks,) = await across(
        DatabaseRole.CORE, lambda: TasksStoragePostgresImpl(sessions).read_deleted(cut, 1000)
    )
    assert served(tasks, "ix_tasks_deleted_at"), tasks
    (files,) = await across(
        DatabaseRole.CORE,
        lambda: MediaStoragePostgresImpl(sessions).read_purgeable(cut, cut, 1000),
    )
    assert served(files, "ix_files_deleted_at"), files
    assert served(files, "ix_files_created_at_pending"), files
    users, memberships, keys, tenancy_sessions, tickets, invitations = await across(
        DatabaseRole.CORE,
        lambda: TenancyStoragePostgresImpl(sessions).purge_deleted(cut, cut, 1000),
    )
    assert served(users, "ix_users_deleted_at"), users
    assert served(memberships, "ix_memberships_deleted_at"), memberships
    assert served(keys, "ix_api_keys_deleted_at") and "ix_api_keys_expires_at" in keys, keys
    assert served(tenancy_sessions, "ix_sessions_expires_at"), tenancy_sessions
    assert served(tickets, "ix_socket_tickets_expires_at"), tickets
    assert served(invitations, "ix_invitations_updated_at"), invitations
    assert "ix_invitations_expires_at" in invitations, invitations
    (records,) = await across(
        DatabaseRole.CORE,
        lambda: IdempotencyStoragePostgresImpl(sessions).purge_records(cut, new_id(), 1000),
    )
    assert served(records, "ix_idempotency_records_created_at"), records
    assert "ix_idempotency_records_attempt_id" in records, records
    (trim,) = await across(
        DatabaseRole.ACTIVITY, lambda: EventStoragePostgresImpl(sessions).trim(cut, 1000)
    )
    assert served(trim, "ix_events_produced_at"), trim
    assert "uq_events_org_id_seq" in trim and "pk_event_cursors" in trim, trim
    (deliveries,) = await across(
        DatabaseRole.CORE,
        lambda: BillingStoragePostgresImpl(sessions).purge_deliveries(cut, 1000),
    )
    assert served(deliveries, "ix_billing_deliveries_created_at"), deliveries
    installations, states, posts = await across(
        DatabaseRole.CORE, lambda: SlackStoragePostgresImpl(sessions).purge(cut, 1000)
    )
    assert served(installations, "ix_slack_installations_deleted_at"), installations
    assert served(states, "ix_slack_install_states_expires_at"), states
    assert "ix_slack_install_states_redeemed_at" in states, states
    assert served(posts, "ix_slack_posts_created_at"), posts
    (settled,) = await across(
        DatabaseRole.CORE,
        lambda: OrchestrationsStoragePostgresImpl(sessions).purge_settled(cut, 1000),
    )
    assert served(settled, "ix_orchestrations_status_updated_at"), settled


async def test_the_queue_purge_reads_its_index(
    watched: tuple[LoginSessions, list[AsyncEngine]], migrated: dict[DatabaseRole, str]
) -> None:
    # Two indexes of the queue lead with the status, and on an empty table
    # they cost the same; the planner's choice is only a real one over rows
    # and their statistics, so the queue gets both before its plan is read.
    sessions = watched[0]
    await settled_and_leased(sessions, migrated[DatabaseRole.QUEUE])
    (work,) = await plans(
        watched,
        DatabaseRole.QUEUE,
        EMPTY_UUID,
        lambda: WorkStoragePostgresImpl(sessions).purge_items(utcnow(), 1000),
    )
    assert served(work, "ix_work_items_status_updated_at"), work


async def test_a_purge_skips_a_task_another_transaction_holds(pg_sessions: LoginSessions) -> None:
    """A row locked elsewhere is left for the next call, not waited on: the
    purge takes the rest and returns at once."""
    storage = TasksStoragePostgresImpl(pg_sessions)
    org = new_id()
    cut = utcnow() - ANCIENT
    held, free = make_task("held"), make_task("free")
    for task in (held, free):
        await seed(
            storage,
            org,
            task.model_copy(update={"deleted_at": cut - timedelta(days=1), "deleted_by": org}),
        )
    async with pg_sessions[DatabaseRole.CORE]() as holder:
        await set_scope(holder, org, None, None)
        await holder.execute(
            text("SELECT id FROM core.tasks WHERE id = :id FOR UPDATE"), {"id": held.id}
        )
        assert await storage.purge_deleted(cut, [held.id, free.id]) == 1
        assert await storage.purge_tenant(org, 10) == 0, "the held one is still held"
        await holder.rollback()
    assert await storage.purge_deleted(cut, [held.id, free.id]) == 1
    assert await storage.read_task(org, held.id) is None


async def test_the_trim_skips_a_stream_an_append_holds_and_trims_the_others(
    pg_sessions: LoginSessions,
) -> None:
    """An append holds its tenant's cursor row to its commit. The trim takes
    every other tenant's run in the meantime and leaves that one for its next
    call, never waiting on it; the floor of each tenant moves with its own."""
    storage = EventStoragePostgresImpl(pg_sessions)
    now = await drained_events(storage)
    busy, idle = new_id(), new_id()
    for org in (busy, idle):
        for _ in range(2):
            await append_one(storage, org, make_event(org, produced_at=now - timedelta(days=100)))
    before = now - timedelta(days=90)
    async with pg_sessions[DatabaseRole.ACTIVITY]() as holder:
        await set_scope(holder, busy, None, None)
        await holder.execute(
            text("SELECT head FROM activity.event_cursors WHERE org_id = :org FOR UPDATE"),
            {"org": busy},
        )
        assert await storage.trim(before, 1000) == 2, "the idle tenant's, not the busy one's"
        await holder.rollback()
    assert await storage.read_floor(idle) == 2
    assert await storage.read_floor(busy) == 0
    assert await storage.trim(before, 1000) == 2
    assert await storage.read_floor(busy) == 2
    assert await storage.read_after(busy, 0, 10) == []


async def test_a_cross_tenant_purge_skips_a_row_another_transaction_holds(
    pg_sessions: LoginSessions,
) -> None:
    storage = WorkStoragePostgresImpl(pg_sessions)
    org = new_id()
    settled = {
        "status": WorkStatus.DONE,
        "updated_at": utcnow() - timedelta(days=2),
        "claim_token": None,
        "claimed_by": None,
        "lease_expires_at": None,
    }
    items = [make_item(lane="purges").model_copy(update=settled) for _ in range(3)]
    for item in items:
        await storage.create_item(org, item)
    cut = utcnow() - timedelta(days=1)
    async with pg_sessions.system[DatabaseRole.QUEUE]() as holder:
        await set_scope(holder, EMPTY_UUID, None, None)
        await holder.execute(
            text("SELECT id FROM queue.work_items WHERE id = :id FOR UPDATE"),
            {"id": items[0].id},
        )
        assert await storage.purge_items(cut, 10) == 2
        await holder.rollback()
    assert await storage.purge_items(cut, 10) == 1


async def test_the_requeue_of_expired_leases_reads_its_index_across_tenants(
    watched: tuple[LoginSessions, list[AsyncEngine]],
) -> None:
    (requeue,) = await plans(
        watched,
        DatabaseRole.QUEUE,
        EMPTY_UUID,
        lambda: WorkStoragePostgresImpl(watched[0]).requeue_stale(utcnow(), timedelta(0), 100),
    )
    assert served(requeue, "ix_work_items_status_lease_expires_at"), requeue


async def test_the_requeue_skips_an_item_another_transaction_holds(
    pg_sessions: LoginSessions,
) -> None:
    """An item a worker is renewing or settling is locked by that write; the
    requeue takes the others and leaves it for the next call, never waits."""
    storage = WorkStoragePostgresImpl(pg_sessions)
    while await storage.requeue_stale(utcnow(), timedelta(0), 1000):
        pass  # the expired leases the cases before this one left
    lane = f"requeue-{new_id().hex[-12:]}"
    org_a, org_b = new_id(), new_id()
    for org in (org_a, org_b, org_b):
        await storage.create_item(org, make_item(lane=lane))
    claimed = []
    for _ in range(3):
        found = await storage.claim_next(lane, [WorkKind.NOOP], "w1", timedelta(seconds=-1))
        assert found is not None
        claimed.append(found)
    held_org, held = claimed[0]
    async with pg_sessions.system[DatabaseRole.QUEUE]() as holder:
        await set_scope(holder, EMPTY_UUID, None, None)
        await holder.execute(
            text("SELECT id FROM queue.work_items WHERE id = :id FOR UPDATE"), {"id": held.id}
        )
        moved = await storage.requeue_stale(utcnow(), timedelta(0), 10)
        assert sorted(item.id for _, item in moved) == sorted(i.id for _, i in claimed[1:])
        await holder.rollback()
    assert [
        (org, item.id) for org, item in await storage.requeue_stale(utcnow(), timedelta(0), 10)
    ] == [(held_org, held.id)]


async def test_a_session_purge_skips_a_row_another_transaction_holds(
    pg_sessions: LoginSessions,
) -> None:
    storage = TenancyStoragePostgresImpl(pg_sessions)
    cut = await drained_tenancy(storage)
    org = new_id()
    user = new_id()
    gone = before(cut, timedelta(days=1))
    dead = [make_session(new_id(), user, uuid4().hex, ttl=gone) for _ in range(2)]
    for session in dead:
        await storage.write_session(org, session)
    await storage.create_socket_ticket(org, make_socket_ticket(user, uuid4().hex, ttl=gone))
    async with pg_sessions[DatabaseRole.CORE]() as holder:
        await set_scope(holder, org, None, None)
        await holder.execute(
            text("SELECT id FROM core.sessions WHERE id = :id FOR UPDATE"), {"id": dead[0].id}
        )
        assert await storage.purge_deleted(cut, cut, 10) == 2, "a session and the ticket"
        await holder.rollback()
    assert await storage.purge_deleted(cut, cut, 10) == 1


async def test_records_and_posts_go_a_batch_at_a_time_over_postgres(
    pg_sessions: LoginSessions,
) -> None:
    """The same batches as the contract suites, over the statements the
    indexes above serve."""
    org = new_id()
    records = IdempotencyStoragePostgresImpl(pg_sessions)
    now = await drained_records(records)
    for i in range(3):
        old = make_record(key=f"old-{i}", created_at=now - timedelta(days=2))
        await records.write_record(org, old.model_copy(update={"status": 201, "body": "{}"}))
    cut, attempts_before = now - timedelta(days=1), lease_bound(now - timedelta(minutes=20))
    assert await records.purge_records(cut, attempts_before, 2) == 2
    assert await records.purge_records(cut, attempts_before, 2) == 1
    slack = SlackStoragePostgresImpl(pg_sessions)
    then = await drained_slack(slack)
    for _ in range(3):
        await slack.create_post(org, posted_at(make_post(new_id()), then))
    assert await slack.purge(then + timedelta(hours=1), 2) == 2
    assert await slack.purge(then + timedelta(hours=1), 2) == 1


BACKLOG_ROWS = 20000
"""Rows per table, their ages spread over sixty days: enough that reading a
whole table costs more than an index walk, as it does in production."""

BATCH = 1000
"""The batch the sweep takes, each manager's `purge_batch`."""

MEDIA_BATCH = 100
"""Media's batch: each file it reads costs a request to the store."""

SETTLE = 8
"""Runs with a backlog before the idle pass. Postgres weighs a generic plan
after five, so eight leave the connection settled on whichever it keeps."""

AGE = f"now() - interval '60 days' + g * interval '{60 * 86400 // BACKLOG_ROWS} seconds'"
"""The age of row `g`: oldest first, as rows land in time."""

TENANT = "md5((g % 5000)::text)::uuid"
"""The tenant of row `g`: the rows spread over 5,000 tenants, as a sweep
across tenants meets them."""

ACTOR = "CAST(:actor AS uuid)"

BORN = {"id": "gen_random_uuid()", "org_id": TENANT, "created_at": AGE}
TRACKED = BORN | {"updated_at": AGE, "created_by": ACTOR, "updated_by": ACTOR}
DELETED = {"deleted_at": AGE, "deleted_by": ACTOR}

BACKLOG: dict[DatabaseRole, dict[str, dict[str, str]]] = {
    DatabaseRole.CORE: {
        "users": TRACKED
        | DELETED
        | {
            "identity_id": "gen_random_uuid()",
            "email": "'u' || g || '@example.com'",
            "display_name": "'User'",
        },
        "memberships": TRACKED
        | DELETED
        | {"user_id": "gen_random_uuid()", "role": "'member'", "teams": "'[]'"},
        "api_keys": TRACKED
        | {
            "name": "'key'",
            "user_id": "gen_random_uuid()",
            "key_hash": "md5('k' || g)",
            "role": "'member'",
            "expires_at": AGE,
        },
        "sessions": TRACKED
        | {
            "identity_id": "gen_random_uuid()",
            "user_id": "gen_random_uuid()",
            "token_hash": "md5('s' || g)",
            "credential_kind": "'session'",
            "expires_at": AGE,
        },
        "socket_tickets": BORN
        | {
            "user_id": "gen_random_uuid()",
            "ticket_hash": "md5('t' || g)",
            "credential_kind": "'session'",
            "credential_id": "gen_random_uuid()",
            "expires_at": AGE,
        },
        "invitations": TRACKED
        | {
            "email": "'i' || g || '@example.com'",
            "role": "'member'",
            "provider_invitation_id": "md5('i' || g)",
            "state": "CASE WHEN g % 2 = 0 THEN 'accepted' ELSE 'pending' END",
            "expires_at": AGE,
        },
        "billing_deliveries": BORN | {"event_id": "'evt_' || g", "event_type": "'invoice.paid'"},
        "slack_installations": TRACKED
        | DELETED
        | {
            "team_id": "'T' || g",
            "team_name": "'Team'",
            "app_id": "'A1'",
            "bot_user_id": "'B1'",
            "scopes": "'chat:write'",
            "installed_by_slack_user": "'U1'",
            "credential_ref": "'ref'",
            "status": "'active'",
        },
        "slack_install_states": BORN
        | {
            "user_id": "gen_random_uuid()",
            "state_hash": "md5('st' || g)",
            "expires_at": AGE,
            "redeemed_at": f"CASE WHEN g % 2 = 0 THEN {AGE} END",
        },
        "slack_posts": BORN | {"key": "gen_random_uuid()", "channel_id": "'C1'", "ts": "g::text"},
        "orchestrations": TRACKED
        | {
            "kind": "'task_import'",
            "input": "'{}'",
            "status": "(ARRAY['succeeded', 'failed', 'running', 'parked'])[1 + g % 4]",
            "cursor": "0",
            "applied": "0",
            "skipped": "0",
            "row_errors": "'[]'",
            "version": "1",
        },
        "outbox_rows": BORN
        | {
            "kind": "'task.updated'",
            "target_id": "gen_random_uuid()",
            "payload": "'{}'",
            "actor_id": ACTOR,
            "request_id": "gen_random_uuid()",
            "app": "'api'",
            "attempts": "1",
            "done_at": f"CASE WHEN g % 20 > 0 THEN {AGE} END",
            "failed_at": f"CASE WHEN g % 20 = 0 THEN {AGE} END",
        },
        "files": TRACKED
        | {
            "name": "'note'",
            "key": "'k/' || g",
            "extension": "'webm'",
            "content_type": "'audio/webm'",
            "size_bytes": "1024",
            "purpose": "'voice_dictation'",
            "status": "CASE WHEN g % 20 = 0 THEN 'pending' ELSE 'stored' END",
            "deleted_at": f"CASE WHEN g % 20 = 1 THEN {AGE} END",
            "deleted_by": f"CASE WHEN g % 20 = 1 THEN {ACTOR} END",
        },
    },
    DatabaseRole.QUEUE: {
        "work_items": TRACKED
        | {
            "kind": "'NOOP'",
            "target_id": "gen_random_uuid()",
            "idempotency_key": "gen_random_uuid()",
            "request_id": "gen_random_uuid()",
            "payload": "'{}'",
            "lane": "'default'",
            "status": "(ARRAY['done', 'failed', 'queued'])[1 + g % 3]",
            "available_at": "now()",
            "attempts": "1",
            "max_attempts": "5",
        },
    },
}
"""Each table a purge across tenants reads, by role, with the value of each
column of row `g`. Every row is past a cut a day ahead."""


async def backlog(sessions: LoginSessions, migrated: dict[DatabaseRole, str]) -> None:
    """`BACKLOG_ROWS` rows in each table of `BACKLOG`, written in the system
    scope since they are many tenants' rows; then ANALYZE under the owner,
    since the plan cache weighs its plans by the statistics."""
    values = {"actor": new_id(), "n": BACKLOG_ROWS}
    for role, tables in BACKLOG.items():
        async with sessions.system[role]() as session:
            await set_scope(session, EMPTY_UUID, None, None)
            for table, columns in tables.items():
                await session.execute(
                    text(
                        f"INSERT INTO {role.value}.{table} ({', '.join(columns)})"
                        f" SELECT {', '.join(columns.values())} FROM generate_series(1, :n) g"
                    ),
                    values,
                )
            await session.commit()
        engine = create_async_engine(migrated[role])
        try:
            async with engine.begin() as connection:
                await connection.execute(text("ANALYZE"))
        finally:
            await engine.dispose()


def spelled(value: object) -> str:
    """A captured value as a SQL literal; the prepared statement types it."""
    if value is None:
        return "NULL"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, datetime):
        return f"'{value.isoformat()}'"
    return "'" + str(value).replace("'", "''") + "'"


async def settled_idle_plans(
    watched: tuple[LoginSessions, list[AsyncEngine]],
    role: DatabaseRole,
    purge: Callable[[timedelta], Awaitable[object]],
) -> list[str]:
    """The plans one connection holds for an idle pass of `purge`, after it
    ran the purge `SETTLE` times with a backlog. `purge` takes how far past
    now its cut stands: a day ahead, everything is past it; `ANCIENT` back,
    nothing is. Each statement of the idle pass is explained as its prepared
    statement on that connection, under the transaction's plan-cache setting,
    so the plan read is the one the cache hands the next idle pass."""
    sessions, engines = watched
    for _ in range(SETTLE):
        await purge(timedelta(days=1))
    idle: list[tuple[str, Any, str]] = []
    mode = "auto"

    def record(conn: Any, cursor: Any, statement: str, parameters: Any, *_: Any) -> None:
        nonlocal mode
        if "plan_cache_mode" in statement:
            mode = statement.rsplit("=", 1)[1].strip()
        elif "set_config" not in statement:
            idle.append((statement, parameters, mode))

    def ended(conn: Any) -> None:
        nonlocal mode
        mode = "auto"

    listeners = (("before_cursor_execute", record), ("commit", ended), ("rollback", ended))
    for engine in engines:
        for name, listener in listeners:
            event.listen(engine.sync_engine, name, listener)
    try:
        await purge(-ANCIENT)
    finally:
        for engine in engines:
            for name, listener in listeners:
                event.remove(engine.sync_engine, name, listener)
    found: list[str] = []
    async with sessions.system[role]() as session:
        await set_scope(session, EMPTY_UUID, None, None)
        connection = await session.connection()
        listed = await connection.exec_driver_sql(
            "SELECT statement, name FROM pg_prepared_statements"
        )
        prepared: dict[str, str] = {statement: name for statement, name in listed.all()}
        for sql, parameters, statement_mode in idle:
            assert sql in prepared, f"not prepared on the settled connection: {sql}"
            await connection.exec_driver_sql(f"SET LOCAL plan_cache_mode = {statement_mode}")
            # EXECUTE takes no bound parameter, so the values are spelled.
            values = ", ".join(spelled(value) for value in parameters)
            rows = await connection.exec_driver_sql(f"EXPLAIN EXECUTE {prepared[sql]}({values})")
            found.append("\n".join(row[0] for row in rows))
        await session.rollback()
    return found


async def test_an_idle_pass_after_a_backlog_still_reads_each_purge_index(
    watched: tuple[LoginSessions, list[AsyncEngine]], migrated: dict[DatabaseRole, str]
) -> None:
    """The plan cache never settles a purge on a scan of its whole table. A
    connection whose first runs met a backlog would otherwise keep a generic
    plan that reads every row, and run it on every idle pass after."""
    sessions = watched[0]
    await backlog(sessions, migrated)

    def cut(ahead: timedelta) -> datetime:
        return utcnow() + ahead

    tenancy = TenancyStoragePostgresImpl(sessions)
    users, memberships, keys, tenancy_sessions, tickets, invitations = await settled_idle_plans(
        watched,
        DatabaseRole.CORE,
        lambda ahead: tenancy.purge_deleted(cut(ahead), cut(ahead), BATCH),
    )
    assert served(users, "ix_users_deleted_at"), users
    assert served(memberships, "ix_memberships_deleted_at"), memberships
    assert served(keys, "ix_api_keys_deleted_at") and "ix_api_keys_expires_at" in keys, keys
    assert served(tenancy_sessions, "ix_sessions_expires_at"), tenancy_sessions
    assert served(tickets, "ix_socket_tickets_expires_at"), tickets
    assert served(invitations, "ix_invitations_updated_at"), invitations
    assert "ix_invitations_expires_at" in invitations, invitations
    media = MediaStoragePostgresImpl(sessions)
    (files,) = await settled_idle_plans(
        watched,
        DatabaseRole.CORE,
        lambda ahead: media.read_purgeable(cut(ahead), cut(ahead), MEDIA_BATCH),
    )
    assert served(files, "ix_files_deleted_at"), files
    assert served(files, "ix_files_created_at_pending"), files
    billing = BillingStoragePostgresImpl(sessions)
    (deliveries,) = await settled_idle_plans(
        watched, DatabaseRole.CORE, lambda ahead: billing.purge_deliveries(cut(ahead), BATCH)
    )
    assert served(deliveries, "ix_billing_deliveries_created_at"), deliveries
    slack = SlackStoragePostgresImpl(sessions)
    installations, states, posts = await settled_idle_plans(
        watched, DatabaseRole.CORE, lambda ahead: slack.purge(cut(ahead), BATCH)
    )
    assert served(installations, "ix_slack_installations_deleted_at"), installations
    assert served(states, "ix_slack_install_states_expires_at"), states
    assert "ix_slack_install_states_redeemed_at" in states, states
    assert served(posts, "ix_slack_posts_created_at"), posts
    orchestrations = OrchestrationsStoragePostgresImpl(sessions)
    (settled,) = await settled_idle_plans(
        watched, DatabaseRole.CORE, lambda ahead: orchestrations.purge_settled(cut(ahead), BATCH)
    )
    assert served(settled, "ix_orchestrations_status_updated_at"), settled
    outbox = OutboxStoragePostgresImpl(sessions)
    done, failed = await settled_idle_plans(
        watched, DatabaseRole.CORE, lambda ahead: outbox.purge_done(cut(ahead), BATCH)
    )
    assert served(done, "ix_outbox_rows_done_at_id"), done
    assert served(failed, "ix_outbox_rows_failed_at"), failed
    work = WorkStoragePostgresImpl(sessions)
    (items,) = await settled_idle_plans(
        watched, DatabaseRole.QUEUE, lambda ahead: work.purge_items(cut(ahead), BATCH)
    )
    assert served(items, "ix_work_items_status_updated_at"), items
