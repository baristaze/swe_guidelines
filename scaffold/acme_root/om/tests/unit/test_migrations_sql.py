"""The migration files obey the role rules without a database."""

import pytest
from sqlalchemy.dialects import postgresql

from acme.om.media.types.file import FileStatus
from acme.om.storage.migrate import (
    MIGRATIONS_DIR,
    check_role_of_sql,
    head,
    role_metadata,
    split_statements,
)
from acme.om.storage.roles import DatabaseRole
from acme.om.tasks.rules import RANK_SCALE_BOUND
from acme.om.tasks.storage.impl.postgres import (  # pyright: ignore[reportPrivateUsage]
    _DONE_SHELF,
    _LONG_RANK,
)
from acme.om.tasks.types.task import TaskStatus


def test_every_sql_file_names_only_its_own_role() -> None:
    sql_files = list((MIGRATIONS_DIR / "sql").rglob("*.sql"))
    assert sql_files
    for path in sql_files:
        check_role_of_sql(DatabaseRole(path.parent.name), path.read_text())


def test_a_file_naming_another_role_is_refused() -> None:
    with pytest.raises(RuntimeError):
        check_role_of_sql(DatabaseRole.CORE, "CREATE TABLE queue.work_items (id uuid)")
    with pytest.raises(LookupError):
        check_role_of_sql(DatabaseRole.CORE, "CREATE TABLE core.unknown_table (id uuid)")
    check_role_of_sql(DatabaseRole.QUEUE, "DROP INDEX queue.ix_work_items_org_id")
    check_role_of_sql(DatabaseRole.QUEUE, "ALTER INDEX queue.ix_work_items_a RENAME TO ix_b")
    with pytest.raises(RuntimeError):
        check_role_of_sql(DatabaseRole.CORE, "DROP INDEX queue.ix_work_items_org_id")


def test_split_statements_drops_comments_and_blanks() -> None:
    sql = (
        "-- header\nCREATE TABLE core.orgs (id uuid);\n\n"
        "-- more\nCREATE INDEX ix ON core.orgs (id);\n"
    )
    assert split_statements(sql) == [
        "CREATE TABLE core.orgs (id uuid)",
        "CREATE INDEX ix ON core.orgs (id)",
    ]


def test_split_statements_keeps_a_function_body_whole() -> None:
    sql = (
        "-- a trigger\nCREATE FUNCTION core.f() RETURNS trigger LANGUAGE plpgsql AS $$\n"
        "BEGIN\n    NEW.a := 1;\n    RETURN NEW;\nEND\n$$;\n"
        "CREATE TRIGGER t BEFORE INSERT ON core.tasks FOR EACH ROW EXECUTE FUNCTION core.f();\n"
    )
    assert split_statements(sql) == [
        "CREATE FUNCTION core.f() RETURNS trigger LANGUAGE plpgsql AS $$\n"
        "BEGIN\n    NEW.a := 1;\n    RETURN NEW;\nEND\n$$",
        "CREATE TRIGGER t BEFORE INSERT ON core.tasks FOR EACH ROW EXECUTE FUNCTION core.f()",
    ]


def test_a_function_is_checked_by_its_schema_alone() -> None:
    check_role_of_sql(DatabaseRole.CORE, "DROP FUNCTION core.tasks_rank_from_position()")
    with pytest.raises(RuntimeError):
        check_role_of_sql(DatabaseRole.CORE, "CREATE FUNCTION queue.f() RETURNS trigger")


@pytest.mark.parametrize("role", list(DatabaseRole))
def test_each_role_has_at_most_one_head_and_stamped_wrappers(role: DatabaseRole) -> None:
    versions = MIGRATIONS_DIR / "versions" / role.value
    wrappers = sorted(versions.glob("*.py")) if versions.is_dir() else []
    if not wrappers:
        assert head(role) is None
        return
    assert head(role) == wrappers[-1].stem.split("_", 1)[0]
    for wrapper in wrappers:
        stamp = wrapper.stem.split("_", 1)[0]
        assert len(stamp) == 12 and stamp.isdigit(), wrapper.name
        assert f'revision = "{stamp}"' in wrapper.read_text()
        for suffix in (".up.sql", ".down.sql"):
            assert (MIGRATIONS_DIR / "sql" / role.value / f"{wrapper.stem}{suffix}").is_file()


