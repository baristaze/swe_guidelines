"""checkers/src/arch_check/rules/storage.py: the storage lenses a parser decides.

`GOOD` is a storage layer shaped the way the guideline prescribes: one
namespace (`widgets`, beside the base tree's `tasks`) with its interface,
impl, and tables, the shared mixins, the role and scope maps, the storage
root with a getter per namespace and two impls, and one migration of the
`core` role. Every rule passes on it; each fail
case bends one file.
"""

import pytest

pytest.importorskip("tomllib")

from arch_check_fixtures import check_json, rules_found, write_project

OM = "om/src/acme/om"
BASE = f"{OM}/storage/tables/base.py"
WIDGETS = f"{OM}/widgets/storage/tables/widgets.py"
CATALOG = f"{OM}/widgets/storage/tables/catalog.py"
IFACE = f"{OM}/widgets/storage/__init__.py"
ROOT = f"{OM}/storage/root.py"
ROLES = f"{OM}/storage/roles.py"
SCOPES = f"{OM}/storage/scopes.py"
PG = f"{OM}/storage/impl/postgres.py"
MEMORY = f"{OM}/storage/impl/memory.py"
UP = "om/migrations/sql/core/202601010000_initial.up.sql"
DOWN = "om/migrations/sql/core/202601010000_initial.down.sql"
WRAPPER = "om/migrations/versions/core/202601010000_initial.py"

GOOD: dict[str, str] = {
    f"{OM}/storage/__init__.py": "",
    f"{OM}/storage/impl/__init__.py": "",
    f"{OM}/storage/tables/__init__.py": "",
    ROLES: """\
from enum import Enum


class DatabaseRole(str, Enum):
    CORE = "core"
    ACTIVITY = "activity"


TABLE_ROLES = {
    "widgets": DatabaseRole.CORE,
    "catalog": DatabaseRole.CORE,
}
""",
    SCOPES: """\
TABLE_SCOPES = {
    "widgets": TableScope(ScopeKind.ORG),
    "catalog": TableScope(ScopeKind.SYSTEM),
}
""",
    BASE: """\
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class IdentifiableMixin:
    id: Mapped[UUID] = mapped_column(primary_key=True, sort_order=-1000)
    org_id: Mapped[UUID] = mapped_column(index=True, sort_order=-999)


class FeedIdentifiableMixin:
    id: Mapped[UUID] = mapped_column(primary_key=True, sort_order=-1000)
    org_id: Mapped[UUID] = mapped_column(sort_order=-999)


class GlobalIdentifiableMixin:
    id: Mapped[UUID] = mapped_column(primary_key=True, sort_order=-1000)


class NamedMixin:
    name: Mapped[str] = mapped_column(sort_order=-900)


class CreatedMixin:
    created_at: Mapped[datetime] = mapped_column(sort_order=-800)


class TrackableMixin(CreatedMixin):
    updated_at: Mapped[datetime] = mapped_column(sort_order=-799)
    created_by: Mapped[UUID] = mapped_column(sort_order=-798)
    updated_by: Mapped[UUID] = mapped_column(sort_order=-797)


class SoftDeletableMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(sort_order=-700)
    deleted_by: Mapped[UUID | None] = mapped_column(sort_order=-699)
""",
    f"{OM}/widgets/__init__.py": "",
    f"{OM}/widgets/storage/impl/__init__.py": "",
    f"{OM}/widgets/storage/tables/__init__.py": "",
    IFACE: """\
from abc import ABC, abstractmethod


class WidgetsStorageInterface(ABC):
    @abstractmethod
    async def read_widgets(self, org_id: UUID, limit: int) -> list[Widget]: ...

    @abstractmethod
    async def create_widget(self, org_id: UUID, widget: Widget, outbox_rows: tuple[OutboxRow, ...]) -> bool: ...
""",
    f"{OM}/widgets/storage/impl/postgres.py": """\
from acme.om.widgets.storage import WidgetsStorageInterface

CLAIM = "SELECT id FROM core.widgets FOR UPDATE SKIP LOCKED"


class WidgetsStoragePostgresImpl(WidgetsStorageInterface):
    async def read_widgets(self, org_id, limit):
        async with self._sessions() as session:
            await session.commit()
        return []
""",
    WIDGETS: """\
from sqlalchemy import Index, text

from acme.om.storage.tables.base import Base, IdentifiableMixin, NamedMixin, SoftDeletableMixin, TrackableMixin


class Widgets(IdentifiableMixin, NamedMixin, TrackableMixin, SoftDeletableMixin, Base):
    __tablename__ = "widgets"
    __table_args__ = (
        Index("uq_widgets_slug", "slug", unique=True, postgresql_where=text("deleted_at IS NULL")),
    )
    slug: Mapped[str]
    parent_id: Mapped[UUID | None] = mapped_column(ForeignKey("core.catalog.id"))
""",
    CATALOG: """\
from acme.om.storage.tables.base import Base, CreatedMixin, GlobalIdentifiableMixin


class Catalog(GlobalIdentifiableMixin, CreatedMixin, Base):
    __tablename__ = "catalog"
    title: Mapped[str]
""",
    ROOT: """\
from abc import ABC, abstractmethod

from acme.om.tasks.storage import TasksStorageInterface
from acme.om.tenancy.storage import TenancyStorageInterface
from acme.om.widgets.storage import WidgetsStorageInterface


class StorageInterface(ABC):
    @abstractmethod
    def get_tasks_storage(self) -> TasksStorageInterface: ...

    @abstractmethod
    def get_tenancy_storage(self) -> TenancyStorageInterface: ...

    @abstractmethod
    def get_widgets_storage(self) -> WidgetsStorageInterface: ...

    @abstractmethod
    async def healthcheck(self) -> bool: ...

    @abstractmethod
    async def close(self) -> None: ...
""",
    MEMORY: """\
class StorageMemoryImpl(StorageInterface):
    def get_tasks_storage(self):
        return self._tasks

    def get_tenancy_storage(self):
        return self._tenancy

    def get_widgets_storage(self):
        return self._widgets

    async def healthcheck(self):
        return True

    async def close(self):
        return None
""",
    PG: """\
from sqlalchemy.ext.asyncio import create_async_engine


def engine(url, pool):
    return create_async_engine(url, pool_size=pool.size, pool_timeout=pool.checkout_timeout)


class StoragePostgresImpl(StorageInterface):
    def get_tasks_storage(self):
        return self._tasks

    def get_tenancy_storage(self):
        return self._tenancy

    def get_widgets_storage(self):
        return self._widgets

    async def healthcheck(self):
        return True

    async def close(self):
        return None
""",
    UP: """\
-- The first tables. A comment may say CREATE FUNCTION and activity.events freely.
CREATE TABLE core.widgets (id uuid PRIMARY KEY, org_id uuid NOT NULL);
CREATE TABLE core.catalog (id uuid PRIMARY KEY, title text NOT NULL DEFAULT 'activity.x');
ALTER TABLE core.widgets ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.widgets FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.widgets FOR ALL USING (org_id = current_setting('app.org_id')::uuid);
""",
    DOWN: "DROP TABLE core.catalog;\nDROP TABLE core.widgets;\n",
    WRAPPER: """\
\"\"\"The first tables.\"\"\"

from acme.om.storage.migrate import run_sql
from acme.om.storage.roles import DatabaseRole

revision = "202601010000"
down_revision = None


def upgrade() -> None:
    \"\"\"Up.\"\"\"
    run_sql(DatabaseRole.CORE, "202601010000_initial.up.sql")


def downgrade() -> None:
    run_sql(DatabaseRole.CORE, "202601010000_initial.down.sql")
""",
}

RULES = [
    "STO-01",
    "STO-02",
    "STO-03",
    "STO-05",
    "STO-06",
    "STO-08",
    "STO-09",
    "STO-10",
    "STO-11",
    "STO-12",
    "STO-13",
    "STO-14",
    "STO-17",
    "STO-18",
    "STO-20",
    "STO-23",
    "STO-26",
    "STO-27",
    "STO-28",
    "STO-29",
]


