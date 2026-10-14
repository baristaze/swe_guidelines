"""A copy adds its kinds from its own module. This module stands for a
copy's namespace: it declares an orchestration kind, a leasable resource
kind, and a waiter kind as such a namespace does, and hands them to the
registries as the root does below its markers. Each resolves with no edit to
a scaffold file. A kind nobody registered is refused, and a malformed name
never reaches a row."""

from collections.abc import Mapping
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import Field, ValidationError

from acme.infra.impl.local import InfraLocalImpl
from acme.om.base import OmBase, new_id, utcnow
from acme.om.context import AppContext, AppType, RequestContext, TenantContext
from acme.om.exceptions import ValidationFailed
from acme.om.leases.hooks import AskCheckInterface, LeasableResourceKindInterface, WaiterInterface
from acme.om.leases.impl.kinds import NoopLeasableResourceKindImpl, OrchestrationWaiterImpl
from acme.om.leases.impl.manager import LeasesManagerImpl, LeasesOptions
from acme.om.leases.types.leasable_resource import LeasableResource, LeasableResourceKind
from acme.om.leases.types.lease import Lease
from acme.om.leases.types.request import LeaseRequest, WaiterKind
from acme.om.orchestrations.kinds import NOOP_ORCHESTRATION, OrchestrationKindSpec
from acme.om.orchestrations.types.orchestration import Orchestration, OrchestrationKind
from acme.om.outbox.types.row import OutboxRow
from acme.om.root import ORCHESTRATION_KINDS, Managers, build_managers
from acme.om.storage.impl.memory import StorageMemoryImpl

APP = AppContext(type=AppType.PORTAL, version="portal@test")

# The copy's own module: its names, its shapes, and its hooks.

DOCK_IMPORT = "dock_import"
DOCK = "dock"
CREW = "crew"


class DockImportInput(OmBase):
    rows: int = Field(ge=1, le=10)


DOCK_IMPORT_KIND = OrchestrationKindSpec(DOCK_IMPORT, DockImportInput)


class DockAsk(OmBase):
    bay: int = Field(ge=1, le=40)


class DockKindImpl(LeasableResourceKindInterface):
    ASK = DockAsk

    async def may_grant(
        self, ctx: TenantContext, resource: LeasableResource, request: LeaseRequest
    ) -> bool:
        return True

    def grant_rows(
        self, ctx: TenantContext, resource: LeasableResource, request: LeaseRequest, lease: Lease
    ) -> tuple[OutboxRow, ...]:
        return ()


class Shapeless(DockKindImpl):
    """A kind's hooks that forgot the shape of its ask."""

    ASK = None  # type: ignore[assignment]


class CrewWaiterImpl(WaiterInterface):
    def __init__(self) -> None:
        self.woken: list[UUID] = []

    async def still_waits(self, ctx: TenantContext, waiter_id: UUID) -> bool:
        return True

    def wake_rows(self, ctx: TenantContext, waiter_id: UUID, lease: Lease) -> tuple[OutboxRow, ...]:
        self.woken.append(waiter_id)
        return ()

    def end_rows(
        self, ctx: TenantContext, waiter_id: UUID, request: LeaseRequest
    ) -> tuple[OutboxRow, ...]:
        return ()

    def revoke_rows(
        self, ctx: TenantContext, waiter_id: UUID, lease: Lease
    ) -> tuple[OutboxRow, ...]:
        return ()


class AnyAsk(AskCheckInterface):
    async def check_ask(
        self, ctx: TenantContext, request: LeaseRequest, resource: LeasableResource | None
    ) -> None:
        return None


# The registries, as a copy's root builds them below its markers.


class World:
    def __init__(self, tmp_path: Path, *, copys: bool = True) -> None:
        self.storage = StorageMemoryImpl()
        self.managers: Managers = build_managers(
            self.storage,
            InfraLocalImpl(tmp_path),
            orchestration_kinds=(
                ORCHESTRATION_KINDS.extended((DOCK_IMPORT_KIND,)) if copys else ORCHESTRATION_KINDS
            ),
        )
        self.crew = CrewWaiterImpl()
        self.leases = (
            LeasesManagerImpl(
                self.storage.get_lease_storage(),
                self.managers.tenancy,
                self.managers.outbox,
                LeasesOptions(),
                kinds={
                    LeasableResourceKind.NOOP: NoopLeasableResourceKindImpl(),
                    DOCK: DockKindImpl(),
                },
                waiters={
                    WaiterKind.ORCHESTRATION: OrchestrationWaiterImpl(self.managers.orchestrations),
                    CREW: self.crew,
                },
                work=self.managers.work,
            )
            if copys
            else self.managers.leases
        )

    async def owner(self) -> TenantContext:
        slug = f"ajax-{new_id().hex[-8:]}"
        owner, _ = await self.managers.tenancy.bootstrap(
            RequestContext(request_id=new_id(), app=APP), "Ajax", slug, f"a-{slug}@x.test", "Ann"
        )
        return owner


def a_record(kind: str, **input: object) -> Orchestration:
    now, actor = utcnow(), new_id()
    return Orchestration(
        id=new_id(),
        created_at=now,
        updated_at=now,
        created_by=actor,
        updated_by=actor,
        kind=kind,
        input=input,
    )


def a_dock(kind: str = DOCK) -> LeasableResource:
    now, actor = utcnow(), new_id()
    return LeasableResource(
        id=new_id(),
        created_at=now,
        updated_at=now,
        created_by=actor,
        updated_by=actor,
        kind=kind,
        ref_id=new_id(),
    )


