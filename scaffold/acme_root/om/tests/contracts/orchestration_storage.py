"""The orchestrations storage contract. The cases named in
`CROSS_TENANT_CASES` are the tenant fence's evidence: each one presents
another tenant's identifier and asserts that nothing is found and nothing
changes."""

from datetime import timedelta
from uuid import UUID

import pytest

from acme.om.base import new_id, utcnow
from acme.om.exceptions import PreconditionFailed
from acme.om.orchestrations.storage import OrchestrationsStorageInterface
from acme.om.orchestrations.types.orchestration import (
    Orchestration,
    OrchestrationKind,
    OrchestrationStatus,
    ParkReason,
)
from acme.om.outbox.types.row import OutboxRow

CROSS_TENANT_CASES: frozenset[str] = frozenset(
    {
        "create_orchestration",
        "purge_tenant",
        "read_orchestration",
        "read_parked",
        "read_recent",
        "write_orchestration",
    }
)
"""Every method of `OrchestrationsStorageInterface` that takes a tenant has a
case in this module that presents another tenant's."""


def make_record(
    kind: OrchestrationKind = OrchestrationKind.TASK_IMPORT,
    *,
    period: str | None = None,
    status: OrchestrationStatus = OrchestrationStatus.RUNNING,
    park_reason: ParkReason | None = None,
    updated_ago: timedelta = timedelta(0),
) -> Orchestration:
    now = utcnow()
    actor = new_id()
    return Orchestration(
        id=new_id(),
        created_at=now - updated_ago,
        updated_at=now - updated_ago,
        created_by=actor,
        updated_by=actor,
        kind=kind,
        input={"file_id": str(new_id())},
        period=period,
        status=status,
        park_reason=park_reason,
    )


def make_row(org_id: UUID, record: Orchestration) -> OutboxRow:
    return OutboxRow(
        id=new_id(),
        created_at=utcnow(),
        org_id=org_id,
        kind="orchestrations.orchestration.updated",
        target_id=record.id,
        actor_id=record.created_by,
        request_id=new_id(),
        app="worker",
    )


async def seed(
    storage: OrchestrationsStorageInterface, org_id: UUID, record: Orchestration
) -> None:
    assert await storage.create_orchestration(org_id, record, (make_row(org_id, record),))


async def drained(storage: OrchestrationsStorageInterface) -> timedelta:
    """How far back a purge case stands: a century, so no other case's record
    is past its cut, with whatever an earlier run of these cases left behind
    it purged first. The purge reaches across tenants, so a case owns the
    records behind its cut."""
    back = timedelta(days=36500)
    while await storage.purge_settled(utcnow() - back - timedelta(days=30), 1000):
        pass
    return back