def run(tmp_path, rule, files=None, drop=()):
    tree = {**GOOD, **(files or {})}
    for rel in drop:
        tree.pop(rel)
    write_project(tmp_path, tree)
    return check_json(tmp_path, "--rule", rule)


def edit(rel, old, new):
    assert old in GOOD[rel], f"{rel} does not contain {old!r}"
    return {rel: GOOD[rel].replace(old, new)}


def messages(report):
    return [f["message"] for f in report["findings"]]


def assert_messages(report, expected):
    assert sorted(messages(report)) == sorted(expected)


def test_the_group_lists_every_rule(tmp_path):
    write_project(tmp_path, GOOD)
    _, report = check_json(tmp_path, "--group", "storage")
    assert sorted(r["id"] for r in report["rules_run"]) == RULES


@pytest.mark.parametrize("rule", RULES)
def test_the_good_tree_passes(tmp_path, rule):
    code, report = run(tmp_path, rule)
    assert report["findings"] == []
    assert code == 0


@pytest.mark.parametrize("rule", RULES)
def test_the_base_tree_passes(tmp_path, rule):
    write_project(tmp_path)
    code, report = check_json(tmp_path, "--rule", rule)
    assert report["findings"] == []
    assert code == 0


def test_sto_01_a_relationship_in_a_table(tmp_path):
    files = {WIDGETS: GOOD[WIDGETS] + "    catalog = relationship('Catalog')\n"}
    code, report = run(tmp_path, "STO-01", files)
    assert code == 1
    assert [m.split(" ")[0] for m in messages(report)] == ["relationship()"]


@pytest.mark.parametrize("keyword", ['ondelete="RESTRICT"', 'ondelete="CASCADE"'])
def test_sto_01_an_ondelete_on_a_foreign_key_is_left_to_the_review(tmp_path, keyword):
    files = edit(WIDGETS, 'ForeignKey("core.catalog.id")', f'ForeignKey("core.catalog.id", {keyword})')
    code, report = run(tmp_path, "STO-01", files)
    assert report["findings"] == []
    assert code == 0


def test_sto_01_a_manager_catching_an_integrity_error(tmp_path):
    impl = "try:\n    pass\nexcept IntegrityError:\n    pass\n"
    code, report = run(tmp_path, "STO-01", {f"{OM}/widgets/impl/manager.py": impl})
    assert code == 1
    assert rules_found(report) == [("STO-01", f"{OM}/widgets/impl/manager.py", 3)]


def test_sto_02_a_manager_or_a_service_commits(tmp_path):
    files = {
        f"{OM}/widgets/impl/manager.py": "async def f(s):\n    await s.commit()\n",
        "services/api/src/acme/services/api/routes.py": "async def g(s, m):\n    await m.begin(key)\n    await s.rollback()\n",
    }
    code, report = run(tmp_path, "STO-02", files)
    assert code == 1
    assert [(p, line) for _, p, line in rules_found(report)] == [
        (f"{OM}/widgets/impl/manager.py", 2),
        ("services/api/src/acme/services/api/routes.py", 3),
    ]


def test_sto_03_a_lock_outside_a_storage_impl(tmp_path):
    files = {f"{OM}/widgets/impl/manager.py": 'Q = "SELECT 1 FOR UPDATE"\n\n\ndef f(q):\n    return q.with_for_update()\n'}
    code, report = run(tmp_path, "STO-03", files)
    assert code == 1
    assert len(report["findings"]) == 2


def test_sto_03_a_session_in_a_storage_signature(tmp_path):
    files = edit(IFACE, "from abc import", "from sqlalchemy.ext.asyncio import AsyncSession\nfrom abc import")
    files[IFACE] = files[IFACE].replace("org_id: UUID, limit: int", "session: AsyncSession, limit: int")
    code, report = run(tmp_path, "STO-03", files)
    assert code == 1
    assert "read_widgets takes AsyncSession" in messages(report)[0]


def test_sto_03_a_domain_session_is_not_a_database_session(tmp_path):
    files = edit(IFACE, "org_id: UUID, limit: int", "session: Session, limit: int")
    code, _ = run(tmp_path, "STO-03", files)
    assert code == 0


def test_sto_05_a_function_the_chain_leaves(tmp_path):
    later = "om/migrations/sql/core/202601020000_touch.up.sql"
    files = {
        later: "CREATE OR REPLACE FUNCTION core.touch() RETURNS trigger AS $$ BEGIN RETURN NEW; END $$ LANGUAGE plpgsql;\n",
        later.replace(".up.", ".down."): "DROP FUNCTION core.touch;\n",
    }
    code, report = run(tmp_path, "STO-05", files)
    assert code == 1
    assert rules_found(report) == [("STO-05", later, 1)]


def test_sto_05_a_function_a_later_migration_drops_is_history(tmp_path):
    first = "om/migrations/sql/core/202601020000_touch.up.sql"
    second = "om/migrations/sql/core/202601030000_untouch.up.sql"
    files = {
        first: (
            "CREATE FUNCTION core.touch() RETURNS trigger AS $$ $$;\n"
            "CREATE TRIGGER touch_widgets BEFORE UPDATE ON core.widgets;\n"
        ),
        second: "DROP TRIGGER IF EXISTS touch_widgets ON core.widgets;\nDROP FUNCTION IF EXISTS core.touch();\n",
    }
    code, _ = run(tmp_path, "STO-05", files)
    assert code == 0


def test_sto_05_a_database_set_timestamp(tmp_path):
    files = edit(
        BASE,
        "updated_at: Mapped[datetime] = mapped_column(sort_order=-799)",
        "updated_at: Mapped[datetime] = mapped_column(onupdate=func.now(), sort_order=-799)",
    )
    code, report = run(tmp_path, "STO-05", files)
    assert code == 1
    assert "TrackableMixin.updated_at has `onupdate=`" in messages(report)[0]


def test_sto_05_a_schema_default_and_a_computed_column_are_allowed(tmp_path):
    files = edit(
        BASE,
        "created_at: Mapped[datetime] = mapped_column(sort_order=-800)",
        "created_at: Mapped[datetime] = mapped_column(server_default=func.now(), sort_order=-800)",
    )
    files[CATALOG] = GOOD[CATALOG] + '    search: Mapped[str] = mapped_column(Computed("lower(title)"))\n'
    code, report = run(tmp_path, "STO-05", files)
    assert report["findings"] == []
    assert code == 0


@pytest.mark.parametrize(
    "column",
    [
        "mapped_column(primary_key=True, server_default=text('gen_random_uuid()'), sort_order=-1000)",
        "mapped_column(Identity(), primary_key=True, sort_order=-1000)",
        "mapped_column(primary_key=True, autoincrement=True, sort_order=-1000)",
    ],
)
def test_sto_06_a_database_minted_id(tmp_path, column):
    files = edit(
        BASE,
        "mapped_column(primary_key=True, sort_order=-1000)\n    org_id: Mapped[UUID] = mapped_column(index=True",
        f"{column}\n    org_id: Mapped[UUID] = mapped_column(index=True",
    )
    code, report = run(tmp_path, "STO-06", files)
    assert code == 1
    assert rules_found(report) == [("STO-06", BASE, 9)]


def test_sto_06_a_write_returning_a_uuid(tmp_path):
    code, report = run(tmp_path, "STO-06", edit(IFACE, "tuple[OutboxRow, ...]) -> bool", "tuple[OutboxRow, ...]) -> UUID"))
    assert code == 1
    assert "create_widget returns a UUID" in messages(report)[0]


def test_sto_08_an_interface_importing_the_orm(tmp_path):
    files = edit(IFACE, "from abc import", "from sqlalchemy import Row\nfrom abc import")
    files[f"{OM}/widgets/types/widget.py"] = "import asyncpg\n"
    code, report = run(tmp_path, "STO-08", files)
    assert code == 1
    assert [p for _, p, _ in rules_found(report)] == [IFACE, f"{OM}/widgets/types/widget.py"]


