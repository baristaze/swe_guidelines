import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import UUID

from pydantic import ValidationError

from acme.infra.observability import OUTCOMES
from acme.om.base import Platform, utcnow
from acme.om.exceptions import (
    NotFound,
    PreconditionFailed,
    TenantMismatch,
    ValidationFailed,
)
from acme.om.opcontext import OpContext, Permission
from acme.om.orchestrations.manager import OrchestrationsManagerInterface
from acme.om.orchestrations.rules import advanced, failed, is_settled, outcome, resumed, stagger
from acme.om.orchestrations.steps import step_rows
from acme.om.orchestrations.storage import OrchestrationsStorageInterface
from acme.om.orchestrations.types.orchestration import (
    ORCHESTRATION_INPUTS,
    FailReason,
    NoopInput,
    Orchestration,
    OrchestrationKind,
    OrchestrationPage,
    OrchestrationStatus,
    ParkReason,
)
from acme.om.outbox import OutboxRelayInterface
from acme.om.outbox.types.row import OutboxRow
from acme.om.tenancy import TenancyManagerInterface

log = logging.getLogger(__name__)


class OrchestrationsOptions(Platform):
    max_limit: int = 50
    # The steps one wake resumes start this far apart.
    stagger: timedelta = timedelta(seconds=2)
    wake_batch: int = 50  # records one wake resumes per org
    retention: timedelta = timedelta(days=30)  # a settled record is purged after this
    purge_batch: int = 1000  # records one purge statement deletes at most


