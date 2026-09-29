from pathlib import Path

from worker_support import RecordingHandler, build_container, make_item, sign_in

from acme.workers.maintenance.handler import NoopHandlerImpl


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