def an_ask(
    dock: LeasableResource,
    *,
    kind: str = DOCK,
    waiter_kind: str | None = None,
    payload: Mapping[str, object] | None = None,
) -> LeaseRequest:
    now, actor = utcnow(), new_id()
    return LeaseRequest(
        id=new_id(),
        created_at=now,
        updated_at=now,
        created_by=actor,
        updated_by=actor,
        idempotency_key=new_id(),
        kind=kind,
        leasable_resource_id=dock.id,
        payload=dict(payload or {}),
        waiter_kind=waiter_kind,
        waiter_id=None if waiter_kind is None else new_id(),
    )


def test_the_copys_names_are_in_no_scaffold_list() -> None:
    assert DOCK_IMPORT not in ORCHESTRATION_KINDS and DOCK_IMPORT not in set(OrchestrationKind)
    assert DOCK not in set(LeasableResourceKind) and CREW not in set(WaiterKind)


async def test_a_copys_orchestration_kind_starts_and_holds_its_input(tmp_path: Path) -> None:
    world = World(tmp_path)
    owner = await world.owner()
    started = await world.managers.orchestrations.start(owner, a_record(DOCK_IMPORT, rows=3))
    assert started.kind == DOCK_IMPORT and dict(started.input) == {"rows": 3}
    assert await world.managers.orchestrations.get(owner, started.id) == started
    for wrong in ({"rows": 11}, {"rows": 3, "bay": 1}, {}):
        with pytest.raises(ValidationFailed, match="a dock_import input is not"):
            await world.managers.orchestrations.start(owner, a_record(DOCK_IMPORT, **wrong))
    page = await world.managers.orchestrations.get_recent(owner, DOCK_IMPORT, 10)
    assert [r.id for r in page.items] == [started.id]


async def test_a_copys_resource_kind_takes_its_ask_and_its_waiter_is_woken(
    tmp_path: Path,
) -> None:
    world = World(tmp_path)
    owner = await world.owner()
    dock = await world.leases.register(owner, a_dock())
    assert dock.kind == DOCK
    asked = an_ask(dock, waiter_kind=CREW, payload={"bay": 3})
    standing = await world.leases.ask(owner, asked)
    assert standing.lease is not None and standing.request.kind == DOCK
    assert world.crew.woken == [asked.waiter_id]
    for wrong in ({"bay": 0}, {"shelf": 1}):
        with pytest.raises(ValidationFailed, match="a dock ask is not"):
            await world.leases.ask(owner, an_ask(dock, payload=wrong))


async def test_a_kind_nobody_registered_is_refused(tmp_path: Path) -> None:
    world = World(tmp_path, copys=False)
    owner = await world.owner()
    with pytest.raises(ValidationFailed, match="no orchestration kind dock_import is registered"):
        await world.managers.orchestrations.start(owner, a_record(DOCK_IMPORT, rows=3))
    with pytest.raises(ValidationFailed, match="no resource kind dock is registered"):
        await world.leases.register(owner, a_dock())
    noop = await world.leases.register(owner, a_dock(LeasableResourceKind.NOOP))
    with pytest.raises(ValidationFailed, match="no resource kind dock is registered"):
        await world.leases.ask(owner, an_ask(noop, payload={"bay": 3}))
    with pytest.raises(ValidationFailed, match="no waiter of kind crew is registered"):
        await world.leases.ask(
            owner, an_ask(noop, kind=LeasableResourceKind.NOOP, waiter_kind=CREW)
        )


def test_a_malformed_name_never_reaches_a_row() -> None:
    for name in ("Dock", "dock import", "1dock", "", "d" * 65, "dock\n"):
        with pytest.raises(ValidationError):
            a_record(name)
        with pytest.raises(ValidationError):
            a_dock(name)
        with pytest.raises(ValidationError):
            an_ask(a_dock(), kind=name)
        with pytest.raises(ValidationError):
            an_ask(a_dock(), waiter_kind=name)
        with pytest.raises(ValueError, match="named in snake case"):
            OrchestrationKindSpec(name, DockImportInput)


def test_a_registry_refuses_a_name_twice_so_no_scaffold_kind_is_taken_over() -> None:
    mine = OrchestrationKindSpec(OrchestrationKind.NOOP, DockImportInput)
    with pytest.raises(ValueError, match="orchestration kind noop is registered twice"):
        ORCHESTRATION_KINDS.extended((mine,))
    extended = ORCHESTRATION_KINDS.extended((DOCK_IMPORT_KIND,))
    assert extended.get(DOCK_IMPORT) is DOCK_IMPORT_KIND
    assert extended.get(OrchestrationKind.NOOP) is NOOP_ORCHESTRATION


async def test_the_lease_build_refuses_a_kind_it_could_not_serve(tmp_path: Path) -> None:
    world = World(tmp_path)

    def build(**hooks: object) -> LeasesManagerImpl:
        return LeasesManagerImpl(
            world.storage.get_lease_storage(),
            world.managers.tenancy,
            world.managers.outbox,
            LeasesOptions(),
            work=world.managers.work,
            **{"kinds": {DOCK: DockKindImpl()}, "waiters": {}, **hooks},  # type: ignore[arg-type]
        )

    with pytest.raises(ValueError, match="named in snake case, never Dock"):
        build(kinds={"Dock": DockKindImpl()})
    with pytest.raises(ValueError, match="named in snake case, never Crew"):
        build(waiters={"Crew": CrewWaiterImpl()})
    with pytest.raises(ValueError, match="resource kind dock declares no ASK"):
        build(kinds={DOCK: Shapeless()})
    with pytest.raises(ValueError, match="an ask check names no registered resource kind: crew"):
        build(asks={CREW: AnyAsk()})
    assert isinstance(build(asks={DOCK: AnyAsk()}), LeasesManagerImpl)