def test_sto_08_the_driver_list_comes_from_the_options(tmp_path):
    files = {f"{OM}/widgets/types/widget.py": "import sqlalchemy\n"}
    write_project(
        tmp_path,
        {**GOOD, **files},
        pyproject='[tool.arch-check]\npackage = "acme"\n\n[tool.arch-check.options.STO-08]\npackages = ["asyncpg"]\n',
    )
    code, _ = check_json(tmp_path, "--rule", "STO-08")
    assert code == 0


def test_sto_09_a_table_next_to_the_types_and_an_interface_in_impl(tmp_path):
    files = {
        f"{OM}/widgets/types/widget.py": 'class Stray(Base):\n    __tablename__ = "stray"\n',
        f"{OM}/widgets/storage/impl/memory.py": "class OtherStorageInterface(ABC):\n    pass\n",
    }
    code, report = run(tmp_path, "STO-09", files)
    assert code == 1
    assert sorted(p for _, p, _ in rules_found(report)) == sorted(files)


def test_sto_09_reads_a_table_spelled_with_table_a_declared_attr_or_core(tmp_path):
    files = {
        f"{OM}/widgets/types/a.py": "class A(Base):\n    __table__ = Table('a', metadata)\n",
        f"{OM}/widgets/types/b.py": (
            "class B(Base):\n    @declared_attr.directive\n    def __tablename__(cls):\n        return 'b'\n"
        ),
        f"{OM}/widgets/types/c.py": "import sqlalchemy as sa\n\nc = sa.Table('c', metadata)\n",
    }
    code, report = run(tmp_path, "STO-09", files)
    assert code == 1
    assert sorted({p for _, p, _ in rules_found(report)}) == sorted(files)


def test_sto_09_a_namespace_storage_without_its_parts(tmp_path):
    code, report = run(tmp_path, "STO-09", {f"{OM}/gadgets/storage/__init__.py": ""})
    assert code == 1
    assert_messages(
        report,
        [
            "acme.om.gadgets.storage defines no *StorageInterface; the interface lives here",
            "acme.om.gadgets.storage has no impl/ package",
            "acme.om.gadgets.storage has no tables/ package",
        ],
    )


def test_sto_10_a_root_missing_a_getter_and_a_misnamed_impl(tmp_path):
    files = edit(ROOT, "    @abstractmethod\n    def get_widgets_storage(self) -> WidgetsStorageInterface: ...\n\n", "")
    files[PG] = GOOD[PG].replace("StoragePostgresImpl", "PostgresStorage")
    code, report = run(tmp_path, "STO-10", files)
    assert code == 1
    assert_messages(
        report,
        [
            "StorageInterface has no getter returning WidgetsStorageInterface",
            "storage root PostgresStorage is not named Storage<Tech>Impl",
        ],
    )


def test_sto_10_a_shared_base_and_a_string_getter(tmp_path):
    files = edit(ROOT, "-> WidgetsStorageInterface: ...", '-> "WidgetsStorageInterface": ...')
    files[f"{OM}/storage/impl/base.py"] = (
        "class StorageSqlBase(StorageInterface):\n"
        "    async def healthcheck(self):\n        return True\n\n"
        "    async def close(self):\n        return None\n"
    )
    files[PG] = (
        GOOD[PG]
        .replace("class StoragePostgresImpl(StorageInterface):", "class StoragePostgresImpl(StorageSqlBase):")
        .replace("    async def healthcheck(self):\n        return True\n\n    async def close(self):\n        return None\n", "")
    )
    code, report = run(tmp_path, "STO-10", files)
    assert report["findings"] == []
    assert code == 0
    files[PG] = files[PG].replace("    def get_widgets_storage(self):\n        return self._widgets\n\n", "")
    code, report = run(tmp_path, "STO-10", files)
    assert code == 1
    assert messages(report) == ["StoragePostgresImpl does not define get_widgets_storage()"]


def test_sto_10_no_memory_root(tmp_path):
    code, report = run(tmp_path, "STO-10", drop=[MEMORY])
    assert code == 1
    assert messages(report) == ["StorageInterface needs two roots, one of them StorageMemoryImpl"]


def test_sto_11_order_redeclaration_and_a_global_org_id(tmp_path):
    files = {
        WIDGETS: GOOD[WIDGETS]
        .replace(
            "IdentifiableMixin, NamedMixin, TrackableMixin, SoftDeletableMixin, Base",
            "NamedMixin, IdentifiableMixin, Base, TrackableMixin, SoftDeletableMixin",
        )
        .replace("    slug: Mapped[str]\n", "    slug: Mapped[str]\n    updated_at: Mapped[datetime]\n"),
        CATALOG: GOOD[CATALOG].replace("    title: Mapped[str]\n", "    title: Mapped[str]\n    org_id: Mapped[UUID]\n"),
    }
    code, report = run(tmp_path, "STO-11", files)
    assert code == 1
    assert_messages(
        report,
        [
            "Catalog composes GlobalIdentifiableMixin and declares org_id",
            "Widgets lists Base before its mixins; the base goes last",
            "Widgets lists NamedMixin before IdentifiableMixin; the house order is identity, name, lifecycle, soft delete",
            "Widgets redeclares updated_at, which TrackableMixin brings",
        ],
    )


def test_sto_12_a_row_returned_by_an_impl_or_named_by_an_interface(tmp_path):
    files = edit(
        f"{OM}/widgets/storage/impl/postgres.py",
        "async def read_widgets(self, org_id, limit):",
        "async def read_widgets(self, org_id, limit) -> list[Widgets]:",
    )
    files[IFACE] = GOOD[IFACE].replace("widget: Widget,", 'widget: "Widgets",')
    files[CATALOG] = GOOD[CATALOG].replace("Base):", "Base, frozen=True):")
    code, report = run(tmp_path, "STO-12", files)
    assert code == 1
    assert len(report["findings"]) == 3


def test_sto_13_a_mixin_column_without_a_band_and_bands_out_of_order(tmp_path):
    files = edit(BASE, "name: Mapped[str] = mapped_column(sort_order=-900)", "name: Mapped[str] = mapped_column(sort_order=-650)")
    files[BASE] = files[BASE].replace(
        "deleted_by: Mapped[UUID | None] = mapped_column(sort_order=-699)", "deleted_by: Mapped[UUID | None]"
    )
    files[CATALOG] = GOOD[CATALOG].replace("title: Mapped[str]", "title: Mapped[str] = mapped_column(sort_order=-5)")
    code, report = run(tmp_path, "STO-13", files)
    assert code == 1
    assert_messages(
        report,
        [
            "Catalog sets a negative sort_order; only mixin columns lead",
            "SoftDeletableMixin.deleted_by has no negative sort_order; mixin columns lead the table",
            "CreatedMixin.created_at sorts at -800, before NamedMixin.name at -650; bands follow the house order",
            "TrackableMixin.created_by sorts at -798, before NamedMixin.name at -650; bands follow the house order",
            "TrackableMixin.updated_at sorts at -799, before NamedMixin.name at -650; bands follow the house order",
            "TrackableMixin.updated_by sorts at -797, before NamedMixin.name at -650; bands follow the house order",
        ],
    )


def test_sto_13_a_band_read_through_a_module_constant(tmp_path):
    files = edit(
        BASE,
        "class NamedMixin:\n    name: Mapped[str] = mapped_column(sort_order=-900)",
        "NAME_ORDER = -900\n\n\nclass NamedMixin:\n    name: Mapped[str] = mapped_column(sort_order=NAME_ORDER)",
    )
    files[f"{OM}/storage/tables/extra.py"] = (
        "from acme.om.storage.tables.orders import HEADER\n\n\n"
        "class NotedMixin:\n    note: Mapped[str] = mapped_column(sort_order=HEADER)\n"
    )
    code, report = run(tmp_path, "STO-13", files)
    assert report["findings"] == []
    assert code == 0
    files[BASE] = files[BASE].replace("NAME_ORDER = -900", "NAME_ORDER = -650")
    code, report = run(tmp_path, "STO-13", files)
    assert code == 1
    assert "CreatedMixin.created_at sorts at -800, before NamedMixin.name at -650; bands follow the house order" in messages(
        report
    )


