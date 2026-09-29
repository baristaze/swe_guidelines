"""The second fence, read off the migrated database.

`make migrate-check` compares tables, columns, and indexes; a policy is
invisible to it, so what the database holds is asserted here. The contract
suites in this directory are the other half: they run the same cases over
Postgres with the policies live and stay green, which is the business layer
relying on nothing of the fence.
"""

from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from contracts.event_storage import make_event
from contracts.factories import make_identity, make_user
from contracts.idempotency_storage import make_record
from contracts.work_storage import make_item
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from acme.om.base import EMPTY_UUID, new_id
from acme.om.events.storage.impl.postgres import EventStoragePostgresImpl
from acme.om.events.storage.tables.events import Events
from acme.om.idempotency.storage.impl.postgres import IdempotencyStoragePostgresImpl
from acme.om.idempotency.storage.tables.idempotency_records import IdempotencyRecords
from acme.om.storage.impl.pg_base import LoginSessions, set_scope
from acme.om.storage.logins import MIGRATION_LOGIN, RUNTIME_LOGIN, SYSTEM_LOGIN
from acme.om.storage.roles import DatabaseRole, role_for
from acme.om.storage.scopes import (
    POLICY_NAME,
    SYSTEM_POLICY_NAME,
    TABLE_SCOPES,
    ScopeKind,
    scope_for,
)
from acme.om.tenancy.storage.impl.postgres import TenancyStoragePostgresImpl
from acme.om.tenancy.storage.tables.users import Users
from acme.om.work.storage.impl.postgres import WorkStoragePostgresImpl

pytestmark = pytest.mark.integration

Sessions = LoginSessions

WHO = text("SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")


@pytest.fixture
async def migration_engine(migrated: dict[DatabaseRole, str]) -> AsyncIterator[AsyncEngine]:
    """The migration login's connection, which owns the tables."""
    engine = create_async_engine(migrated[DatabaseRole.CORE])
    yield engine
    await engine.dispose()


async def test_no_login_is_a_superuser_or_bypasses_rls(
    pg_sessions: Sessions, migration_engine: AsyncEngine
) -> None:
    """The test that makes the fence real and not a claim. A superuser walks
    past every policy, and so does a role with BYPASSRLS; on either, every
    assertion below would pass against a database that fences nothing. Each
    of the three logins is asked on a live connection of its own."""
    seen: set[str] = set()
    for factories in (pg_sessions, pg_sessions.system):
        for role in DatabaseRole:
            async with factories[role]() as session:
                found = (await session.execute(WHO)).one()
                seen.add(found.rolname)
                assert not found.rolsuper, f"{found.rolname} is a superuser"
                assert not found.rolbypassrls, f"{found.rolname} carries BYPASSRLS"
    async with migration_engine.connect() as connection:
        found = (await connection.execute(WHO)).one()
        seen.add(found.rolname)
        assert not found.rolsuper, f"{found.rolname} is a superuser"
        assert not found.rolbypassrls, f"{found.rolname} carries BYPASSRLS"
    assert seen == {RUNTIME_LOGIN, SYSTEM_LOGIN, MIGRATION_LOGIN}


async def test_the_runtime_and_the_system_logins_own_nothing(pg_sessions: Sessions) -> None:
    """Only an owner can drop a policy, turn FORCE off, or alter a table, so a
    login that owns nothing cannot, whatever statement reaches it. The role
    schemas and every table in them are the migration login's."""
    async with pg_sessions[DatabaseRole.CORE]() as session:
        owned = (
            await session.execute(
                text(
                    "SELECT pg_get_userbyid(c.relowner) AS owner, n.nspname, c.relname"
                    " FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
                    " WHERE pg_get_userbyid(c.relowner) IN (:runtime, :system)"
                    " UNION ALL SELECT pg_get_userbyid(nspowner), nspname, ''"
                    " FROM pg_namespace WHERE pg_get_userbyid(nspowner) IN (:runtime, :system)"
                ),
                {"runtime": RUNTIME_LOGIN, "system": SYSTEM_LOGIN},
            )
        ).all()
        assert owned == [], f"owned by a serving login: {owned}"
        schemas = (
            await session.execute(
                text(
                    "SELECT nspname, pg_get_userbyid(nspowner) AS owner FROM pg_namespace"
                    " WHERE nspname = ANY(:schemas)"
                ),
                {"schemas": [role.value for role in DatabaseRole]},
            )
        ).all()
        assert {row.owner for row in schemas} == {MIGRATION_LOGIN}, schemas
        with pytest.raises(DBAPIError) as refused:
            await session.execute(text("ALTER TABLE core.tasks NO FORCE ROW LEVEL SECURITY"))
        assert "must be owner" in str(refused.value)


