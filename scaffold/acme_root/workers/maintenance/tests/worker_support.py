"""Helpers the worker tests share."""

import asyncio
import functools
import selectors
from collections.abc import Callable, Coroutine
from datetime import timedelta
from pathlib import Path
from uuid import UUID

from acme.infra.cache import CacheScope
from acme.infra.impl.local import InfraLocalImpl
from acme.integrations.identity import IdentityProviderInterface
from acme.integrations.identity.absent import IdentityProviderAbsentImpl
from acme.om.base import Platform, new_id, utcnow
from acme.om.context import AppContext, AppType, RequestContext, TenantContext
from acme.om.media.types.file import File, FilePurpose
from acme.om.orchestrations.types.orchestration import Orchestration, OrchestrationKind
from acme.om.root import build_tenancy
from acme.om.storage.impl.memory import StorageMemoryImpl
from acme.om.tenancy import TenancyManagerInterface
from acme.om.tenancy.impl.manager import TenancyOptions
from acme.om.work.types.handler import WorkHandlerInterface
from acme.om.work.types.work_item import WorkItem, WorkKind
from acme.workers.maintenance.container import WorkerContainer
from acme.workers.maintenance.handler import NoopHandlerImpl
from acme.workers.maintenance.loop import LoopOptions


class RecordingHandler(WorkHandlerInterface):
    """The production handler with a record of what it handled, for the tests only:
    the production handler keeps nothing per item."""

    def __init__(self) -> None:
        self._inner = NoopHandlerImpl()
        self.handled: list[WorkItem] = []

    async def handle(self, ctx: TenantContext, item: WorkItem) -> None:
        await self._inner.handle(ctx, item)
        self.handled.append(item)


def build_container(tmp_path: Path) -> WorkerContainer:
    return WorkerContainer.for_tests(StorageMemoryImpl(), InfraLocalImpl(tmp_path))


def request() -> RequestContext:
    """The request stage a test mints at its edge, one per call."""
    return RequestContext(
        request_id=new_id(), app=AppContext(type=AppType.PORTAL, version="portal@test")
    )


def signing(
    container: WorkerContainer, identity_provider: IdentityProviderInterface | None = None
) -> TenancyManagerInterface:
    """A tenancy manager over the worker's storage that signs people in: by
    address, with the local sign-in on, or through `identity_provider` when
    one is given. The worker signs nobody in itself."""
    return build_tenancy(
        container.storage.get_tenancy_storage(),
        container.managers.outbox,
        container.infra.get_cache(CacheScope.REALTIME_TICKET),
        TenancyOptions(dev_sign_in=identity_provider is None),
        identity_provider=identity_provider or IdentityProviderAbsentImpl(),
    )


async def sign_in(container: WorkerContainer, slug: str = "ajax") -> TenantContext:
    """A session in a seeded org, `ajax` unless named, whose owner is
    `ann@<slug>.test`, signed in by address."""
    tenancy = container.managers.tenancy
    email = "ann@example.test" if slug == "ajax" else f"ann@{slug}.test"
    _, org = await tenancy.bootstrap(request(), slug.title(), slug, email, "Ann")
    login = await signing(container).sign_in.dev_sign_in(request(), email)
    identity = await tenancy.authenticate_login(request(), login.token)
    issued = await tenancy.sign_in.exchange_login(identity, org.id)
    return await tenancy.authenticate(request(), issued.token)


async def upload(
    container: WorkerContainer, ctx: TenantContext, name: str = "photo.png", *, confirm: bool = True
) -> File:
    """A file the person stored in the org: its row, and its object in the
    store. Unconfirmed, the upload is started and never finished: a pending
    row, and no object."""
    data = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    now = utcnow()
    media = container.managers.media
    file = await media.create_file(
        ctx,
        File(
            id=new_id(),
            name=name,
            created_at=now,
            updated_at=now,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
            content_type="image/png",
            size_bytes=len(data),
            purpose=FilePurpose.UPLOAD,
        ),
    )
    if not confirm:
        return file
    await media.put_content(ctx, file.id, data)
    return await media.confirm_file(ctx, file.id)