def test_sto_14_an_org_id_index_beside_a_compound_one(tmp_path):
    files = edit(
        WIDGETS,
        'postgresql_where=text("deleted_at IS NULL")),',
        'postgresql_where=text("deleted_at IS NULL")),\n        Index("ix_widgets_org_id_id", "org_id", "id"),',
    )
    code, report = run(tmp_path, "STO-14", files)
    assert code == 1
    assert messages(report) == ["Widgets indexes org_id alone and leads a compound index with it"]


def test_sto_14_a_feed_mixin_or_a_flag_turns_the_single_index_off(tmp_path):
    compound = 'postgresql_where=text("deleted_at IS NULL")),\n        Index("ix_widgets_org_id_id", "org_id", "id"),'
    files = edit(WIDGETS, 'postgresql_where=text("deleted_at IS NULL")),', compound)
    files[WIDGETS] = (
        files[WIDGETS]
        .replace("(IdentifiableMixin,", "(FeedIdentifiableMixin,")
        .replace("import Base, IdentifiableMixin", "import Base, FeedIdentifiableMixin")
    )
    code, _ = run(tmp_path, "STO-14", files)
    assert code == 0
    flagged = edit(
        BASE, "mapped_column(index=True, sort_order=-999)", "mapped_column(index=cls.__org_id_index__, sort_order=-999)"
    )
    flagged[BASE] = flagged[BASE].replace(
        "class IdentifiableMixin:\n", "class IdentifiableMixin:\n    __org_id_index__: ClassVar[bool] = True\n"
    )
    flagged[WIDGETS] = (
        GOOD[WIDGETS]
        .replace('postgresql_where=text("deleted_at IS NULL")),', compound)
        .replace('__tablename__ = "widgets"', '__tablename__ = "widgets"\n    __org_id_index__ = False')
    )
    code, _ = run(tmp_path, "STO-14", flagged)
    assert code == 0


def test_sto_14_a_unique_org_id_index_beside_a_compound_one_is_a_rule_not_a_lookup(tmp_path):
    compound = (
        'postgresql_where=text("deleted_at IS NULL")),\n'
        '        Index("ix_widgets_org_id_deleted_at", "org_id", "deleted_at"),\n'
        '        Index("uq_widgets_org_id", "org_id", unique=True, postgresql_where=text("deleted_at IS NULL")),'
    )
    files = edit(WIDGETS, 'postgresql_where=text("deleted_at IS NULL")),', compound)
    files[WIDGETS] = (
        files[WIDGETS]
        .replace("(IdentifiableMixin,", "(FeedIdentifiableMixin,")
        .replace("import Base, IdentifiableMixin", "import Base, FeedIdentifiableMixin")
    )
    code, report = run(tmp_path, "STO-14", files)
    assert code == 0, messages(report)
    files[WIDGETS] = files[WIDGETS].replace(
        'Index("uq_widgets_org_id", "org_id", unique=True, postgresql_where=text("deleted_at IS NULL")),',
        'Index("ix_widgets_org_id", "org_id"),',
    )
    code, report = run(tmp_path, "STO-14", files)
    assert code == 1
    assert messages(report) == ["Widgets indexes org_id alone and leads a compound index with it"]


def test_sto_14_a_descending_id_index(tmp_path):
    code, report = run(tmp_path, "STO-14", edit(WIDGETS, '"slug", unique=True', 'text("id DESC"), unique=True'))
    assert code == 1
    assert "descending index on id" in messages(report)[0]


def test_sto_17_a_missing_table_a_stale_key_a_schema_and_a_cross_role_key(tmp_path):
    files = edit(ROLES, '"catalog": DatabaseRole.CORE,', '"catalog": DatabaseRole.ACTIVITY,\n    "gone": DatabaseRole.CORE,')
    files[f"{OM}/widgets/storage/tables/parts.py"] = (
        'class Parts(Base):\n    __tablename__ = "parts"\n    __table_args__ = ({"schema": "core"},)\n'
    )
    code, report = run(tmp_path, "STO-17", files)
    assert code == 1
    assert_messages(
        report,
        [
            "Parts declares its schema; the role map derives it",
            "TABLE_ROLES names gone, which no table class declares",
            "Widgets has a foreign key into role activity; it is in core",
            "table parts is missing from TABLE_ROLES",
        ],
    )


def test_sto_18_a_lone_file_a_bad_name_a_fat_wrapper_and_a_foreign_table(tmp_path):
    files = {
        "om/migrations/sql/core/202601020000_more.up.sql": "ALTER TABLE activity.events ADD COLUMN x int;\n",
        "om/migrations/sql/core/notes.sql": "",
        "om/migrations/versions/core/202601020000_more.py": (
            'revision = "202601020000"\n'
            'down_revision = "202601010000"\n'
            "\n"
            "\n"
            "def upgrade():\n"
            '    op.add_column("t", "x")\n'
            "\n"
            "\n"
            "def downgrade():\n"
            '    run_sql(DatabaseRole.ACTIVITY, "202601020000_more.down.sql")\n'
        ),
        "services/api/migrations/001.sql": "",
    }
    code, report = run(tmp_path, "STO-18", files)
    assert code == 1
    assert_messages(
        report,
        [
            "a migration file not named <YYYYMMDDHHMM>_<slug>.up.sql or .down.sql",
            "a migration outside the OM; migrations live with the OM",
            "activity.events is in role activity; this file is core's",
            "downgrade() runs role activity; the wrapper is in core/",
            "202601020000_more has no .down.sql",
            "upgrade() is not one run_sql() call; the SQL file holds the migration",
        ],
    )


def test_sto_18_a_gitkeep_keyword_arguments_and_a_path(tmp_path):
    files = {
        "om/migrations/sql/core/.gitkeep": "",
        "om/migrations/sql/core/README.md": "# core\n",
        WRAPPER: GOOD[WRAPPER]
        .replace(
            'run_sql(DatabaseRole.CORE, "202601010000_initial.up.sql")',
            'run_sql(role=DatabaseRole.CORE, path="core/202601010000_initial.up.sql")',
        )
        .replace(
            'run_sql(DatabaseRole.CORE, "202601010000_initial.down.sql")',
            'run_sql(DatabaseRole.CORE, name="202601010000_initial.down.sql")',
        ),
    }
    code, report = run(tmp_path, "STO-18", files)
    assert report["findings"] == []
    assert code == 0
    files[WRAPPER] = files[WRAPPER].replace('name="202601010000_initial.down.sql"', 'name="202601010000_other.down.sql"')
    code, report = run(tmp_path, "STO-18", files)
    assert code == 1
    assert messages(report) == ["downgrade() runs '202601010000_other.down.sql'; it runs 202601010000_initial.down.sql"]


def test_sto_20_the_outbox_storage_takes_one_row(tmp_path):
    files = {
        f"{OM}/outbox/storage/__init__.py": (
            "class OutboxStorageInterface(ABC):\n    async def mark_done(self, row: OutboxRow) -> None: ...\n"
        )
    }
    code, report = run(tmp_path, "STO-20", files)
    assert report["findings"] == []
    assert code == 0


def test_sto_20_a_single_outbox_row(tmp_path):
    code, report = run(tmp_path, "STO-20", edit(IFACE, "outbox_rows: tuple[OutboxRow, ...]", "outbox_row: OutboxRow | None"))
    assert code == 1
    assert "create_widget takes one OutboxRow" in messages(report)[0]


def test_sto_23_a_stamp_mismatch_and_two_heads(tmp_path):
    body = 'revision = "{rev}"\ndown_revision = "202601010000"\n'
    files = {
        "om/migrations/versions/core/202601020000_a.py": body.format(rev="202601020000"),
        "om/migrations/versions/core/202601030000_b.py": body.format(rev="202601030000"),
        "om/migrations/versions/core/202601040000_c.py": body.format(rev="202601049999"),
    }
    code, report = run(tmp_path, "STO-23", files)
    assert code == 1
    assert_messages(
        report,
        [
            "role core ends in 2 heads (202601020000, 202601030000); merge them",
            "revision is '202601049999'; it is the file's stamp '202601040000'",
        ],
    )