class OrchestrationsManagerImpl(OrchestrationsManagerInterface):
    def __init__(
        self,
        storage: OrchestrationsStorageInterface,
        tenancy: TenancyManagerInterface,
        relay: OutboxRelayInterface,
        options: OrchestrationsOptions,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._storage = storage
        self._tenancy = tenancy
        self._relay = relay
        self._options = options
        self._clock = clock

    async def start(self, ctx: OpContext, record: Orchestration) -> Orchestration:
        ctx.require(Permission.WRITE)
        try:
            ORCHESTRATION_INPUTS[record.kind].model_validate(dict(record.input))
        except ValidationError as error:
            raise ValidationFailed(f"a {record.kind.value} input is not {error}") from None
        now = self._clock()
        created = record.model_copy(
            update={
                "created_at": now,
                "updated_at": now,
                "created_by": ctx.user_id,
                "updated_by": ctx.user_id,
                "status": OrchestrationStatus.RUNNING,
                "cursor": 0,
                "total": None,
                "applied": 0,
                "skipped": 0,
                "row_errors": (),
                "park_reason": None,
                "fail_reason": None,
                "fail_detail": None,
                "finished_at": None,
                "version": 1,
            }
        )
        rows = step_rows(ctx, created, created=True)
        if not await self._storage.create_orchestration(ctx.org_id, created, rows):
            # A retry under the same id, or the period's record opened already.
            existing = await self._storage.read_orchestration(ctx.org_id, created.id)
            if existing is None:
                raise TenantMismatch(f"orchestration {created.id} is not in {ctx.org_id}")
            return existing
        await self._relay_all(ctx, rows)
        log.info("started %s %s in org %s", created.kind.value, created.id, ctx.org_id)
        OUTCOMES.labels(subsystem="orchestrations", outcome=outcome(created)).inc()
        return created

    async def get(self, ctx: OpContext, record_id: UUID) -> Orchestration:
        ctx.require(Permission.READ)
        record = await self._storage.read_orchestration(ctx.org_id, record_id)
        if record is None:
            raise NotFound(f"orchestration {record_id} not found")
        return record

    async def step_noop(self, ctx: OpContext, record: Orchestration) -> Orchestration:
        ctx.require(Permission.WRITE)
        steps = NoopInput.model_validate(dict(record.input)).steps
        cursor = record.cursor + 1
        stepped = advanced(
            record, self._clock(), ctx.user_id, cursor=cursor, total=steps, finished=cursor >= steps
        )
        await self._write(ctx, stepped, record.version)
        return stepped

    async def get_recent(
        self, ctx: OpContext, kind: OrchestrationKind, limit: int
    ) -> OrchestrationPage:
        ctx.require(Permission.READ)
        limit = max(1, min(limit, self._options.max_limit))
        rows = await self._storage.read_recent(ctx.org_id, kind, limit + 1)
        return OrchestrationPage(items=tuple(rows[:limit]), has_more=len(rows) > limit)

    async def resume(self, ctx: OpContext, record_id: UUID) -> Orchestration:
        ctx.require(Permission.WRITE)
        record = await self.get(ctx, record_id)
        if record.status is OrchestrationStatus.RUNNING:
            return record
        if is_settled(record):
            raise ValidationFailed(f"orchestration {record_id} has {record.status.value}")
        return await self._resume(ctx, record, self._clock())

    async def wake(self, ctx: OpContext, reason: ParkReason) -> int:
        ctx.require(Permission.WRITE)
        now = self._clock()
        waiting = await self._storage.read_parked(ctx.org_id, reason, self._options.wake_batch)
        return await self._resume_all(ctx, waiting, now)

    async def fail(
        self,
        ctx: OpContext,
        record: Orchestration,
        reason: FailReason,
        detail: str | None = None,
    ) -> Orchestration:
        ctx.require(Permission.WRITE)
        ended = failed(record, self._clock(), ctx.user_id, reason, detail[:500] if detail else None)
        await self._write(ctx, ended, record.version)
        # A bound of the input is the person's to fix; a defect is ours.
        level = logging.ERROR if reason is FailReason.DEFECT else logging.WARNING
        log.log(
            level,
            "%s %s in org %s failed: %s %s",
            record.kind.value,
            record.id,
            ctx.org_id,
            reason.value,
            detail or "",
        )
        OUTCOMES.labels(subsystem="orchestrations", outcome=outcome(ended)).inc()
        return ended

    async def purge_across_tenants(self) -> int:
        return await self._storage.purge_settled(
            self._clock() - self._options.retention, self._options.purge_batch
        )

    async def purge_tenant(self, ctx: OpContext) -> int:
        ctx.require(Permission.WRITE)
        if not await self._tenancy.tenant_expired(ctx):
            return 0
        return await self._storage.purge_tenant(ctx.org_id, self._options.purge_batch)

    async def _resume_all(self, ctx: OpContext, records: list[Orchestration], now: datetime) -> int:
        """Each record's next step starts a stagger after the one before it,
        so the records one event wakes do not all start at once. A record
        another writer moved meanwhile is left to it."""
        resumed_count = 0
        for position, record in enumerate(records):
            try:
                await self._resume(ctx, record, now, now + stagger(position, self._options.stagger))
            except PreconditionFailed:
                continue
            resumed_count += 1
        if resumed_count:
            log.info("resumed %d records in org %s", resumed_count, ctx.org_id)
        return resumed_count

    async def _resume(
        self,
        ctx: OpContext,
        record: Orchestration,
        now: datetime,
        not_before: datetime | None = None,
    ) -> Orchestration:
        running = resumed(record, now, ctx.user_id)
        await self._write(ctx, running, record.version, not_before)
        OUTCOMES.labels(subsystem="orchestrations", outcome=f"{record.kind.value}_resumed").inc()
        return running

    async def _write(
        self,
        ctx: OpContext,
        record: Orchestration,
        expected_version: int,
        not_before: datetime | None = None,
    ) -> None:
        """The record and its rows in one compare-and-set, then the relay at
        once; the sweep relays what a crash left behind."""
        rows = step_rows(ctx, record, not_before=not_before)
        await self._storage.write_orchestration(ctx.org_id, record, expected_version, rows)
        await self._relay_all(ctx, rows)

    async def _relay_all(self, ctx: OpContext, rows: tuple[OutboxRow, ...]) -> None:
        await self._relay.relay_all(ctx.org_id, rows)
