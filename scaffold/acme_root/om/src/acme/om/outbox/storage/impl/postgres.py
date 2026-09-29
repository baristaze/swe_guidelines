from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import DateTime, Interval, Uuid, any_, bindparam, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import ARRAY

from acme.om.base import EMPTY_UUID, utcnow
from acme.om.outbox.rules import MAX_DOUBLINGS
from acme.om.outbox.storage import OutboxStorageInterface
from acme.om.outbox.storage.tables.outbox_rows import OutboxRows
from acme.om.outbox.types.row import OutboxRow
from acme.om.storage.impl.pg_base import PLAN_WITH_VALUES, PgStorageBase, delete_batch, deleted
from acme.om.storage.utils.translation import to_model


class OutboxStoragePostgresImpl(PgStorageBase, OutboxStorageInterface):
    async def claim_pending(
        self,
        limit: int,
        now: datetime,
        grace: timedelta,
        backoff_base: timedelta,
        backoff_cap: timedelta,
    ) -> list[OutboxRow]:
        candidates = (
            select(OutboxRows.id)
            .where(
                OutboxRows.done_at.is_(None),
                OutboxRows.failed_at.is_(None),
                or_(OutboxRows.next_attempt_at.is_(None), OutboxRows.next_attempt_at <= now),
                OutboxRows.created_at < now - grace,
            )
            .order_by(OutboxRows.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
            # Materialized, so the candidates are chosen and locked once. As
            # a plain IN subquery the planner may take a semi join that
            # rescans it per row, and each rescan skips the rows this update
            # already changed and locks the next ones: a claim past `limit`
            # that leaves the other sweep nothing.
            .cte("candidates")
            .prefix_with("MATERIALIZED")
        )
        # rules.relay_delay, in SQL: the attempt being spent is attempts + 1,
        # so its delay is base * 2^attempts, capped.
        delay = func.least(
            literal(backoff_base, Interval())
            * func.power(2, func.least(OutboxRows.attempts, MAX_DOUBLINGS)),
            literal(backoff_cap, Interval()),
        )
        stmt = (
            update(OutboxRows)
            .where(OutboxRows.id.in_(select(candidates.c.id)))
            .values(
                attempts=OutboxRows.attempts + 1,
                next_attempt_at=literal(now, DateTime(timezone=True)) + delay,
            )
            .returning(OutboxRows)
        )
        async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
            rows = (await session.execute(stmt)).scalars().all()
            claimed = sorted((to_model(row, OutboxRow) for row in rows), key=lambda r: r.id)
            await session.commit()
            return claimed

    async def mark_done(self, org_id: UUID, row_ids: Sequence[UUID]) -> None:
        if not row_ids:
            return
        # One array parameter, not an IN list: the statement is the same text
        # for one row or a hundred, so one prepared statement serves them all.
        ids = bindparam("row_ids", list(row_ids), type_=ARRAY(Uuid()))
        stmt = (
            update(OutboxRows)
            .where(
                OutboxRows.id == any_(ids),
                OutboxRows.org_id == org_id,
                OutboxRows.done_at.is_(None),
            )
            .values(done_at=utcnow())
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            await session.execute(stmt)
            await session.commit()

    async def record_failure(
        self, org_id: UUID, row_id: UUID, error: str, failed_at: datetime | None
    ) -> None:
        stmt = (
            update(OutboxRows)
            .where(
                OutboxRows.id == row_id,
                OutboxRows.org_id == org_id,
                OutboxRows.done_at.is_(None),
            )
            .values(last_error=error, failed_at=failed_at)
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            await session.execute(stmt)
            await session.commit()

    async def oldest_pending_at(self) -> datetime | None:
        # The pending rows are the head of `ix_outbox_rows_done_at_id`, where
        # done_at is null: the backlog and the dead letters not yet purged,
        # and never the done rows behind them.
        stmt = select(func.min(OutboxRows.created_at)).where(
            OutboxRows.done_at.is_(None), OutboxRows.failed_at.is_(None)
        )
        async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
            return (await session.execute(stmt)).scalar_one()

    async def count_failed_since(self, since: datetime) -> int:
        # A range of the partial `ix_outbox_rows_failed_at`, which holds the
        # dead letters alone: the comparison implies `failed_at IS NOT NULL`.
        stmt = select(func.count()).where(OutboxRows.failed_at > since)
        async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
            return (await session.execute(stmt)).scalar_one()

    async def purge_done(self, before: datetime, limit: int) -> int:
        # Two statements, not one with an OR: each branch has an index of its
        # own, `ix_outbox_rows_done_at_id` and the partial
        # `ix_outbox_rows_failed_at`, and an OR would read neither. Each is
        # planned with its values, so its index serves an idle pass too.
        purged = 0
        for stmt in (
            delete_batch(OutboxRows, OutboxRows.done_at < before, limit=limit),
            delete_batch(OutboxRows, OutboxRows.failed_at < before, limit=limit),
        ):
            async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
                await session.execute(PLAN_WITH_VALUES)
                purged += deleted(await session.execute(stmt))
                await session.commit()
        return purged
