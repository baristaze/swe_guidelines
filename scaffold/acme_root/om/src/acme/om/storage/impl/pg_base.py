"""The Postgres base every namespace storage shares: per-statement role
routing, the funnel that names the scope of every transaction and translates a
database that did not answer in time, and the write primitives: an upsert that
checks the tenant and lands the core row's outbox rows in the same commit, and
an insert that refuses an existing id."""

import logging
import re
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager
from typing import Any, cast
from uuid import UUID

import asyncpg
from sqlalchemy import ColumnElement, CursorResult, Delete, Result, Table, delete, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.util import find_tables

from acme.infra.observability import OUTCOMES
from acme.om.base import EMPTY_UUID, Identifiable
from acme.om.exceptions import (
    CrossRoleStatement,
    RowDeleted,
    TenantMismatch,
    Unavailable,
    UniqueKeyTaken,
)
from acme.om.outbox.storage.tables.outbox_rows import OutboxRows
from acme.om.outbox.types.row import OutboxRow
from acme.om.storage.roles import DatabaseRole, role_for
from acme.om.storage.scopes import IDENTITY_SETTING, ORG_SETTING, USER_SETTING
from acme.om.storage.utils.translation import apply_row, to_row, undeletes

SessionFactory = async_sessionmaker[AsyncSession]

log = logging.getLogger(__name__)


class LoginSessions(Mapping[DatabaseRole, SessionFactory]):
    """The session factories of every role under the two logins a process
    holds: the runtime login's, which is what the mapping itself answers, and
    the system login's beside them, which only the system scope opens. The
    system login's URL names another login, so its pool is its own."""

    def __init__(
        self,
        runtime: Mapping[DatabaseRole, SessionFactory],
        system: Mapping[DatabaseRole, SessionFactory],
    ) -> None:
        self._runtime = dict(runtime)
        self.system: Mapping[DatabaseRole, SessionFactory] = dict(system)

    def __getitem__(self, role: DatabaseRole) -> SessionFactory:
        return self._runtime[role]

    def __iter__(self) -> Iterator[DatabaseRole]:
        return iter(self._runtime)

    def __len__(self) -> int:
        return len(self._runtime)


SCOPE_SETTINGS: tuple[tuple[str, str], ...] = (
    (ORG_SETTING, "org_id"),
    (USER_SETTING, "user_id"),
    (IDENTITY_SETTING, "identity_id"),
)
"""Each transaction setting and the bind parameter that carries it. The
setting names are constants of this module; nothing a caller spells reaches
them, which is why they can be written into the statement."""

_UUID_TEXT = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

_BEGUN = "acme.begun"
"""The key, in a pooled connection's `info`, that says a transaction has
begun on it before: it came from the pool, and is not one opened for this
call."""


def violated_constraint(error: IntegrityError) -> str | None:
    """The name of the constraint an integrity error names, or None when the
    driver does not say. The asyncpg adapter wraps the driver's error, which
    carries `constraint_name`, as the cause of the one SQLAlchemy raises."""
    cause: BaseException | None = error.orig
    while cause is not None:
        name = getattr(cause, "constraint_name", None)
        if isinstance(name, str):
            return name
        cause = cause.__cause__
    return None


QUERY_CANCELED = "57014"
"""Postgres's SQLSTATE for a statement it cancelled: here, one that ran past
the `statement_timeout` every connection of a pool opens with."""


def cancelled(error: DBAPIError) -> bool:
    """Whether Postgres cancelled the statement. The asyncpg adapter wraps the
    driver's error, which carries the SQLSTATE, as the cause of the one
    SQLAlchemy raises."""
    cause: BaseException | None = error.orig
    while cause is not None:
        if getattr(cause, "sqlstate", None) == QUERY_CANCELED:
            return True
        cause = cause.__cause__
    return False