async def _event_ids(session: AsyncSession, org_id: UUID) -> list[UUID]:
    await set_scope(session, org_id, None, None)
    return list((await session.execute(text("SELECT id FROM activity.events"))).scalars())


async def test_the_runtime_login_naming_the_system_scope_reads_nothing(
    pg_sessions: Sessions,
) -> None:
    """Any session may write the setting, so a statement injected into a
    request can name the system scope. The policy admits the system scope to
    the system login alone, and the runtime login naming it reads nothing,
    while the system login reads every tenant's rows."""
    events = EventStoragePostgresImpl(pg_sessions)
    first, second = new_id(), new_id()
    await events.append_events(first, [make_event(first)])
    await events.append_events(second, [make_event(second)])

    async with pg_sessions[DatabaseRole.ACTIVITY]() as session:
        assert await _event_ids(session, EMPTY_UUID) == []
    async with pg_sessions.system[DatabaseRole.ACTIVITY]() as session:
        assert len(await _event_ids(session, EMPTY_UUID)) == 2


async def test_the_funnel_opens_the_system_scope_on_the_system_login(
    pg_sessions: Sessions,
) -> None:
    events = EventStoragePostgresImpl(pg_sessions)
    who = text("SELECT current_user")
    async with events._session_for(Events, org_id=EMPTY_UUID) as session:
        assert (await session.execute(who)).scalar_one() == SYSTEM_LOGIN
    org = new_id()
    async with events._session_for(Events, org_id=org) as session:
        assert (await session.execute(who)).scalar_one() == RUNTIME_LOGIN


async def test_the_policy_is_what_refuses_the_other_tenant(
    pg_sessions: Sessions, migration_engine: AsyncEngine
) -> None:
    """The negative control, run twice. A statement with no tenant predicate
    of its own, under one tenant's scope, reads that tenant's rows alone while
    the policy is in place; with the policy off for the table under test, the
    same statement reads the other tenant's too. That difference is what says
    the policy is live and not only present. Turning it off takes the owner,
    so that step runs under the migration login, and the fence is put back
    whatever the assertions say."""
    events = EventStoragePostgresImpl(pg_sessions)
    mine, theirs = new_id(), new_id()
    await events.append_events(mine, [make_event(mine)])
    await events.append_events(theirs, [make_event(theirs)])
    read = text("SELECT org_id FROM activity.events")

    async def orgs_seen() -> set[UUID]:
        async with pg_sessions[DatabaseRole.ACTIVITY]() as session:
            await set_scope(session, mine, None, None)
            return set((await session.execute(read)).scalars())

    assert await orgs_seen() == {mine}
    try:
        async with migration_engine.begin() as connection:
            await connection.execute(text("ALTER TABLE activity.events DISABLE ROW LEVEL SECURITY"))
        assert await orgs_seen() == {mine, theirs}
    finally:
        async with migration_engine.begin() as connection:
            await connection.execute(text("ALTER TABLE activity.events ENABLE ROW LEVEL SECURITY"))
    assert await orgs_seen() == {mine}