def test_sto_23_a_merge_revision_closes_the_fork(tmp_path):
    body = 'revision = "{rev}"\ndown_revision = {down}\n'
    files = {
        "om/migrations/versions/core/202601020000_a.py": body.format(rev="202601020000", down='"202601010000"'),
        "om/migrations/versions/core/202601030000_b.py": body.format(rev="202601030000", down='"202601010000"'),
        "om/migrations/versions/core/202601040000_merge.py": body.format(
            rev="202601040000", down='("202601020000", "202601030000")'
        ),
    }
    code, report = run(tmp_path, "STO-23", files)
    assert report["findings"] == []
    assert code == 0


def test_sto_26_a_full_unique_key_on_a_soft_deletable_table(tmp_path):
    files = edit(WIDGETS, ', postgresql_where=text("deleted_at IS NULL")', "")
    files[WIDGETS] += "    code: Mapped[str] = mapped_column(unique=True)\n"
    code, report = run(tmp_path, "STO-26", files)
    assert code == 1
    assert len(report["findings"]) == 2


def test_sto_26_a_full_unique_key_on_a_table_that_is_not_soft_deletable(tmp_path):
    files = {CATALOG: GOOD[CATALOG] + '    __table_args__ = (Index("uq_catalog_title", "title", unique=True),)\n'}
    code, _ = run(tmp_path, "STO-26", files)
    assert code == 0


def test_sto_27_an_engine_on_the_default_pool(tmp_path):
    code, report = run(tmp_path, "STO-27", edit(PG, "pool_size=pool.size, pool_timeout=pool.checkout_timeout", "pool_size=5"))
    assert code == 1
    assert_messages(
        report,
        [
            "pool_size is hard-coded; it comes from settings",
            "the engine is built without pool_timeout; the pool declares it from settings",
        ],
    )


def test_sto_28_a_tenant_table_left_without_force_and_a_missing_scope(tmp_path):
    later = "om/migrations/sql/core/202601020000_loosen.up.sql"
    files = {later: "ALTER TABLE core.widgets NO FORCE ROW LEVEL SECURITY;\n"}
    files.update(edit(SCOPES, '    "catalog": TableScope(ScopeKind.SYSTEM),\n', ""))
    code, report = run(tmp_path, "STO-28", files)
    assert code == 1
    assert_messages(
        report,
        [
            "catalog has no scope in TABLE_SCOPES",
            "core.widgets is a tenant table and the chain leaves it without FORCE ROW LEVEL SECURITY",
        ],
    )


def test_sto_28_a_policy_restored_by_a_later_migration_passes(tmp_path):
    files = {
        "om/migrations/sql/core/202601020000_drop.up.sql": "DROP POLICY tenant_fence ON core.widgets;\n",
        "om/migrations/sql/core/202601030000_back.up.sql": "CREATE POLICY tenant_fence ON core.widgets FOR ALL USING (true);\n",
    }
    code, _ = run(tmp_path, "STO-28", files)
    assert code == 0


def test_sto_28_a_policy_split_by_login_passes(tmp_path):
    files = edit(
        UP,
        "CREATE POLICY tenant_fence ON core.widgets FOR ALL USING (org_id = current_setting('app.org_id')::uuid);",
        "CREATE POLICY tenant_fence ON core.widgets FOR ALL TO acme_runtime"
        " USING (org_id = current_setting('app.org_id')::uuid);\n"
        "CREATE POLICY system_fence ON core.widgets FOR ALL TO acme_system"
        " USING (current_setting('app.org_id') = '00000000-0000-0000-0000-000000000000');",
    )
    code, _ = run(tmp_path, "STO-28", files)
    assert code == 0


def test_sto_28_rls_on_a_system_table(tmp_path):
    files = edit(
        UP,
        "ALTER TABLE core.widgets ENABLE",
        "ALTER TABLE core.catalog ENABLE ROW LEVEL SECURITY;\nALTER TABLE core.widgets ENABLE",
    )
    code, report = run(tmp_path, "STO-28", files)
    assert code == 1
    assert messages(report) == ["core.catalog is a system table and the chain enables row-level security on it"]


def test_sto_29_a_list_read_without_a_limit(tmp_path):
    files = edit(IFACE, "org_id: UUID, limit: int) -> list[Widget]", "org_id: UUID) -> tuple[Widget, ...]")
    files["infra/src/acme/infra/buckets/__init__.py"] = (
        "class BucketsInterface(ABC):\n    async def list(self, org_id, bucket, prefix) -> list[str]: ...\n"
    )
    code, report = run(tmp_path, "STO-29", files)
    assert code == 1
    assert_messages(
        report,
        [
            "BucketsInterface.list returns a list and takes no limit",
            "WidgetsStorageInterface.read_widgets returns a list and takes no limit",
        ],
    )


def test_sto_29_a_write_that_reports_ids_is_not_a_read(tmp_path):
    files = edit(
        IFACE,
        "class WidgetsStorageInterface(ABC):\n",
        "class WidgetsStorageInterface(ABC):\n"
        "    async def delete_widgets(self, org_id: UUID, ids: list[UUID]) -> list[UUID]: ...\n\n",
    )
    code, _ = run(tmp_path, "STO-29", files)
    assert code == 0


def test_sto_29_a_bucket_listing_under_another_name(tmp_path):
    files = {
        "infra/src/acme/infra/buckets/__init__.py": (
            "class BucketsInterface(ABC):\n"
            "    async def keys(self, org_id, bucket, prefix) -> list[str]: ...\n\n"
            "    async def list(self, org_id, bucket, prefix, limit: int) -> list[str]: ...\n\n"
            "    async def get(self, org_id, bucket, key) -> bytes: ...\n"
        )
    }
    code, report = run(tmp_path, "STO-29", files)
    assert code == 1
    assert messages(report) == ["BucketsInterface.keys returns a list and takes no limit"]


# --- the SQL reader and the replays


def test_sql_code_blanks_dollar_bodies_and_escape_strings():
    from arch_check.rules._storage_util import name_list, sql_code

    text = (
        "CREATE FUNCTION f() AS $body$ BEGIN RAISE 'don''t'; END $body$;\n"
        "SELECT E'it\\'s ; here', $1;\n"
        "CREATE TABLE core.after (id uuid);\n"
    )
    code = sql_code(text)
    assert len(code) == len(text)
    assert "BEGIN RAISE" in code and "don" not in code and "here" not in code
    assert "CREATE TABLE core.after" in code
    assert name_list(' a(int, text), "b c"(), d CASCADE') == ["a", '"b c"', "d"]


def test_sto_05_a_quote_in_a_dollar_body_does_not_hide_the_rest(tmp_path):
    later = "om/migrations/sql/core/202601020000_touch.up.sql"
    files = {
        later: (
            "COMMENT ON TABLE core.widgets IS $$it's a widget$$;\n"
            "COMMENT ON TABLE core.catalog IS E'the shop\\'s list';\n"
            "CREATE FUNCTION core.gate() RETURNS trigger AS $fn$ BEGIN RETURN NEW; END $fn$ LANGUAGE plpgsql;\n"
        )
    }
    code, report = run(tmp_path, "STO-05", files)
    assert code == 1
    assert rules_found(report) == [("STO-05", later, 3)]


def test_sto_05_triggers_are_scoped_to_their_table(tmp_path):
    first = "om/migrations/sql/core/202601020000_touch.up.sql"
    second = "om/migrations/sql/core/202601030000_untouch.up.sql"
    files = {
        first: (
            "CREATE TRIGGER touch BEFORE UPDATE ON core.widgets EXECUTE FUNCTION core.a();\n"
            "CREATE TRIGGER touch BEFORE UPDATE ON core.catalog EXECUTE FUNCTION core.a();\n"
        ),
        second: "DROP TRIGGER touch ON core.widgets;\n",
    }
    code, report = run(tmp_path, "STO-05", files)
    assert code == 1
    assert [(r, p, line) for r, p, line in rules_found(report)] == [("STO-05", first, 2)]