def test_role_metadata_holds_only_that_role() -> None:
    core = role_metadata(DatabaseRole.CORE)
    assert {t.name for t in core.tables.values()} >= {"orgs", "identities", "users"}
    assert all(t.schema == "core" for t in core.tables.values())
    assert {t.name for t in role_metadata(DatabaseRole.QUEUE).tables.values()} == {"work_items"}
    assert {t.name for t in role_metadata(DatabaseRole.ACTIVITY).tables.values()} == {
        "events",
        "event_cursors",
    }
    assert {t.name for t in role_metadata(DatabaseRole.ADMIN).tables.values()} == {"platform_sizes"}


SWEEP_INDEXES = {
    "users": "ix_users_org_id_deleted_at",
    "memberships": "ix_memberships_org_id_deleted_at",
    "slack_installations": "ix_slack_installations_org_id_deleted_at",
}
"""The index the per-tenant purge needs on each soft-deletable table whose
only other org_id index is partial."""


@pytest.mark.parametrize(("table", "name"), sorted(SWEEP_INDEXES.items()))
def test_the_purge_of_a_tenant_has_an_index_the_orm_and_the_chain_agree_on(
    table: str, name: str
) -> None:
    """The purge of a tenant past its retention reads `org_id = X` alone, the
    member list reads `org_id = X AND deleted_at IS NULL`, and the purge of
    the memberships of the users the sweep removes reads by tenant too. The
    first and the last target rows a partial unique index `WHERE deleted_at
    IS NULL` leaves out, so each table carries a plain (org_id, deleted_at)
    index. `make migrate-check` compares the ORM metadata with the migrated
    schema, so both say it."""
    orm = role_metadata(DatabaseRole.CORE).tables[f"core.{table}"]
    index = next((i for i in orm.indexes if i.name == name), None)
    assert index is not None, f"{table} declares no {name}"
    assert [c.name for c in index.columns] == ["org_id", "deleted_at"]
    assert not index.unique
    assert index.dialect_kwargs.get("postgresql_where") is None, "the dead rows are the point"
    chain = "\n".join(
        path.read_text() for path in (MIGRATIONS_DIR / "sql" / "core").glob("*.up.sql")
    )
    assert f"CREATE INDEX {name} ON core.{table} (org_id, deleted_at)" in chain


def test_the_re_mint_fence_has_an_index_the_orm_and_the_chain_agree_on() -> None:
    """The re-mint of a secret is conditional on the marker still holding the
    attempt making the write, so the rerun's statement reads the pending
    marker by its attempt. `uq_idempotency_records_org_id_user_id_key` leads
    with the caller's key, so the fence has an index over the pending
    markers, the only ones it reads. An attempt token is minted once, so the
    attempt alone finds the one marker, and the purge reads the abandoned
    attempts of every tenant through the same index. `make migrate-check`
    compares the ORM metadata with the migrated schema, so both say it."""
    name = "ix_idempotency_records_attempt_id"
    orm = role_metadata(DatabaseRole.CORE).tables["core.idempotency_records"]
    index = next((i for i in orm.indexes if i.name == name), None)
    assert index is not None, f"the markers declare no {name}"
    assert [c.name for c in index.columns] == ["attempt_id"]
    assert not index.unique
    assert str(index.dialect_kwargs["postgresql_where"]) == "status IS NULL"
    chain = "\n".join(
        path.read_text() for path in (MIGRATIONS_DIR / "sql" / "core").glob("*.up.sql")
    )
    assert (
        f"CREATE INDEX {name} ON core.idempotency_records (attempt_id) WHERE status IS NULL"
    ) in chain


PARTIAL_INDEXES = {
    "ix_tasks_org_id_status_updated_at_id_unarchived": "archived_at IS NULL AND deleted_at IS NULL",
    "ix_tasks_org_id_status_updated_at_id_archived": (
        "archived_at IS NOT NULL AND deleted_at IS NULL"
    ),
    "ix_tasks_org_id_assignee_id_status": "deleted_at IS NULL",
    "ix_tasks_org_id_created_by_status": "assignee_id IS NULL AND deleted_at IS NULL",
    "ix_idempotency_records_attempt_id": "status IS NULL",
    "ix_tasks_deleted_at": "deleted_at IS NOT NULL",
    "ix_files_deleted_at": "deleted_at IS NOT NULL",
    "ix_users_deleted_at": "deleted_at IS NOT NULL",
    "ix_memberships_deleted_at": "deleted_at IS NOT NULL",
    "ix_api_keys_deleted_at": "deleted_at IS NOT NULL",
    "ix_slack_installations_deleted_at": "deleted_at IS NOT NULL",
    "ix_slack_install_states_redeemed_at": "redeemed_at IS NOT NULL",
}
"""The partial indexes the task lists, the cleanup, the fence, and the purges
across tenants read, with their predicates."""