class OrchestrationStorageContract:
    @pytest.fixture
    def storage(self) -> OrchestrationsStorageInterface:
        raise NotImplementedError("the concrete test class provides the storage")

    async def test_round_trip(self, storage: OrchestrationsStorageInterface) -> None:
        org = new_id()
        record = make_record()
        await seed(storage, org, record)
        assert await storage.read_orchestration(org, record.id) == record

    async def test_create_reports_an_existing_id_and_changes_nothing(
        self, storage: OrchestrationsStorageInterface
    ) -> None:
        org = new_id()
        record = make_record()
        await seed(storage, org, record)
        again = record.model_copy(update={"cursor": 7})
        assert await storage.create_orchestration(org, again, ()) is False
        assert await storage.read_orchestration(org, record.id) == record

    async def test_a_period_opens_once_per_org_and_kind(
        self, storage: OrchestrationsStorageInterface
    ) -> None:
        org, other = new_id(), new_id()
        first = make_record(OrchestrationKind.TASK_CLEANUP, period="2026-09-25")
        await seed(storage, org, first)
        second = make_record(OrchestrationKind.TASK_CLEANUP, period="2026-09-25")
        assert await storage.create_orchestration(org, second, ()) is False
        assert await storage.read_orchestration(org, second.id) is None
        # Another day, and another org's same day, are records of their own.
        await seed(storage, org, make_record(OrchestrationKind.TASK_CLEANUP, period="2026-09-26"))
        await seed(storage, other, make_record(OrchestrationKind.TASK_CLEANUP, period="2026-09-25"))

    async def test_create_orchestration_under_another_tenant_is_not_read_here(
        self, storage: OrchestrationsStorageInterface
    ) -> None:
        org, other = new_id(), new_id()
        record = make_record()
        await seed(storage, org, record)
        assert await storage.read_orchestration(other, record.id) is None
        assert await storage.create_orchestration(other, record, ()) is False
        assert await storage.read_orchestration(other, record.id) is None

    async def test_read_recent_is_newest_first_of_the_kind_and_the_tenant(
        self, storage: OrchestrationsStorageInterface
    ) -> None:
        org, other = new_id(), new_id()
        older, newer = make_record(), make_record()
        await seed(storage, org, older)
        await seed(storage, org, newer)
        await seed(storage, org, make_record(OrchestrationKind.TASK_CLEANUP, period="2026-09-25"))
        await seed(storage, other, make_record())
        found = await storage.read_recent(org, OrchestrationKind.TASK_IMPORT, 10)
        assert [r.id for r in found] == [newer.id, older.id]
        assert [r.id for r in await storage.read_recent(org, OrchestrationKind.TASK_IMPORT, 1)] == [
            newer.id
        ]
        assert await storage.read_recent(new_id(), OrchestrationKind.TASK_IMPORT, 10) == []

    async def test_read_parked_names_the_reason_and_the_tenant(
        self, storage: OrchestrationsStorageInterface
    ) -> None:
        org, other = new_id(), new_id()
        waiting = make_record(status=OrchestrationStatus.PARKED, park_reason=ParkReason.PLAN_LIMIT)
        await seed(storage, org, waiting)
        await seed(storage, org, make_record())
        await seed(
            storage,
            other,
            make_record(status=OrchestrationStatus.PARKED, park_reason=ParkReason.PLAN_LIMIT),
        )
        found = await storage.read_parked(org, ParkReason.PLAN_LIMIT, 10)
        assert [r.id for r in found] == [waiting.id]
        assert [r.id for r in await storage.read_parked(other, ParkReason.PLAN_LIMIT, 10)] != [
            waiting.id
        ]

    async def test_write_is_a_compare_and_set_on_the_version(
        self, storage: OrchestrationsStorageInterface
    ) -> None:
        org = new_id()
        record = make_record()
        await seed(storage, org, record)
        moved = record.model_copy(update={"cursor": 100, "version": 2})
        await storage.write_orchestration(org, moved, 1, (make_row(org, moved),))
        assert await storage.read_orchestration(org, record.id) == moved
        stale = record.model_copy(update={"cursor": 50, "version": 2})
        with pytest.raises(PreconditionFailed):
            await storage.write_orchestration(org, stale, 1, ())
        assert await storage.read_orchestration(org, record.id) == moved

    async def test_write_orchestration_under_another_tenant_lands_nothing(
        self, storage: OrchestrationsStorageInterface
    ) -> None:
        org, other = new_id(), new_id()
        record = make_record()
        await seed(storage, org, record)
        forged = record.model_copy(update={"cursor": 9, "version": 2})
        with pytest.raises(PreconditionFailed):
            await storage.write_orchestration(other, forged, 1, ())
        assert await storage.read_orchestration(org, record.id) == record

    async def test_purge_settled_takes_only_settled_records_past_the_cut(
        self, storage: OrchestrationsStorageInterface
    ) -> None:
        """The purge runs across tenants: every tenant's settled records past
        the cut go, a batch at a time, and no parked or younger one."""
        org, other = new_id(), new_id()
        back = await drained(storage)
        old = back + timedelta(days=40)
        old_done = make_record(status=OrchestrationStatus.SUCCEEDED, updated_ago=old)
        old_failed = make_record(status=OrchestrationStatus.FAILED, updated_ago=old)
        old_parked = make_record(
            status=OrchestrationStatus.PARKED, park_reason=ParkReason.PLAN_LIMIT, updated_ago=old
        )
        fresh = make_record(status=OrchestrationStatus.SUCCEEDED)
        theirs = make_record(status=OrchestrationStatus.SUCCEEDED, updated_ago=old)
        for record in (old_done, old_failed, old_parked, fresh):
            await seed(storage, org, record)
        await seed(storage, other, theirs)
        cut = utcnow() - back - timedelta(days=30)
        assert await storage.purge_settled(cut, 2) == 2, "a batch at most"
        assert await storage.purge_settled(cut, 2) == 1
        assert await storage.purge_settled(cut, 2) == 0
        assert await storage.read_orchestration(org, old_done.id) is None
        assert await storage.read_orchestration(other, theirs.id) is None, "every tenant's"
        assert await storage.read_orchestration(org, old_parked.id) is not None
        assert await storage.read_orchestration(org, fresh.id) is not None

    async def test_purge_tenant_takes_every_record_of_the_tenant(
        self, storage: OrchestrationsStorageInterface
    ) -> None:
        org, other = new_id(), new_id()
        await seed(storage, org, make_record())
        await seed(storage, org, make_record(status=OrchestrationStatus.SUCCEEDED))
        theirs = make_record()
        await seed(storage, other, theirs)
        assert await storage.purge_tenant(org, 1) == 1, "a batch at most"
        assert await storage.purge_tenant(org, 1) == 1
        assert await storage.read_orchestration(other, theirs.id) == theirs