@pytest.mark.parametrize(
    "drop",
    [
        "DROP FUNCTION core.a(), core.b() CASCADE;\n",
        "DROP TABLE core.widgets, core.catalog;\nDROP FUNCTION core.a(), core.b();\n",
        "DROP ROUTINE IF EXISTS core.a, core.b CASCADE;\n",
    ],
)
def test_sto_05_a_cascade_a_table_drop_and_a_list_clear_the_chain(tmp_path, drop):
    first = "om/migrations/sql/core/202601020000_touch.up.sql"
    second = "om/migrations/sql/core/202601030000_untouch.up.sql"
    files = {
        first: (
            "CREATE FUNCTION core.a() RETURNS trigger AS $$ $$;\n"
            "CREATE FUNCTION core.b() RETURNS trigger AS $$ $$;\n"
            "CREATE TRIGGER ta BEFORE UPDATE ON core.widgets EXECUTE FUNCTION core.a();\n"
            "CREATE TRIGGER tb BEFORE UPDATE ON core.catalog EXECUTE PROCEDURE core.b();\n"
        ),
        second: drop,
    }
    code, report = run(tmp_path, "STO-05", files)
    assert report["findings"] == []
    assert code == 0
    files[second] = "DROP FUNCTION core.a(), core.b();\n"
    code, report = run(tmp_path, "STO-05", files)
    assert code == 1
    assert [line for _, _, line in rules_found(report)] == [3, 4]


@pytest.mark.parametrize(
    "column",
    [
        "mapped_column(primary_key=True, insert_default=uuid4, sort_order=-1000)",
        "mapped_column(primary_key=True, default_factory=uuid4, sort_order=-1000)",
    ],
)
def test_sto_06_an_orm_minted_id(tmp_path, column):
    test_sto_06_a_database_minted_id(tmp_path, column)


@pytest.mark.parametrize("ret", ['"UUID"', "tuple[UUID, ...]", "None | UUID", "list[uuid.UUID]"])
def test_sto_06_a_write_returning_uuids_in_another_spelling(tmp_path, ret):
    code, report = run(tmp_path, "STO-06", edit(IFACE, "tuple[OutboxRow, ...]) -> bool", f"tuple[OutboxRow, ...]) -> {ret}"))
    assert code == 1
    assert "create_widget returns a UUID" in messages(report)[0]


def test_sto_10_no_impl_root_at_all(tmp_path):
    code, report = run(tmp_path, "STO-10", drop=[MEMORY, PG])
    assert code == 1
    assert messages(report) == ["StorageInterface needs two roots, one of them StorageMemoryImpl"]


def test_sto_10_no_storage_root_at_all(tmp_path):
    code, report = run(tmp_path, "STO-10", drop=[ROOT, MEMORY, PG])
    assert code == 1
    assert messages(report) == ["acme.om has namespace storages and no StorageInterface under acme.om.storage"]


def test_sto_18_the_role_as_a_keyword_passes(tmp_path):
    files = edit(
        WRAPPER,
        'run_sql(DatabaseRole.CORE, "202601010000_initial.up.sql")',
        'run_sql("202601010000_initial.up.sql", role=DatabaseRole.CORE)',
    )
    code, _ = run(tmp_path, "STO-18", files)
    assert code == 0


def test_sto_17_a_foreign_key_constraint_across_roles(tmp_path):
    files = edit(
        WIDGETS,
        "    __table_args__ = (\n",
        '    __table_args__ = (\n        ForeignKeyConstraint(["parent_id"], ["activity.events.id"]),\n',
    )
    code, report = run(tmp_path, "STO-17", files)
    assert code == 1
    assert messages(report) == ["Widgets has a foreign key into role activity; it is in core"]
    files[WIDGETS] = files[WIDGETS].replace('["activity.events.id"]', '["core.catalog.id"]')
    assert run(tmp_path, "STO-17", files)[0] == 0


@pytest.mark.parametrize("rule,missing", [("STO-17", ROLES), ("STO-28", SCOPES)])
def test_sto_17_and_28_tables_with_no_map(tmp_path, rule, missing):
    code, report = run(tmp_path, rule, drop=[missing])
    assert code == 1
    assert rules_found(report) == [(rule, CATALOG, 4)]
    assert "the OM has tables and no TABLE_" in messages(report)[0]


def test_sto_28_rls_actions_in_one_statement(tmp_path):
    files = edit(
        UP,
        "ALTER TABLE core.widgets ENABLE ROW LEVEL SECURITY;\nALTER TABLE core.widgets FORCE ROW LEVEL SECURITY;\n",
        "ALTER TABLE core.widgets ENABLE ROW LEVEL SECURITY, FORCE ROW LEVEL SECURITY;\n",
    )
    code, report = run(tmp_path, "STO-28", files)
    assert report["findings"] == []
    assert code == 0
    files[UP] = files[UP].replace(", FORCE ROW", ", NO FORCE ROW")
    code, report = run(tmp_path, "STO-28", files)
    assert code == 1
    assert "without FORCE ROW LEVEL SECURITY" in messages(report)[0]


def test_sto_28_a_quoted_policy_name(tmp_path):
    files = edit(UP, "CREATE POLICY tenant_fence ON", 'CREATE POLICY "tenant fence" ON')
    assert run(tmp_path, "STO-28", files)[0] == 0
    files["om/migrations/sql/core/202601020000_drop.up.sql"] = 'DROP POLICY IF EXISTS "tenant fence" ON core.widgets;\n'
    code, report = run(tmp_path, "STO-28", files)
    assert code == 1
    assert "without a policy" in messages(report)[0]


@pytest.mark.parametrize(
    "ret", ["list[Widget] | None", "tuple[list[Widget], str | None]", "frozenset[Widget]", '"Sequence[Widget]"']
)
def test_sto_29_other_list_shapes(tmp_path, ret):
    files = edit(IFACE, "org_id: UUID, limit: int) -> list[Widget]", f"org_id: UUID) -> {ret}")
    code, report = run(tmp_path, "STO-29", files)
    assert code == 1
    assert messages(report) == ["WidgetsStorageInterface.read_widgets returns a list and takes no limit"]


def test_sto_03_a_lower_case_lock_in_a_query(tmp_path):
    files = {f"{OM}/widgets/impl/manager.py": 'Q = "select id from core.widgets for update skip locked"\nW = "wait for update"\n'}
    code, report = run(tmp_path, "STO-03", files)
    assert code == 1
    assert rules_found(report) == [("STO-03", f"{OM}/widgets/impl/manager.py", 1)]


def test_sto_05_an_event_trigger_and_an_aggregate_the_chain_leaves(tmp_path):
    first = "om/migrations/sql/core/202601020000_audit.up.sql"
    second = "om/migrations/sql/core/202601030000_unaudit.up.sql"
    files = {
        first: (
            "CREATE EVENT TRIGGER audit_ddl ON ddl_command_end EXECUTE FUNCTION core.audit();\n"
            "CREATE AGGREGATE core.total(numeric) (SFUNC = numeric_add, STYPE = numeric);\n"
        ),
    }
    code, report = run(tmp_path, "STO-05", files)
    assert code == 1
    assert messages(report) == [
        "CREATE EVENT TRIGGER audit_ddl is left in the schema; logic happens in the code",
        "CREATE AGGREGATE core.total is left in the schema; logic happens in the code",
    ]
    files[second] = "DROP EVENT TRIGGER IF EXISTS audit_ddl;\nDROP AGGREGATE core.total(numeric);\n"
    code, report = run(tmp_path, "STO-05", files)
    assert report["findings"] == []
    assert code == 0