def not_in_time(error: Exception, role: DatabaseRole, login: str) -> Unavailable | None:
    """The refusal of a call the database did not serve in time, counted; None
    for any other failure, which the funnel leaves as it is.

    Two bounds end such a call, and both are the pool's. The checkout bound: no
    connection came free in time (SQLAlchemy's `TimeoutError`), or a new one
    did not open in time (the driver's connect timeout, the builtin
    `TimeoutError`). And the statement deadline, past which Postgres cancels
    the statement. Neither says anything wrong with the call: the same call may
    well be served a moment later. So each is `Unavailable`, 503
    `unavailable`, the shape of a dependency that cannot answer right now,
    and never a driver error that a caller would have to know to read."""
    if isinstance(error, PoolTimeoutError | TimeoutError):
        outcome = "checkout_timeout"
        what = f"no connection to the {role.value} role ({login} login) within the checkout bound"
    elif isinstance(error, DBAPIError) and cancelled(error):
        outcome = "statement_timeout"
        what = f"a statement on the {role.value} role ({login} login) passed its deadline"
    else:
        return None
    OUTCOMES.labels(subsystem="storage", outcome=outcome).inc()
    return Unavailable(f"the database did not answer in time: {what}")


def role_of(target: Any) -> DatabaseRole:
    """The one role a statement or a table class touches; refuses a statement that spans two."""
    if isinstance(target, type):
        return role_for(target.__tablename__)
    tables = find_tables(
        target,
        check_columns=True,
        include_aliases=True,
        include_joins=True,
        include_selects=True,
        include_crud=True,
    )
    roles = {role_for(table.name) for table in tables if isinstance(table, Table)}
    if len(roles) != 1:
        raise CrossRoleStatement(f"statement touches roles {sorted(r.value for r in roles)}")
    return roles.pop()


async def set_scope(
    session: AsyncSession,
    org_id: UUID,
    user_id: UUID | None,
    identity_id: UUID | None,
) -> None:
    """The scope as a statement of its own, inside a transaction that has
    begun: a write that moves to a second tenant in one transaction names the
    second one here. The funnel sends the first scope in the message that
    begins the transaction (`scope_statement`). The settings are set with
    `set_config(name, value, true)`, so they die with the transaction and
    never leak onto the next caller of a pooled connection; `SET LOCAL` takes
    no bind parameter, which is why this is a `SELECT`. A setting that is not
    given is not set at all, and a policy reads it as NULL."""
    values = {"org_id": str(org_id)}
    if user_id is not None:
        values["user_id"] = str(user_id)
    if identity_id is not None:
        values["identity_id"] = str(identity_id)
    calls = ", ".join(
        f"set_config('{setting}', :{param}, true)"
        for setting, param in SCOPE_SETTINGS
        if param in values
    )
    await session.execute(text(f"SELECT {calls}"), values)


def scope_statement(org_id: UUID, user_id: UUID | None, identity_id: UUID | None) -> str:
    """The scope of one transaction as one statement with its values written
    in, for the message that begins the transaction: a message that carries
    two statements takes no bind parameter. Each value is a `UUID` and is
    written only in its canonical text, eight, four, four, four, and twelve
    lower-case hex digits, so nothing but those characters reaches the
    statement. A setting that is not given is not set at all, as in
    `set_scope`."""
    values = {"org_id": org_id, "user_id": user_id, "identity_id": identity_id}
    calls: list[str] = []
    for setting, param in SCOPE_SETTINGS:
        value = values[param]
        if value is None:
            continue
        if not isinstance(value, UUID):
            raise TypeError(f"{param} is {type(value).__name__}, not a UUID")
        literal = str(value)
        if not _UUID_TEXT.fullmatch(literal):
            raise ValueError(f"{param} is not a canonical UUID")
        calls.append(f"set_config('{setting}', '{literal}', true)")
    if not calls:
        raise ValueError("a transaction names its org, the system scope included")
    return f"SELECT {', '.join(calls)}"