async def start_noop(container: WorkerContainer, ctx: TenantContext, steps: int) -> Orchestration:
    """A `noop` record of `steps` steps, started by the person: it lands
    running, with the work row of its first step."""
    now = utcnow()
    return await container.managers.orchestrations.start(
        ctx,
        Orchestration(
            id=new_id(),
            created_at=now,
            updated_at=now,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
            kind=OrchestrationKind.NOOP,
            input={"steps": steps},
        ),
    )


def make_item(
    ctx: TenantContext,
    *,
    kind: WorkKind = WorkKind.NOOP,
    payload: Platform | None = None,
    target_id: UUID | None = None,
    traceparent: str | None = None,
) -> WorkItem:
    """The item a caller enqueues under its own context: the request that caused
    the work and its trace context are the caller's, and the enqueue leaves
    them as constructed."""
    now = utcnow()
    return WorkItem(
        id=new_id(),
        created_at=now,
        updated_at=now,
        created_by=ctx.user_id,
        updated_by=ctx.user_id,
        kind=kind,
        target_id=target_id or new_id(),
        idempotency_key=new_id(),
        request_id=ctx.request_id,
        traceparent=traceparent,
        payload={} if payload is None else payload.model_dump(mode="json"),
        available_at=now,
    )


def fast_options(**overrides: object) -> LoopOptions:
    base: dict[str, object] = {
        "worker_id": "maintenance-test",
        "capacity": 2,
        "lease": timedelta(seconds=0.6),
        "heartbeat_interval": timedelta(seconds=0.05),
        "sweep_interval": timedelta(seconds=0.1),
        "poll_interval": timedelta(seconds=0.05),
    }
    return LoopOptions.model_validate({**base, **overrides})


async def until(predicate: Callable[[], bool], polls: int = 300) -> None:
    """Returns once `predicate` holds, asked every hundredth of a second at
    most `polls` times. The bound counts the test's own turns, never the wall
    clock: a stall of the process, a collection or a busy runner, costs one
    poll and leaves the rest, so only a condition the code never meets fails
    the wait."""
    for _ in range(polls):
        if predicate():
            return
        await asyncio.sleep(0.01)
    assert predicate(), "condition not met in time"


async def ended(task: asyncio.Task[None], polls: int = 300) -> None:
    """Awaits `task`, which must end within `polls` turns, counted as `until`
    counts them; what it raised is raised here."""
    await until(task.done, polls)
    await task


# A real clock reads a little past the timer it woke for, never on it. The
# test clock does too, by a microsecond, so a deadline is never met to the
# instant, where a rounding error of the float decides which side it is on.
PAST_THE_TIMER = 1e-6


class _TestClockSelector(selectors.DefaultSelector):
    """The selector of a loop on the test clock. When nothing is ready and the
    loop would wait for its next timer, the clock moves to just past that
    timer and the wait ends at once. The loop still sees a socket or a thread
    that answers, but the clock does not wait for one, so a test on it keeps
    its work on the loop."""

    def __init__(self) -> None:
        super().__init__()
        self.now = 0.0

    def select(self, timeout: float | None = None) -> list[tuple[selectors.SelectorKey, int]]:
        ready = super().select(0)
        if ready or timeout == 0:
            return ready
        if timeout is None:
            return super().select(None)
        self.now += timeout + PAST_THE_TIMER
        return []


class _TestClockLoop(asyncio.SelectorEventLoop):
    def __init__(self) -> None:
        self._test_clock = _TestClockSelector()
        super().__init__(self._test_clock)

    def time(self) -> float:
        return self._test_clock.now


def on_the_test_clock[**P](test: Callable[P, Coroutine[object, object, None]]) -> Callable[P, None]:
    """Runs an async test on the test clock: an event loop whose clock stands
    still while the process works, and moves to the next timer only once
    every task waits on one. A stall of the process, a collection or a busy
    runner, moves no deadline on it, so a test can assert to the millisecond
    when the loop did something, and a lease of a minute passes in no wall
    time."""

    @functools.wraps(test)
    def run(*args: P.args, **kwargs: P.kwargs) -> None:
        with asyncio.Runner(loop_factory=_TestClockLoop) as runner:
            runner.run(test(*args, **kwargs))

    return run
