from pathlib import Path

from worker_support import RecordingHandler, build_container, make_item, sign_in

from acme.om.root import KINDS
from acme.om.tenancy.rules import ROLE_PERMISSIONS
from acme.om.work.types.work_item import WorkKind
from acme.workers.maintenance.handler import NoopHandlerImpl
from acme.workers.maintenance.main import build_loop


async def test_handler_is_idempotent_over_the_memory_container(tmp_path: Path) -> None:
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    item = make_item(ctx)
    handler = RecordingHandler()
    await handler.handle(ctx, item)
    await handler.handle(ctx, item)
    assert [handled.id for handled in handler.handled] == [item.id, item.id]


async def test_handler_keeps_nothing_per_item(tmp_path: Path) -> None:
    # The production handler runs for the life of the worker; a record of what it
    # handled would grow with every item.
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    handler = NoopHandlerImpl()
    for _ in range(3):
        await handler.handle(ctx, make_item(ctx))
    assert vars(handler) == {}, "the production handler holds no per-item state"


def test_every_kind_is_asked_for_by_a_permission_as_wide_as_its_handler(tmp_path: Path) -> None:
    """Every kind the root registers has a handler, the scaffold's own among
    them, and whoever may ask for a kind may make every call its handler
    makes: the authorization at enqueue covers the whole run."""
    loop = build_loop(build_container(tmp_path))
    handlers = loop._handlers  # pyright: ignore[reportPrivateUsage] (the worker's own table)
    assert set(handlers) == {kind.name for kind in KINDS}
    assert set(WorkKind) <= set(handlers)
    for kind in KINDS:
        requires = type(handlers[kind.name]).REQUIRES
        for role, permissions in ROLE_PERMISSIONS.items():
            if kind.permission in permissions:
                missing = [p for p in requires if p not in permissions]
                assert not missing, f"{role.value} asks for {kind.name} without {missing}"