@pytest.mark.parametrize("table_name", sorted(TABLE_SCOPES))
async def test_every_table_holds_the_policy_its_scope_declares(
    pg_sessions: Sessions, table_name: str
) -> None:
    role = role_for(table_name)
    scope = scope_for(table_name)
    fenced = scope.kind is not ScopeKind.SYSTEM
    async with pg_sessions[role]() as session:
        flags = (
            await session.execute(
                text(
                    "SELECT c.relrowsecurity, c.relforcerowsecurity FROM pg_class c"
                    " JOIN pg_namespace n ON n.oid = c.relnamespace"
                    " WHERE n.nspname = :schema AND c.relname = :table"
                ),
                {"schema": role.value, "table": table_name},
            )
        ).one()
        assert flags.relrowsecurity is fenced, f"{table_name}: row security {flags.relrowsecurity}"
        assert flags.relforcerowsecurity is fenced, (
            f"{table_name}: force {flags.relforcerowsecurity}"
        )
        policies = (
            await session.execute(
                text(
                    "SELECT policyname, cmd, roles::text[] AS roles, qual, with_check"
                    " FROM pg_policies WHERE schemaname = :schema AND tablename = :table"
                    " ORDER BY policyname"
                ),
                {"schema": role.value, "table": table_name},
            )
        ).all()
    if not fenced:
        assert policies == [], f"{table_name} is system-scoped and carries {policies}"
        return
    if scope.by_login:
        # One policy per login (ADR 0044): the runtime login's on the tenant
        # alone, the system login's on the system scope alone, and no other
        # login admitted by either.
        assert [(p.policyname, p.cmd, p.roles) for p in policies] == [
            (SYSTEM_POLICY_NAME, "ALL", [SYSTEM_LOGIN]),
            (POLICY_NAME, "ALL", [RUNTIME_LOGIN]),
        ], policies
        system, tenant = policies
        for expression in (tenant.qual, tenant.with_check):
            assert "org_id = (NULLIF(current_setting('app.org_id'" in expression, expression
            assert "00000000-0000-0000-0000-000000000000" not in expression, expression
        for expression in (system.qual, system.with_check):
            assert "'00000000-0000-0000-0000-000000000000'" in expression, expression
            assert "org_id" not in expression.replace("'app.org_id'", ""), expression
        return
    assert len(policies) == 1, f"{table_name} carries {len(policies)} policies"
    policy = policies[0]
    assert policy.policyname == POLICY_NAME
    assert policy.cmd == "ALL"
    for expression in (policy.qual, policy.with_check):
        assert expression is not None
        assert "org_id" in expression, f"{table_name}: no tenant in {expression}"
        assert "'00000000-0000-0000-0000-000000000000'" in expression, (
            f"{table_name}: the system scope is not spelled in the policy"
        )
        assert f"'{SYSTEM_LOGIN}'" in expression, (
            f"{table_name}: the system scope does not name {SYSTEM_LOGIN}"
        )
        assert "'acme'" not in expression.replace(f"'{SYSTEM_LOGIN}'", ""), (
            f"{table_name}: the system scope still admits a login beside {SYSTEM_LOGIN}"
        )
        narrowed = scope.narrowing is not None and all(
            part in expression for part in scope.narrowing
        )
        assert narrowed == (scope.kind is ScopeKind.BOTH), (
            f"{table_name} is {scope.kind.value} and the policy says otherwise: {expression}"
        )


async def test_a_transaction_with_no_scope_reads_nothing_and_writes_refuse(
    pg_sessions: Sessions,
) -> None:
    """Fail closed. A transaction that names no tenant is not a transaction
    that sees every tenant; the setting reads as NULL, every comparison to
    NULL is false, and the policy holds. The read going silently empty is the
    accepted price, and the write is refused outright."""
    org = new_id()
    events = EventStoragePostgresImpl(pg_sessions)
    (appended,) = await events.append_events(org, [make_event(org)])
    assert await events.read_head(org) == 1

    async with pg_sessions[DatabaseRole.ACTIVITY]() as session:
        assert (await session.execute(text("SELECT id FROM activity.events"))).all() == []
        cursors = text("SELECT org_id FROM activity.event_cursors")
        assert (await session.execute(cursors)).all() == []
        with pytest.raises(DBAPIError) as refused:
            await session.execute(
                text("INSERT INTO activity.event_cursors (org_id, head) VALUES (:org, 1)"),
                {"org": org},
            )
        assert "row-level security" in str(refused.value)

    # The row is there; it is the transaction that could not see it.
    assert await events.read_after(org, 0, 10) == [appended]


async def test_a_narrowed_transaction_sees_only_its_person(pg_sessions: Sessions) -> None:
    """A `both`-scoped table narrows to one person when the transaction names
    one. The markers are per (tenant, user, key), so a transaction opened for
    one user reads that user's rows and no other's, with no predicate of its
    own in the statement."""
    org = new_id()
    markers = IdempotencyStoragePostgresImpl(pg_sessions)
    mine, theirs = make_record(key="mine"), make_record(key="theirs")
    await markers.write_record(org, mine)
    await markers.write_record(org, theirs)

    read_all = text("SELECT key FROM core.idempotency_records ORDER BY key")
    async with markers._session_for(IdempotencyRecords, org_id=org) as session:
        assert [row.key for row in await session.execute(read_all)] == ["mine", "theirs"]
    async with markers._session_for(
        IdempotencyRecords, org_id=org, user_id=mine.user_id
    ) as session:
        assert [row.key for row in await session.execute(read_all)] == ["mine"]
    async with markers._session_for(
        IdempotencyRecords, org_id=org, user_id=theirs.user_id
    ) as session:
        assert [row.key for row in await session.execute(read_all)] == ["theirs"]


