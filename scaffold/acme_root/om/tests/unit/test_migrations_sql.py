"""The migration files obey the role rules without a database."""

import shutil
from pathlib import Path

import pytest

from acme.om.media.types.file import FileStatus
from acme.om.storage.migrate import (
    MIGRATIONS_DIR,
    backfill_statements,
    chain,
    check_role_of_sql,
    head,
    migrations_of,
    role_metadata,
    split_statements,
)
from acme.om.storage.roles import DatabaseRole


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
        "CREATE TRIGGER t BEFORE INSERT ON core.orgs FOR EACH ROW EXECUTE FUNCTION core.f();\n"
    )
    assert split_statements(sql) == [
        "CREATE FUNCTION core.f() RETURNS trigger LANGUAGE plpgsql AS $$\n"
        "BEGIN\n    NEW.a := 1;\n    RETURN NEW;\nEND\n$$",
        "CREATE TRIGGER t BEFORE INSERT ON core.orgs FOR EACH ROW EXECUTE FUNCTION core.f()",
    ]


def test_a_function_is_checked_by_its_schema_alone() -> None:
    check_role_of_sql(DatabaseRole.CORE, "DROP FUNCTION core.touch_updated_at()")
    with pytest.raises(RuntimeError):
        check_role_of_sql(DatabaseRole.CORE, "CREATE FUNCTION queue.f() RETURNS trigger")


def test_a_data_migrations_file_is_a_count_then_an_update(tmp_path: Path) -> None:
    """What `run_backfill` reads from the file its wrapper names: the count
    it compares the update's rows with, and the update. An update with no
    count could not tell a backfill that touched nothing from one that had
    nothing to do, so it is refused."""
    (tmp_path / "sql" / "core").mkdir(parents=True)
    count = "SELECT count(*) FROM core.users WHERE locale IS NULL"
    update = "UPDATE core.users SET locale = 'en' WHERE locale IS NULL"
    (tmp_path / "sql" / "core" / "a.up.sql").write_text(f"-- the backfill\n{count};\n{update};\n")
    (tmp_path / "sql" / "core" / "b.up.sql").write_text(f"{update};\n")
    assert backfill_statements(DatabaseRole.CORE, "a.up.sql", tmp_path) == (count, update)
    with pytest.raises(RuntimeError, match="holds 1 statements"):
        backfill_statements(DatabaseRole.CORE, "b.up.sql", tmp_path)


def assert_one_chain_of_stamped_wrappers(
    role: DatabaseRole, migrations_dir: Path = MIGRATIONS_DIR
) -> None:
    """The gate on a role's chain: one head, one first revision, and every
    parent present (`chain` refuses the rest), every wrapper in it, named by
    its stamp, with its SQL pair. The order is the parents', so a wrapper's
    stamp may sort below its parent's."""
    versions = migrations_dir / "versions" / role.value
    wrappers = sorted(versions.glob("*.py")) if versions.is_dir() else []
    revisions = chain(role, migrations_dir)
    assert sorted(revisions) == sorted(wrapper.stem.split("_", 1)[0] for wrapper in wrappers)
    assert head(role, migrations_dir) == (revisions[-1] if revisions else None)
    for wrapper in wrappers:
        stamp = wrapper.stem.split("_", 1)[0]
        assert len(stamp) == 12 and stamp.isdigit(), wrapper.name
        assert f'revision = "{stamp}"' in wrapper.read_text()
        for suffix in (".up.sql", ".down.sql"):
            assert (migrations_dir / "sql" / role.value / f"{wrapper.stem}{suffix}").is_file()


@pytest.mark.parametrize("role", list(DatabaseRole))
def test_each_role_is_one_chain_of_stamped_wrappers(role: DatabaseRole) -> None:
    assert_one_chain_of_stamped_wrappers(role)