def test_sto_18_a_quoted_schema_of_another_role(tmp_path):
    files = {
        "om/migrations/sql/core/202601020000_more.up.sql": 'ALTER TABLE "activity"."events" ADD COLUMN x int;\n',
        "om/migrations/sql/core/202601020000_more.down.sql": 'ALTER TABLE "core"."widgets" DROP COLUMN x;\n',
        "om/migrations/versions/core/202601020000_more.py": GOOD[WRAPPER].replace("202601010000_initial", "202601020000_more"),
    }
    code, report = run(tmp_path, "STO-18", files)
    assert code == 1
    assert messages(report) == ["activity.events is in role activity; this file is core's"]


def test_sto_12_an_entity_that_shares_a_tables_plural_name(tmp_path):
    entity = f"{OM}/widgets/types/widgets.py"
    files = {
        entity: "class Widgets(Entity):\n    pass\n",
        IFACE: GOOD[IFACE]
        .replace("from abc import", "from acme.om.widgets.types.widgets import Widgets\nfrom abc import")
        .replace("-> list[Widget]", "-> Widgets"),
    }
    code, report = run(tmp_path, "STO-12", files)
    assert report["findings"] == []
    assert code == 0
    files[IFACE] = files[IFACE].replace("acme.om.widgets.types.widgets", "acme.om.widgets.storage.tables.widgets")
    code, report = run(tmp_path, "STO-12", files)
    assert code == 1
    assert messages(report) == ["WidgetsStorageInterface.read_widgets returns Widgets; a row never leaves the impl"]


def test_sto_08_and_10_a_root_in_the_storage_package_init(tmp_path):
    init = f"{OM}/storage/__init__.py"
    files = {init: GOOD[ROOT]}
    code, report = run(tmp_path, "STO-10", files, drop=[ROOT])
    assert report["findings"] == []
    assert code == 0
    files[init] = GOOD[ROOT].replace("    @abstractmethod\n    async def close(self) -> None: ...\n", "")
    code, report = run(tmp_path, "STO-10", files, drop=[ROOT])
    assert code == 1
    assert rules_found(report) == [("STO-10", init, 8)]
    assert messages(report) == ["StorageInterface declares no close()"]
    files[init] = "from sqlalchemy.ext.asyncio import AsyncEngine\n" + GOOD[ROOT]
    code, report = run(tmp_path, "STO-08", files, drop=[ROOT])
    assert code == 1
    assert rules_found(report) == [("STO-08", init, 1)]


def test_sto_06_a_database_minted_key_the_migrations_leave(tmp_path):
    later = "om/migrations/sql/core/202601020000_keys.up.sql"
    files = {
        later: (
            "ALTER TABLE core.widgets ALTER COLUMN id SET DEFAULT gen_random_uuid();\n"
            "CREATE TABLE core.parts (id bigserial PRIMARY KEY, n int NOT NULL DEFAULT 0);\n"
            "CREATE TABLE core.feed (\n"
            "    org_id uuid NOT NULL,\n"
            "    seq bigint GENERATED ALWAYS AS IDENTITY,\n"
            "    CONSTRAINT pk_feed PRIMARY KEY (org_id, seq)\n"
            ");\n"
        )
    }
    code, report = run(tmp_path, "STO-06", files)
    assert code == 1
    assert rules_found(report) == [("STO-06", later, 1), ("STO-06", later, 2), ("STO-06", later, 5)]
    assert messages(report) == [
        "core.widgets.id has a DEFAULT in the migrations; an id is minted above storage",
        "core.parts.id has type bigserial in the migrations; an id is minted above storage",
        "core.feed.seq has an identity in the migrations; an id is minted above storage",
    ]


def test_sto_06_a_key_default_a_later_migration_drops_is_history(tmp_path):
    files = {
        "om/migrations/sql/core/202601020000_keys.up.sql": (
            "ALTER TABLE core.widgets ALTER COLUMN id SET DEFAULT gen_random_uuid();\n"
        ),
        "om/migrations/sql/core/202601030000_unkeys.up.sql": "ALTER TABLE core.widgets ALTER COLUMN id DROP DEFAULT;\n",
    }
    code, report = run(tmp_path, "STO-06", files)
    assert report["findings"] == []
    assert code == 0


def test_sto_26_a_full_unique_key_the_migrations_leave_on_a_soft_deletable_table(tmp_path):
    later = "om/migrations/sql/core/202601020000_uniques.up.sql"
    files = {
        later: (
            "CREATE UNIQUE INDEX uq_widgets_code ON core.widgets (org_id, code);\n"
            "CREATE UNIQUE INDEX uq_catalog_title ON core.catalog (title);\n"
            "CREATE TABLE core.parts (id uuid PRIMARY KEY, name text UNIQUE, deleted_at timestamptz NULL);\n"
            "CREATE UNIQUE INDEX uq_widgets_slug ON core.widgets (org_id, slug) WHERE deleted_at IS NULL;\n"
        )
    }
    code, report = run(tmp_path, "STO-26", files)
    assert code == 1
    assert rules_found(report) == [("STO-26", later, 1), ("STO-26", later, 3)]
    assert messages(report) == [
        "core.widgets is soft-deletable and uq_widgets_code holds the dead too; add WHERE deleted_at IS NULL",
        "core.parts is soft-deletable and parts_name_key holds the dead too; add WHERE deleted_at IS NULL",
    ]
    files["om/migrations/sql/core/202601030000_living.up.sql"] = (
        "DROP INDEX core.uq_widgets_code;\n"
        "CREATE UNIQUE INDEX uq_widgets_code ON core.widgets (org_id, code) WHERE deleted_at IS NULL;\n"
        "ALTER TABLE core.parts DROP CONSTRAINT parts_name_key;\n"
    )
    code, report = run(tmp_path, "STO-26", files)
    assert report["findings"] == []
    assert code == 0


def test_sto_26_a_key_the_table_class_reports_is_reported_once(tmp_path):
    files = edit(WIDGETS, ', postgresql_where=text("deleted_at IS NULL")', "")
    files["om/migrations/sql/core/202601020000_slug.up.sql"] = "CREATE UNIQUE INDEX uq_widgets_slug ON core.widgets (slug);\n"
    code, report = run(tmp_path, "STO-26", files)
    assert code == 1
    assert [p for _, p, _ in rules_found(report)] == [WIDGETS]


STR_AUTO = "from enum import StrEnum, auto\n\n\nclass DatabaseRole(StrEnum):\n    CORE = auto()\n    ACTIVITY = auto()\n"


def roles_with(header):
    text = GOOD[ROLES]
    return {ROLES: header + text[text.index("\n\nTABLE_ROLES") :]}


def test_sto_17_reads_a_role_written_with_auto(tmp_path):
    files = roles_with(STR_AUTO)
    files[ROLES] = files[ROLES].replace('"catalog": DatabaseRole.CORE,', '"catalog": DatabaseRole.ACTIVITY,')
    code, report = run(tmp_path, "STO-17", files)
    assert code == 1
    assert messages(report) == ["Widgets has a foreign key into role activity; it is in core"]


def test_sto_28_reads_a_role_written_with_auto(tmp_path):
    files = roles_with(STR_AUTO)
    files["om/migrations/sql/core/202601020000_loosen.up.sql"] = "ALTER TABLE core.widgets NO FORCE ROW LEVEL SECURITY;\n"
    code, report = run(tmp_path, "STO-28", files)
    assert code == 1
    assert messages(report) == ["core.widgets is a tenant table and the chain leaves it without FORCE ROW LEVEL SECURITY"]


def test_sto_17_a_role_it_cannot_read_is_reported(tmp_path):
    files = roles_with("from enum import Enum, auto\n\n\nclass DatabaseRole(Enum):\n    CORE = auto()\n    ACTIVITY = auto()\n")
    code, report = run(tmp_path, "STO-17", files)
    assert code == 1
    assert sorted(messages(report)) == [
        f"TABLE_ROLES gives {t} the role DatabaseRole.CORE, whose value arch-check cannot read; "
        "write the role as a string or a StrEnum member"
        for t in ("catalog", "widgets")
    ]


# --- a table swapped in by a rename, the search path, and quoted names