async def test_a_user_is_narrowed_on_the_identity_behind_it(pg_sessions: Sessions) -> None:
    """A user's person is its identity, so `users` narrows on `app.identity_id`.
    A transaction that names a user id narrows no user row, since a user id
    is never an identity id; one that names an identity sees that identity's
    user and no other."""
    org = new_id()
    tenancy = TenancyStoragePostgresImpl(pg_sessions)
    ann, bob = make_user(make_identity().id), make_user(make_identity().id)
    await tenancy.write_user(org, ann)
    await tenancy.write_user(org, bob)

    read_all = text("SELECT id FROM core.users WHERE org_id = :org ORDER BY id")
    every = sorted([ann.id, bob.id])
    async with tenancy._session_for(Users, org_id=org) as session:
        assert [row.id for row in await session.execute(read_all, {"org": org})] == every
    async with tenancy._session_for(Users, org_id=org, user_id=ann.id) as session:
        assert [row.id for row in await session.execute(read_all, {"org": org})] == every
    async with tenancy._session_for(Users, org_id=org, identity_id=ann.identity_id) as session:
        assert [row.id for row in await session.execute(read_all, {"org": org})] == [ann.id]
    async with tenancy._session_for(Users, org_id=org, identity_id=bob.identity_id) as session:
        assert [row.id for row in await session.execute(read_all, {"org": org})] == [bob.id]


async def _work_orgs(session: AsyncSession, org_id: UUID | None) -> set[UUID]:
    """The tenants of the work items a transaction sees, with no tenant
    predicate of its own: only the policy narrows it."""
    if org_id is not None:
        await set_scope(session, org_id, None, None)
    return set((await session.execute(text("SELECT org_id FROM queue.work_items"))).scalars())


async def test_the_queue_fence_admits_each_login_to_its_half_alone(
    pg_sessions: Sessions, migration_engine: AsyncEngine
) -> None:
    """The work items are fenced by login (ADR 0044). The runtime login sees
    the tenant it names and nothing under the system scope; the system login
    sees every tenant under the system scope and nothing under a tenant or
    under no setting; a login neither policy names sees nothing at all."""
    work = WorkStoragePostgresImpl(pg_sessions)
    mine, theirs = new_id(), new_id()
    await work.create_item(mine, make_item())
    await work.create_item(theirs, make_item())
    runtime, system = pg_sessions[DatabaseRole.QUEUE], pg_sessions.system[DatabaseRole.QUEUE]
    async with runtime() as session:
        assert await _work_orgs(session, mine) == {mine}
    async with runtime() as session:
        assert await _work_orgs(session, EMPTY_UUID) == set()
    async with runtime() as session:
        assert await _work_orgs(session, None) == set()
    async with system() as session:
        assert await _work_orgs(session, EMPTY_UUID) == {mine, theirs}
    async with system() as session:
        assert await _work_orgs(session, mine) == set()
    async with system() as session:
        assert await _work_orgs(session, None) == set()
    async with migration_engine.connect() as connection:
        for org_id in (mine, EMPTY_UUID):
            async with connection.begin():
                await connection.execute(
                    text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org_id)}
                )
                seen = (await connection.execute(text("SELECT org_id FROM queue.work_items"))).all()
                assert seen == [], f"the migration login saw {seen} under {org_id}"
    # A write outside the transaction's tenant is refused on the runtime login.
    async with runtime() as session:
        await set_scope(session, mine, None, None)
        with pytest.raises(DBAPIError) as refused:
            await session.execute(
                text("UPDATE queue.work_items SET org_id = :other"), {"other": theirs}
            )
        assert "row-level security" in str(refused.value)


async def test_the_queue_policy_is_what_refuses_the_other_tenant(
    pg_sessions: Sessions, migration_engine: AsyncEngine
) -> None:
    """The negative control on the table fenced by login, run twice: a
    statement with no tenant predicate reads one tenant's items while the
    policies are in place, and both tenants' with row-level security off."""
    work = WorkStoragePostgresImpl(pg_sessions)
    mine, theirs = new_id(), new_id()
    await work.create_item(mine, make_item())
    await work.create_item(theirs, make_item())

    async def orgs_seen() -> set[UUID]:
        async with pg_sessions[DatabaseRole.QUEUE]() as session:
            return await _work_orgs(session, mine)

    assert await orgs_seen() == {mine}
    try:
        async with migration_engine.begin() as connection:
            await connection.execute(
                text("ALTER TABLE queue.work_items DISABLE ROW LEVEL SECURITY")
            )
        assert await orgs_seen() == {mine, theirs}
    finally:
        async with migration_engine.begin() as connection:
            await connection.execute(text("ALTER TABLE queue.work_items ENABLE ROW LEVEL SECURITY"))
    assert await orgs_seen() == {mine}
