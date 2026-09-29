"""Every table has one role, the role is its schema, and nothing crosses one."""

import importlib
import pkgutil

import pytest
from sqlalchemy import Column, MetaData, Table, Uuid, select, update

import acme.om
from acme.om.exceptions import CrossRoleStatement
from acme.om.storage.impl.pg_base import role_of
from acme.om.storage.roles import TABLE_ROLES, DatabaseRole, role_for
from acme.om.storage.tables.base import Base
from acme.om.tenancy.storage.tables.orgs import Orgs


def import_every_table_module() -> None:
    for module in pkgutil.walk_packages(acme.om.__path__, prefix="acme.om."):
        if ".storage.tables." in module.name:
            importlib.import_module(module.name)


def test_every_table_has_a_role_and_lives_in_its_schema() -> None:
    import_every_table_module()
    assert Base.metadata.tables, "no tables were imported"
    for table in Base.metadata.tables.values():
        role = role_for(table.name)
        assert table.schema == role.value, f"{table.name} is in {table.schema}, role says {role}"


def test_no_foreign_key_crosses_a_role() -> None:
    import_every_table_module()
    for table in Base.metadata.tables.values():
        for fk in table.foreign_keys:
            assert role_for(fk.column.table.name) is role_for(table.name)


def test_every_mapped_table_is_in_the_role_map() -> None:
    import_every_table_module()
    for name in Base.metadata.tables:
        assert name.split(".")[-1] in TABLE_ROLES


def test_unmapped_table_is_refused() -> None:
    with pytest.raises(LookupError):
        role_for("not_a_table")


def test_a_statement_spanning_roles_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(TABLE_ROLES, "probe_activity", DatabaseRole.ACTIVITY)
    probe = Table("probe_activity", MetaData(), Column("id", Uuid), schema="activity")
    assert role_of(select(Orgs)) is DatabaseRole.CORE
    assert role_of(Orgs) is DatabaseRole.CORE
    with pytest.raises(CrossRoleStatement):
        role_of(select(Orgs.id, probe.c.id))


async def test_an_outbox_row_outside_the_core_role_is_refused() -> None:
    # The outbox lives in core; a queue-role write may not carry an outbox row,
    # or the session would hold two roles in one transaction.
    from acme.om.base import new_id, utcnow
    from acme.om.outbox.types.row import OutboxRow
    from acme.om.storage.impl.pg_base import LoginSessions, PgStorageBase
    from acme.om.work.storage.tables.work_items import WorkItems

    row = OutboxRow(
        id=new_id(),
        created_at=utcnow(),
        org_id=new_id(),
        kind="work.item.created",
        target_id=new_id(),
        actor_id=new_id(),
        request_id=new_id(),
        app="worker",
    )

    # No sessions: the role check fires before one is opened.
    base = PgStorageBase(LoginSessions({}, {}))
    with pytest.raises(CrossRoleStatement):
        await base._upsert(WorkItems, new_id(), row, (row,))


def test_the_re_mint_reads_the_marker_inside_one_role() -> None:
    """The re-mint of a secret is conditional on the marker still holding the
    attempt making the write, so its one statement names the api keys and the
    markers. That is a swimlane crossing, as landing an outbox row is, and it
    is only writable as one statement because both tables are in `core`."""
    from acme.om.idempotency.storage.tables.idempotency_records import IdempotencyRecords
    from acme.om.tenancy.storage.tables.api_keys import ApiKeys

    assert role_for("api_keys") is role_for("idempotency_records") is DatabaseRole.CORE
    fence = select(IdempotencyRecords.id).where(IdempotencyRecords.status.is_(None)).exists()
    assert role_of(update(ApiKeys).where(fence).values(key_hash="x")) is DatabaseRole.CORE