@pytest.mark.parametrize(("name", "predicate"), sorted(PARTIAL_INDEXES.items()))
def test_a_partial_index_names_no_bound_value(name: str, predicate: str) -> None:
    """A generic plan, the one a prepared statement reaches after five runs,
    cannot prove a partial predicate on a value bound as a parameter, so it
    would never use such an index. The storage binds every value it compares,
    `status` included; only a literal test, IS NULL or IS NOT NULL, is safe."""
    index = next(
        i
        for t in role_metadata(DatabaseRole.CORE).tables.values()
        for i in t.indexes
        if i.name == name
    )
    assert str(index.dialect_kwargs["postgresql_where"]) == predicate
    assert not any(op in predicate for op in ("=", "<", ">", " IN ")), predicate


def test_the_long_rank_index_and_its_read_spell_one_literal_bound() -> None:
    """The one partial index with a comparison: the respace's read names the
    bound as a literal, the same one the predicate and the chain name, so a
    generic plan still proves the predicate and reads the index."""
    index = next(
        i
        for t in role_metadata(DatabaseRole.CORE).tables.values()
        for i in t.indexes
        if i.name == "ix_tasks_org_id_rank_long"
    )
    predicate = f"scale(rank) > {RANK_SCALE_BOUND} AND deleted_at IS NULL"
    assert str(index.dialect_kwargs["postgresql_where"]) == predicate
    assert [c.name for c in index.columns] == ["org_id", "rank"]
    compiled = str(_LONG_RANK.compile(dialect=postgresql.dialect()))
    assert compiled.endswith(f"> {RANK_SCALE_BOUND}") and "%(" not in compiled
    chain = "\n".join(
        path.read_text() for path in sorted((MIGRATIONS_DIR / "sql" / "core").glob("*.up.sql"))
    )
    assert f"(org_id, rank) WHERE {predicate};" in chain


def test_the_pending_uploads_index_and_the_migration_spell_one_literal() -> None:
    """The index of pending uploads names the status in its predicate, and
    the chain builds it with that predicate. The files purge names the status
    as the same literal, so a generic plan proves the predicate and reads the
    index; the integration suite reads that plan."""
    index = next(
        i
        for t in role_metadata(DatabaseRole.CORE).tables.values()
        for i in t.indexes
        if i.name == "ix_files_created_at_pending"
    )
    predicate = f"deleted_at IS NULL AND status = '{FileStatus.PENDING.value}'"
    assert str(index.dialect_kwargs["postgresql_where"]) == predicate
    assert [c.name for c in index.columns] == ["created_at"]
    chain = "\n".join(
        path.read_text() for path in sorted((MIGRATIONS_DIR / "sql" / "core").glob("*.up.sql"))
    )
    assert f"core.files (created_at) WHERE {predicate};" in chain


def test_the_done_shelf_index_and_its_read_spell_one_literal() -> None:
    """The index the sweep's read of the tenants with a chore due walks names
    the done status in its predicate, and the chain builds it with that
    predicate. The read names the status as the same literal, so a generic
    plan proves the predicate and reads the index; the integration suite
    reads that plan."""
    index = next(
        i
        for t in role_metadata(DatabaseRole.CORE).tables.values()
        for i in t.indexes
        if i.name == "ix_tasks_updated_at_org_id_done_unarchived"
    )
    predicate = f"status = '{TaskStatus.DONE.value}' AND archived_at IS NULL AND deleted_at IS NULL"
    assert str(index.dialect_kwargs["postgresql_where"]) == predicate
    assert [c.name for c in index.columns] == ["updated_at", "org_id"]
    compiled = str(_DONE_SHELF.compile(dialect=postgresql.dialect()))
    assert f"= '{TaskStatus.DONE.value}'" in compiled and "%(" not in compiled
    chain = "\n".join(
        path.read_text() for path in sorted((MIGRATIONS_DIR / "sql" / "core").glob("*.up.sql"))
    )
    assert f"core.tasks (updated_at, org_id) WHERE {predicate};" in chain
