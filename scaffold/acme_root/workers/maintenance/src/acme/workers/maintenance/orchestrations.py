"""The handlers of long-running records: one step of a record, and the wake
of the records an event cleared the reason of.

A step reads the record when it runs. A record that is no longer running
(parked, finished, failed) has nothing for the item to do, and the item
completes. What one step of a kind does is the namespace whose rows it
changes, which lands the step's effect and the record's next cursor in one
commit, with the work row of the next step; so a worker that dies leaves the
record at its last committed step, and the item comes back to the queue and
goes on from there.

An error a step raises is the work queue's: the item is retried with a
growing delay, and the retry starts again at the cursor the last commit
left. On the item's last attempt the record fails `defect` instead, so a
record never waits on work that will not come. A guard parks inside the step
itself; only a bound fails."""

import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import ClassVar

from acme.infra.observability import OUTCOMES, described
from acme.om.context import Permission, TenantContext
from acme.om.exceptions import NotFound, PreconditionFailed
from acme.om.orchestrations import OrchestrationsManagerInterface
from acme.om.orchestrations.rules import outcome
from acme.om.orchestrations.types.orchestration import (
    FailReason,
    Orchestration,
    OrchestrationKind,
    OrchestrationStatus,
)
from acme.om.work.rules import is_exhausted
from acme.om.work.types.handler import WorkHandlerInterface
from acme.om.work.types.work_item import WakeParkedPayload, WorkItem

log = logging.getLogger(__name__)

StepFn = Callable[[TenantContext, Orchestration], Awaitable[Orchestration]]
"""One step of a kind: `OrchestrationsManagerInterface.step_noop`, or the step
of the namespace whose rows a product's kind changes."""


class OrchestrationHandlerImpl(WorkHandlerInterface):
    REQUIRES: ClassVar[tuple[Permission, ...]] = (Permission.READ, Permission.WRITE)
    """`get` reads; every step and `fail` write."""

    def __init__(
        self,
        orchestrations: OrchestrationsManagerInterface,
        steps: Mapping[OrchestrationKind, StepFn],
    ) -> None:
        self._orchestrations = orchestrations
        self._steps = steps

    async def handle(self, ctx: TenantContext, item: WorkItem) -> None:
        try:
            record = await self._orchestrations.get(ctx, item.target_id)
        except NotFound:
            log.info("orchestration %s is gone; step %s has nothing to do", item.target_id, item.id)
            return
        if record.status is not OrchestrationStatus.RUNNING:
            log.info(
                "%s %s is %s; step %s has nothing to do",
                record.kind.value,
                record.id,
                record.status.value,
                item.id,
            )
            return
        try:
            after = await self._steps[record.kind](ctx, record)
        except PreconditionFailed:
            # Another holder wrote the record since it was read here: the
            # step it took is the one that counts, and it asked for the next.
            log.info(
                "%s %s moved under step %s; left to its holder",
                record.kind.value,
                record.id,
                item.id,
            )
            return
        except Exception as error:
            if not is_exhausted(item):
                raise  # the queue retries the item with a growing delay
            log.exception(
                "%s %s failed on the last attempt of %s", record.kind.value, record.id, item.id
            )
            await self._orchestrations.fail(ctx, record, FailReason.DEFECT, described(error))
            return
        log.info(
            "%s %s at %d of %s: %s",
            record.kind.value,
            record.id,
            after.cursor,
            after.total if after.total is not None else "?",
            after.status.value,
        )
        if after.status is not OrchestrationStatus.RUNNING:
            # A park or an end the step wrote itself; a failure the manager
            # wrote counts itself there.
            OUTCOMES.labels(subsystem="orchestrations", outcome=outcome(after)).inc()


class WakeParkedHandlerImpl(WorkHandlerInterface):
    """The reason the org's records parked for is gone: every one of them
    resumes, staggered, and its next step asks its guard again. Idempotent:
    a record already running is not parked, and is not touched."""

    REQUIRES: ClassVar[tuple[Permission, ...]] = (Permission.WRITE,)
    """`wake` writes."""

    def __init__(self, orchestrations: OrchestrationsManagerInterface) -> None:
        self._orchestrations = orchestrations

    async def handle(self, ctx: TenantContext, item: WorkItem) -> None:
        reason = WakeParkedPayload.model_validate(dict(item.payload)).reason
        woken = await self._orchestrations.wake(ctx, reason)
        log.info("woke %d records parked for %s in org %s", woken, reason.value, ctx.org_id)