def a_chain(tmp_path: Path, parents: dict[str, str | None]) -> Path:
    """A checkout whose admin chain is `parents`, each revision's wrapper
    naming its parent, with its SQL pair."""
    there = migrations_of(tmp_path)
    (there / "versions" / "admin").mkdir(parents=True)
    (there / "sql" / "admin").mkdir(parents=True)
    shutil.copy(MIGRATIONS_DIR / "alembic.ini", there)
    for revision, parent in parents.items():
        stem = f"{revision}_step"
        (there / "versions" / "admin" / f"{stem}.py").write_text(
            f'revision = "{revision}"\n'
            f"down_revision = {parent!r}\n"
            "branch_labels = None\n"
            "depends_on = None\n"
        )
        for suffix in (".up.sql", ".down.sql"):
            (there / "sql" / "admin" / f"{stem}{suffix}").write_text("SELECT 1;\n")
    return there


def test_a_chain_is_ordered_by_its_parents_never_by_its_stamps(tmp_path: Path) -> None:
    """A base's migration written after a tree's own sorts above it, and the
    tree's own then follows it on the chain: its stamp sorts below its
    parent's, and the gate passes it under its own name."""
    there = a_chain(
        tmp_path,
        {"202601010000": None, "202602010000": "202601010000", "202512010000": "202602010000"},
    )
    assert_one_chain_of_stamped_wrappers(DatabaseRole.ADMIN, there)
    assert chain(DatabaseRole.ADMIN, there) == ["202601010000", "202602010000", "202512010000"]
    assert head(DatabaseRole.ADMIN, there) == "202512010000"


@pytest.mark.parametrize(
    ("parents", "refusal"),
    [
        (
            {"202601010000": None, "202602010000": "202601010000", "202603010000": "202601010000"},
            "2 heads",
        ),
        (
            {"202601010000": None, "202602010000": "202501010000"},
            "a parent its chain does not hold",
        ),
    ],
    ids=["two heads", "a missing parent"],
)
def test_a_chain_with_two_heads_or_a_missing_parent_fails_the_gate(
    tmp_path: Path, parents: dict[str, str | None], refusal: str
) -> None:
    there = a_chain(tmp_path, parents)
    with pytest.raises(RuntimeError, match=refusal):
        assert_one_chain_of_stamped_wrappers(DatabaseRole.ADMIN, there)


def test_the_head_of_another_checkouts_chain_is_read_from_its_files(
    checkout_ahead: tuple[Path, str],
) -> None:
    """What `migrate stamp --heads-of` writes: the head each chain has in that
    checkout, a revision this checkout's chain does not hold included."""
    checkout, ahead = checkout_ahead
    there = migrations_of(checkout)
    assert head(DatabaseRole.CORE, there) == ahead
    for role in (DatabaseRole.ACTIVITY, DatabaseRole.QUEUE, DatabaseRole.ADMIN):
        assert head(role, there) == head(role)
    assert migrations_of(MIGRATIONS_DIR.parents[1]) == MIGRATIONS_DIR


def test_role_metadata_holds_only_that_role() -> None:
    core = role_metadata(DatabaseRole.CORE)
    assert {t.name for t in core.tables.values()} >= {"orgs", "identities", "users"}
    assert all(t.schema == "core" for t in core.tables.values())
    assert {t.name for t in role_metadata(DatabaseRole.QUEUE).tables.values()} == {
        "work_items",
        "tenant_caps",
    }
    assert {t.name for t in role_metadata(DatabaseRole.ACTIVITY).tables.values()} == {
        "change_events",
        "change_event_cursors",
    }
    assert {t.name for t in role_metadata(DatabaseRole.ADMIN).tables.values()} == {"system_sizes"}


SWEEP_INDEXES = {
    "users": "ix_users_org_id_deleted_at",
    "memberships": "ix_memberships_org_id_deleted_at",
    "files": "ix_files_org_id_deleted_at",
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
    "ix_idempotency_records_attempt_id": "status IS NULL",
    "ix_files_deleted_at": "deleted_at IS NOT NULL",
    "ix_users_deleted_at": "deleted_at IS NOT NULL",
    "ix_memberships_deleted_at": "deleted_at IS NOT NULL",
    "ix_api_keys_deleted_at": "deleted_at IS NOT NULL",
}
"""The partial indexes the fence and the purges across tenants read, with
their predicates."""


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
