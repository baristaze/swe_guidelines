"""Long-running records in the worker, over the `noop` kind: a record started,
its steps claimed and run from the queue until it succeeds, a step whose
guard parks it and the wake-up that resumes it, a step that errors retried by
the queue from its cursor and failed as a defect on its last attempt, and a
step for a record that no longer runs doing nothing."""

from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path

import pytest
from worker_support import build_container, make_item, request, sign_in, start_noop

from acme.om.base import utcnow
from acme.om.context import TenantContext
from acme.om.orchestrations.rules import advanced
from acme.om.orchestrations.steps import step_rows
from acme.om.orchestrations.types.orchestration import (
    FailReason,
    Orchestration,
    OrchestrationKind,
    OrchestrationStatus,
    ParkReason,
)
from acme.om.work.types.handler import WorkHandlerInterface
from acme.om.work.types.work_item import WakeParkedPayload, WorkItem, WorkKind
from acme.workers.maintenance.container import WorkerContainer
from acme.workers.maintenance.main import build_loop
from acme.workers.maintenance.orchestrations import OrchestrationHandlerImpl

LEASE = timedelta(seconds=30)
KINDS = [WorkKind.ORCHESTRATION, WorkKind.WAKE_PARKED]


def handlers_of(container: WorkerContainer) -> Mapping[WorkKind, WorkHandlerInterface]:
    """The worker's own handlers."""
    return build_loop(container)._handlers  # pyright: ignore[reportPrivateUsage]


async def drain(
    container: WorkerContainer, handlers: Mapping[WorkKind, WorkHandlerInterface]
) -> list[WorkItem]:
    """Claims every available step and wake-up and runs it with `handlers`,
    settling it as the loop does."""
    ran: list[WorkItem] = []
    while True:
        claimed = await container.managers.work.claim(request(), "default", KINDS, "test", LEASE)
        if claimed is None:
            return ran
        ctx, item = claimed
        await handlers[item.kind].handle(ctx, item)
        await container.managers.work.complete(ctx, item)
        ran.append(item)


async def test_a_record_steps_from_the_queue_until_it_succeeds(tmp_path: Path) -> None:
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    started = await start_noop(container, ctx, 3)
    assert (started.status, started.cursor) == (OrchestrationStatus.RUNNING, 0)
    ran = await drain(container, handlers_of(container))
    assert [item.target_id for item in ran] == [started.id] * 3, "one item a step"
    done = await container.managers.orchestrations.get(ctx, started.id)
    assert (done.status, done.cursor, done.total) == (OrchestrationStatus.SUCCEEDED, 3, 3)
    assert done.finished_at is not None
    # The platform ran the steps; the record is still the person's.
    assert {item.created_by for item in ran} == {ctx.user_id}


class Guarded:
    """A step with a guard: while the provider it calls is down, it parks the
    record at its cursor, as a namespace's step does; otherwise it is the
    `noop` step."""

    def __init__(self, container: WorkerContainer) -> None:
        self.container = container
        self.down = True

    async def __call__(self, ctx: TenantContext, record: Orchestration) -> Orchestration:
        if not self.down:
            return await self.container.managers.orchestrations.step_noop(ctx, record)
        parked = advanced(
            record,
            utcnow(),
            ctx.user_id,
            cursor=record.cursor,
            total=record.total,
            park=ParkReason.PROVIDER_UNAVAILABLE,
        )
        storage = self.container.storage.get_orchestrations_storage()
        await storage.write_orchestration(
            ctx.org_id, parked, record.version, step_rows(ctx, parked)
        )
        return parked