class ScopedConnection(asyncpg.Connection):
    """The driver connection every pool opens (`postgres.connect_args`). It
    sends the scope in the message that begins the transaction, so the scope
    costs no round trip of its own.

    The driver begins a transaction with one simple query, `BEGIN;`, and a
    simple query may carry several statements, which the server runs in
    order. The funnel hands the scope over just before it begins
    (`scope_next_begin`), and the next `BEGIN` goes out as `BEGIN; SELECT
    set_config(...);`: the transaction opens, the settings land inside it,
    and nothing else has run. The scope is taken once. Anything but a `BEGIN`
    that meets a scope waiting is refused, so a scope never lands on a
    statement it was not meant for."""

    __slots__ = ("_scope",)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._scope: str | None = None

    def scope_next_begin(self, statement: str | None) -> None:
        self._scope = statement

    async def execute(
        self,
        query: str,
        *args: Any,
        timeout: float | None = None,  # noqa: ASYNC109 - the driver's own signature
    ) -> str:
        scope, self._scope = self._scope, None
        if scope is not None:
            if args or not query.startswith("BEGIN"):
                raise RuntimeError("the scope waits for a BEGIN, and another statement came")
            query = f"{query} {scope};"
        return await super().execute(query, *args, timeout=timeout)


async def scoped_session(
    factory: SessionFactory,
    org_id: UUID,
    user_id: UUID | None,
    identity_id: UUID | None,
) -> AsyncSession:
    """A session whose transaction has begun under its scope, on a live
    connection. One message begins the transaction and sets the scope
    (`ScopedConnection`), and it writes nothing. So when it finds the
    connection dead (the server closed it while it sat in the pool), nothing
    of the caller's has run: the connection is dropped and the transaction
    begins again on the next one. A restart closes every pooled connection at
    once, and each is dropped the same way until a live or a new one answers;
    a new connection that fails to begin fails the call. Past this point
    nothing is retried: a connection that dies after the caller's first
    statement fails the call, because that statement may have written.

    The driver adapter begins its transaction lazily, on the first statement.
    The funnel begins it here instead, through the adapter's own
    `_start_transaction`, so the adapter's state and the driver's agree and
    the failure is met before the caller runs. That is SQLAlchemy's private
    method; the lock file pins the version, and
    `test_storage_scope_message.py` fails if it moves."""
    statement = scope_statement(org_id, user_id, identity_id)
    while True:
        session = factory()
        try:
            connection = await session.connection()
            raw = await connection.get_raw_connection()
        except BaseException:
            await session.close()
            raise
        adapter: Any = raw.dbapi_connection
        driver = raw.driver_connection
        if not isinstance(driver, ScopedConnection):
            await session.close()
            raise TypeError("the pool opens ScopedConnection; see postgres.connect_args")
        reused = bool(connection.info.get(_BEGUN))
        driver.scope_next_begin(statement)
        try:
            await adapter._start_transaction()
        except BaseException as error:
            driver.scope_next_begin(None)
            # The transaction may be half begun, so the connection never goes
            # back to the pool.
            await connection.invalidate()
            await session.close()
            dbapi_error: type[Exception] = connection.dialect.loaded_dbapi.Error
            if not isinstance(error, dbapi_error):
                raise
            dead = connection.dialect.is_disconnect(cast(Any, error), adapter, None)
            if dead and reused:
                log.warning("the scope found its connection closed; beginning again on another")
                continue
            raise DBAPIError.instance(
                statement, None, error, dbapi_error, connection_invalidated=dead
            ) from error
        connection.info[_BEGUN] = True
        return session


def delete_batch(table: type[Any], *where: ColumnElement[bool], limit: int) -> Delete:
    """One batch of a purge: at most `limit` rows that match `where`, chosen
    and locked in one pass, then deleted. A row another transaction holds is
    skipped, never waited on, so two sweeps split a backlog between them and
    no statement grows with the backlog past the deadline its pool carries.
    Materialized for the reason `claim_pending` gives: the batch is chosen
    and locked once, never rescanned per row."""
    batch = (
        select(table.id)
        .where(*where)
        .limit(limit)
        .with_for_update(skip_locked=True)
        .cte("batch")
        .prefix_with("MATERIALIZED")
    )
    return delete(table).where(table.id.in_(select(batch.c.id)))