SWAP = (
    "CREATE TABLE core.widgets_v2 (id uuid PRIMARY KEY, org_id uuid NOT NULL, code text, deleted_at timestamptz);\n"
    "ALTER TABLE core.widgets_v2 ENABLE ROW LEVEL SECURITY, FORCE ROW LEVEL SECURITY;\n"
    "CREATE POLICY tenant_fence ON core.widgets_v2 FOR ALL USING (true);\n"
    "DROP TABLE core.widgets;\n"
    "ALTER TABLE core.widgets_v2 RENAME TO widgets;\n"
)


def test_sto_28_a_table_swapped_in_by_a_rename_passes(tmp_path):
    code, report = run(tmp_path, "STO-28", {"om/migrations/sql/core/202601020000_swap.up.sql": SWAP})
    assert report["findings"] == []
    assert code == 0


def test_sto_06_and_26_follow_a_table_through_its_rename(tmp_path):
    later = "om/migrations/sql/core/202601030000_after.up.sql"
    files = {
        "om/migrations/sql/core/202601020000_swap.up.sql": SWAP,
        later: (
            "ALTER TABLE core.widgets ALTER COLUMN id SET DEFAULT gen_random_uuid();\n"
            "CREATE UNIQUE INDEX uq_widgets_code ON core.widgets (org_id, code);\n"
        ),
    }
    code, report = run(tmp_path, "STO-06", files)
    assert (code, rules_found(report)) == (1, [("STO-06", later, 1)])
    assert messages(report) == ["core.widgets.id has a DEFAULT in the migrations; an id is minted above storage"]
    code, report = run(tmp_path, "STO-26", files)
    assert (code, rules_found(report)) == (1, [("STO-26", later, 2)])


MORE_UP = "om/migrations/sql/core/202601020000_more.up.sql"
MORE_DOWN = "om/migrations/sql/core/202601020000_more.down.sql"


def more(up, down="DROP TABLE core.parts;\n"):
    return {
        MORE_UP: up,
        MORE_DOWN: down,
        "om/migrations/versions/core/202601020000_more.py": GOOD[WRAPPER].replace("202601010000_initial", "202601020000_more"),
    }


def test_sto_18_a_table_named_without_its_schema(tmp_path):
    files = more(
        "SET search_path TO activity;\n"
        "CREATE TABLE events (id uuid PRIMARY KEY);\n"
        "CREATE TEMP TABLE scratch (id uuid);\n"
        "CREATE INDEX ix_events_id ON events (id);\n"
        "ALTER TABLE core.widgets ADD COLUMN part_id uuid REFERENCES parts (id);\n"
    )
    code, report = run(tmp_path, "STO-18", files)
    assert code == 1
    assert rules_found(report) == [("STO-18", MORE_UP, 1), ("STO-18", MORE_UP, 2), ("STO-18", MORE_UP, 4), ("STO-18", MORE_UP, 5)]
    assert messages(report) == [
        "the migration sets search_path; every table it names is schema-qualified",
        "events is not schema-qualified; name it core.events",
        "events is not schema-qualified; name it core.events",
        "parts is not schema-qualified; name it core.parts",
    ]
    code, report = run(tmp_path, "STO-18", more("CREATE TABLE core.parts (id uuid PRIMARY KEY);\n"))
    assert report["findings"] == []
    assert code == 0


def test_sql_code_keeps_quoted_identifiers_and_blanks_doubled_quotes():
    from arch_check.rules._storage_util import sql_code

    text = "CREATE TABLE core.t (\"it's\" text, \"a--b\" text, note text DEFAULT 'it''s', id uuid DEFAULT x());\n"
    code = sql_code(text)
    assert len(code) == len(text)
    assert '"it\'s" text, "a--b" text' in code
    assert "it''s" not in code
    assert "id uuid DEFAULT x()" in code


def test_sto_06_a_default_after_a_quoted_identifier(tmp_path):
    later = "om/migrations/sql/core/202601020000_parts.up.sql"
    files = {later: "CREATE TABLE core.parts (\"it's\" text, note text DEFAULT 'it''s', id uuid PRIMARY KEY DEFAULT x());\n"}
    code, report = run(tmp_path, "STO-06", files)
    assert code == 1
    assert messages(report) == ["core.parts.id has a DEFAULT in the migrations; an id is minted above storage"]


def test_sto_26_names_an_unnamed_index_the_way_postgres_does(tmp_path):
    later = "om/migrations/sql/core/202601020000_codes.up.sql"
    files = {
        later: "CREATE UNIQUE INDEX ON core.widgets (org_id, code);\nCREATE UNIQUE INDEX ON core.widgets (lower(slug));\n",
    }
    code, report = run(tmp_path, "STO-26", files)
    assert code == 1
    assert messages(report) == [
        "core.widgets is soft-deletable and widgets_org_id_code_idx holds the dead too; add WHERE deleted_at IS NULL",
        "core.widgets is soft-deletable and widgets_lower_idx holds the dead too; add WHERE deleted_at IS NULL",
    ]
    files["om/migrations/sql/core/202601030000_drop.up.sql"] = (
        "DROP INDEX core.widgets_org_id_code_idx, core.widgets_lower_idx;\n"
    )
    code, report = run(tmp_path, "STO-26", files)
    assert report["findings"] == []
    assert code == 0


@pytest.mark.parametrize(
    ("where", "living"),
    [
        ("deleted_at IS NULL", True),
        ("(deleted_at IS NULL) AND code IS NOT NULL", True),
        ("code IS NOT NULL AND (deleted_at IS NULL)", True),
        ("deleted_at IS NULL OR code IS NULL", False),
        ("(deleted_at IS NULL OR archived)", False),
        ("NOT deleted_at IS NULL", False),
    ],
)
def test_sto_26_a_where_holds_the_living_only_when_it_implies_deleted_at_is_null(tmp_path, where, living):
    later = "om/migrations/sql/core/202601020000_codes.up.sql"
    files = {later: f"CREATE UNIQUE INDEX uq_widgets_code ON core.widgets (org_id, code) WHERE {where};\n"}
    code, report = run(tmp_path, "STO-26", files)
    assert (code, len(report["findings"])) == ((0, 0) if living else (1, 1))


@pytest.mark.parametrize(
    "where", ['text("deleted_at IS NULL OR slug IS NULL")', "or_(Widget.deleted_at.is_(None), Widget.slug.is_(None))"]
)
def test_sto_26_a_table_class_where_that_lets_the_dead_in(tmp_path, where):
    files = edit(WIDGETS, 'postgresql_where=text("deleted_at IS NULL")', f"postgresql_where={where}")
    code, report = run(tmp_path, "STO-26", files)
    assert (code, [p for _, p, _ in rules_found(report)]) == (1, [WIDGETS])


IDENTITY_SCOPED = """\

class IdentityScopedMixin:
    id: Mapped[UUID] = mapped_column(primary_key=True, sort_order=-1000)
    identity_id: Mapped[UUID] = mapped_column(index=True, sort_order=-999)
"""


def test_sto_11_the_identity_scoped_mixin_leads_and_carries_no_org_id(tmp_path):
    files = {BASE: GOOD[BASE] + IDENTITY_SCOPED}
    files[CATALOG] = (
        GOOD[CATALOG]
        .replace("GlobalIdentifiableMixin", "IdentityScopedMixin")
        .replace("IdentityScopedMixin, CreatedMixin, Base", "CreatedMixin, IdentityScopedMixin, Base")
        .replace("    title: Mapped[str]\n", "    title: Mapped[str]\n    org_id: Mapped[UUID]\n")
    )
    code, report = run(tmp_path, "STO-11", files)
    assert code == 1
    assert_messages(
        report,
        [
            "Catalog composes IdentityScopedMixin and declares org_id",
            "Catalog lists CreatedMixin before IdentityScopedMixin; the house order is identity, name, lifecycle, soft delete",
        ],
    )
    code, report = run(tmp_path, "STO-13", {BASE: GOOD[BASE] + IDENTITY_SCOPED.replace("-999", "-600")})
    assert code == 1
    assert messages(report) == [
        "NamedMixin.name sorts at -900, before IdentityScopedMixin.identity_id at -600; bands follow the house order"
    ]