async def test_a_guard_parks_the_record_and_the_wake_up_resumes_it(tmp_path: Path) -> None:
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    started = await start_noop(container, ctx, 2)
    guarded = Guarded(container)
    handlers = {
        **handlers_of(container),
        WorkKind.ORCHESTRATION: OrchestrationHandlerImpl(
            container.managers.orchestrations, {OrchestrationKind.NOOP: guarded}
        ),
    }
    assert len(await drain(container, handlers)) == 1, "a parked record asks for no step"
    parked = await container.managers.orchestrations.get(ctx, started.id)
    assert (parked.status, parked.park_reason, parked.cursor) == (
        OrchestrationStatus.PARKED,
        ParkReason.PROVIDER_UNAVAILABLE,
        0,
    )
    # The provider answers again: the wake-up resumes the record at its
    # cursor, and its steps run to the end.
    guarded.down = False
    wake = make_item(
        ctx,
        kind=WorkKind.WAKE_PARKED,
        payload=WakeParkedPayload(reason=ParkReason.PROVIDER_UNAVAILABLE),
        target_id=ctx.org_id,
    )
    await container.managers.work.enqueue(ctx, wake)
    ran = await drain(container, handlers)
    assert [item.kind for item in ran] == [WorkKind.WAKE_PARKED, *[WorkKind.ORCHESTRATION] * 2]
    done = await container.managers.orchestrations.get(ctx, started.id)
    assert (done.status, done.cursor, done.park_reason) == (
        OrchestrationStatus.SUCCEEDED,
        2,
        None,
    )


class Flaky:
    """A step that raises until it is told not to: a database that went away."""

    def __init__(self, container: WorkerContainer) -> None:
        self.container = container
        self.failing = True

    async def __call__(self, ctx: TenantContext, record: Orchestration) -> Orchestration:
        if self.failing:
            raise ConnectionResetError("the database went away")
        return await self.container.managers.orchestrations.step_noop(ctx, record)


async def test_a_step_that_errors_is_the_queues_to_retry_and_goes_on_from_its_cursor(
    tmp_path: Path,
) -> None:
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    started = await start_noop(container, ctx, 3)
    flaky = Flaky(container)
    handler = OrchestrationHandlerImpl(
        container.managers.orchestrations, {OrchestrationKind.NOOP: flaky}
    )
    steps = [WorkKind.ORCHESTRATION]
    claimed = await container.managers.work.claim(request(), "default", steps, "test", LEASE)
    assert claimed is not None
    item_ctx, item = claimed
    flaky.failing = False
    await handler.handle(item_ctx, item)  # the first step commits
    await container.managers.work.complete(item_ctx, item)
    flaky.failing = True
    claimed = await container.managers.work.claim(request(), "default", steps, "test", LEASE)
    assert claimed is not None
    item_ctx, item = claimed
    # An attempt that is not the last raises, for the queue to retry.
    with pytest.raises(ConnectionResetError):
        await handler.handle(item_ctx, item)
    halfway = await container.managers.orchestrations.get(ctx, started.id)
    assert (halfway.status, halfway.cursor) == (OrchestrationStatus.RUNNING, 1)
    # The retry: the same item, its next attempt, from the same cursor.
    flaky.failing = False
    await handler.handle(item_ctx, item.model_copy(update={"attempts": item.attempts + 1}))
    await container.managers.work.complete(item_ctx, item)
    assert len(await drain(container, {WorkKind.ORCHESTRATION: handler})) == 1
    done = await container.managers.orchestrations.get(ctx, started.id)
    assert (done.status, done.cursor) == (OrchestrationStatus.SUCCEEDED, 3)


async def test_the_last_attempt_fails_the_record_as_a_defect(tmp_path: Path) -> None:
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    started = await start_noop(container, ctx, 3)
    handler = OrchestrationHandlerImpl(
        container.managers.orchestrations, {OrchestrationKind.NOOP: Flaky(container)}
    )
    claimed = await container.managers.work.claim(request(), "default", KINDS, "test", LEASE)
    assert claimed is not None
    item_ctx, item = claimed
    last = item.model_copy(update={"attempts": item.max_attempts})
    await handler.handle(item_ctx, last)
    failed = await container.managers.orchestrations.get(ctx, started.id)
    assert failed.status is OrchestrationStatus.FAILED
    assert failed.fail_reason is FailReason.DEFECT
    # The type alone: a library's text quotes what it was handed.
    assert failed.fail_detail == "ConnectionResetError"
    assert failed.cursor == 0, "what it achieved stays; nothing more is done"


async def test_a_step_for_a_record_that_no_longer_runs_does_nothing(tmp_path: Path) -> None:
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    started = await start_noop(container, ctx, 3)
    record = await container.managers.orchestrations.get(ctx, started.id)
    ended = await container.managers.orchestrations.fail(ctx, record, FailReason.DEFECT)
    ran = await drain(container, handlers_of(container))
    assert len(ran) == 1, "the first step's item ran and found nothing to do"
    assert await container.managers.orchestrations.get(ctx, started.id) == ended