PLAN_WITH_VALUES = text("SET LOCAL plan_cache_mode = force_custom_plan")
"""The first statement of a purge across tenants, in the purge's own
transaction. The driver prepares each statement once per connection. After
five runs Postgres may keep the generic plan, which knows no value. It
guesses that a third of the table is past the cut, so a scan of the whole
table that stops at the limit looks cheap. When the first five runs on a
connection meet a backlog, that plan wins and stays, and every later pass
reads the whole table with nothing to purge. Planned with its values, each
statement reads its index.

An order by the retention column would keep the generic plan on the index
too, but not a backlog's plan: the system scope's policy makes the planner
count a few rows where there are thousands, so it sorts the whole backlog to
take one batch. A purge runs a few times a pass, so planning it each time
costs nothing that shows. `SET LOCAL` ends with the transaction, so no other
statement on the connection is planned this way."""


def deleted(result: Result[Any]) -> int:
    """The rows a DELETE took, as the driver reports them, so a purge counts
    what went without carrying every id back to count it."""
    return cast(CursorResult[Any], result).rowcount


class PgStorageBase:
    def __init__(self, sessions: Mapping[DatabaseRole, SessionFactory]) -> None:
        """`sessions` is a `LoginSessions`: a plain mapping names one login,
        and the system scope has a login of its own, so it is refused rather
        than read as both."""
        if not isinstance(sessions, LoginSessions):
            raise TypeError("PgStorageBase takes LoginSessions: the runtime and the system login")
        self._sessions: LoginSessions = sessions

    @asynccontextmanager
    async def _session_for(
        self,
        target: Any,
        *,
        org_id: UUID,
        user_id: UUID | None = None,
        identity_id: UUID | None = None,
    ) -> AsyncIterator[AsyncSession]:
        """A short session on the pool of the one role `target` touches, opened
        under the scope of the call. Every connection of that pool carries the
        role's statement deadline from the moment it opens, so no statement
        here asks for one.

        This is the funnel: every statement of every impl passes here, so the
        tenant is named once per transaction and not once per query. `org_id`
        is required; `EMPTY_UUID` is the system scope, the transaction that
        reads across tenants, and it is never a default. `user_id` narrows a
        `both`-scoped table that names the user to one person, and
        `identity_id` narrows one whose person is the identity behind it
        (`users`) and is the person of an `identity`-scoped table.

        The system scope runs on the system login's pool and every other
        scope on the runtime login's. The policies admit the system scope to
        the system login alone, so a runtime connection that names
        `EMPTY_UUID` reads nothing.

        The settings go out in the message that begins the transaction, so
        they land before anything else, and that message is the one that may
        be sent twice (`scoped_session`). A commit or a rollback ends that
        transaction and takes them with it, which is why a method that runs a
        second transaction opens a second session.

        It is also where the driver's failures to answer in time are
        translated, once for every role and both logins: a checkout past its
        bound and a statement past its deadline leave here as `Unavailable`
        (`not_in_time`), never as the driver's own error."""
        role = role_of(target)
        system = org_id == EMPTY_UUID
        logins = self._sessions.system if system else self._sessions
        factory = logins[role]
        try:
            async with await scoped_session(factory, org_id, user_id, identity_id) as session:
                yield session
        except Exception as error:
            refusal = not_in_time(error, role, "system" if system else "runtime")
            if refusal is None:
                raise
            raise refusal from error

    async def _upsert(
        self,
        row_type: type[Any],
        org_id: UUID,
        entity: Identifiable,
        outbox_rows: tuple[OutboxRow, ...] = (),
    ) -> None:
        """Insert or update by id, refusing to overwrite another tenant's row, then
        commit. The `outbox_rows` are inserted in the same commit: the core row
        and its handoffs land together or not at all (the transactional outbox),
        which is why every table with an outbox row lives in the `core` role. An
        entity change is one row; a write that also starts work carries a second
        row of kind `work.<kind>` in the same tuple, because the queue is a role
        of its own and no statement reaches both.

        A write never brings a soft-deleted row back. Every update here is a
        read, a copy, and a write of the whole entity, so a delete that commits
        between the read and the write would otherwise be undone by an
        `updated_at` copy that still carries `deleted_at = NULL`. There is no
        restore in this domain; `RowDeleted` says the row went while the
        caller was holding it, and the caller reads it again.

        Another tenant's row is refused twice. The read names the tenant on
        the row and says so, which is the fence this layer relies on. Under
        the policy that read never returns the row at all, so the insert that
        follows meets the primary key instead, and a primary key the read did
        not see is a row this transaction may not see: the same refusal, from
        the key rather than from the column. In a race inside one tenant, two
        writers of one new id meet the same key; the loser is told the row is
        not its tenant's when it is, and answers a Conflict the way it answers
        the other one, by reading the row again."""
        entity_id = entity.id
        if outbox_rows and role_of(row_type) is not role_of(OutboxRows):
            raise CrossRoleStatement(
                f"{row_type.__tablename__} is not in the outbox's role; no outbox row"
            )
        async with self._session_for(row_type, org_id=org_id) as session:
            row = await session.get(row_type, entity_id)
            if row is None:
                session.add(to_row(entity, row_type, org_id=org_id))
            else:
                if row.org_id != org_id:
                    raise TenantMismatch(f"{row_type.__tablename__} {entity_id} is not in {org_id}")
                if undeletes(row, entity):
                    raise RowDeleted(f"{row_type.__tablename__} {entity_id} was deleted")
                apply_row(row, entity)
            for outbox_row in outbox_rows:
                session.add(to_row(outbox_row, OutboxRows, org_id=org_id))
            try:
                await session.commit()
            except IntegrityError as error:
                # A key race the read did not see; a Conflict, never a driver error.
                constraint = violated_constraint(error)
                if row is None and constraint == row_type.__table__.primary_key.name:
                    raise TenantMismatch(
                        f"{row_type.__tablename__} {entity_id} is not in {org_id}"
                    ) from error
                raise UniqueKeyTaken(
                    f"{row_type.__tablename__} {entity.id}: {constraint or 'a unique key'} is taken"
                ) from error

    async def _insert(
        self,
        row_type: type[Any],
        org_id: UUID,
        entity: Identifiable,
        outbox_rows: tuple[OutboxRow, ...] = (),
        **extra: Any,
    ) -> bool:
        """The create primitive: insert by id and commit, with the outbox rows in
        the same commit; `extra` sets storage-only columns beside the entity's.
        False when the id is already written, in which case
        nothing changes, the outbox rows included. Ids are minted above storage,
        so an existing id is a retry, and a retry must neither overwrite the row
        nor announce it twice. Only the primary key reports False: any other
        unique key the row violates is `UniqueKeyTaken`, a Conflict, never a
        driver error and never mistaken for a retry."""
        if outbox_rows and role_of(row_type) is not role_of(OutboxRows):
            raise CrossRoleStatement(
                f"{row_type.__tablename__} is not in the outbox's role; no outbox row"
            )
        async with self._session_for(row_type, org_id=org_id) as session:
            session.add(to_row(entity, row_type, org_id=org_id, **extra))
            for outbox_row in outbox_rows:
                session.add(to_row(outbox_row, OutboxRows, org_id=org_id))
            try:
                await session.commit()
            except IntegrityError as error:
                await session.rollback()
                constraint = violated_constraint(error)
                if constraint == row_type.__table__.primary_key.name:
                    return False
                raise UniqueKeyTaken(
                    f"{row_type.__tablename__} {entity.id}: {constraint or 'a unique key'} is taken"
                ) from error
            return True

    async def _upsert_global(self, row_type: type[Any], entity: Identifiable) -> None:
        """The same primitive for a global table, which has no tenant to check;
        a unique key the read did not see is `UniqueKeyTaken` here too. A
        global table is `system`-scoped and carries no policy, so the funnel
        takes the system scope: `EMPTY_UUID`, spelled here and nowhere in a
        caller. Every caller of this primitive is in the enumerated
        exceptions, because a global row is written with no tenant in hand."""
        entity_id = entity.id
        async with self._session_for(row_type, org_id=EMPTY_UUID) as session:
            row = await session.get(row_type, entity_id)
            if row is None:
                session.add(to_row(entity, row_type))
            else:
                apply_row(row, entity)
            try:
                await session.commit()
            except IntegrityError as error:
                raise UniqueKeyTaken(
                    f"{row_type.__tablename__} {entity_id}: "
                    f"{violated_constraint(error) or 'a unique key'} is taken"
                ) from error
