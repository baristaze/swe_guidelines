from datetime import timedelta
from uuid import UUID

from acme.om.base import EMPTY_UUID, Platform, new_id, utcnow
from acme.om.context import OperatorContext, OperatorPermission, Permission, TenantContext
from acme.om.exceptions import (
    DuplicateIdempotencyKey,
    IdempotencyAttemptLost,
    IdempotencyInProgress,
    IdempotencyKeyReused,
    NotFound,
)
from acme.om.idempotency.manager import IdempotencyManagerInterface
from acme.om.idempotency.storage import IdempotencyStorageInterface
from acme.om.idempotency.types.attempt import lease_bound
from acme.om.idempotency.types.record import IdempotencyRecord


class IdempotencyOptions(Platform):
    pending_ttl: timedelta = timedelta(minutes=2)
    """The pending lease, run from the attempt and never from the marker: an
    attempt older than this was abandoned by a crash between the marker and its
    outcome, or is still running past its lease; the next retry takes the marker
    over, stamps its own attempt, and runs the request again, so the marker
    never suppresses work for good and a marker handed on twice is not stale for
    having been minted long ago."""
    retention: timedelta = timedelta(hours=24)
    """A finished or released record is purged this long after it was written:
    a retry that late begins afresh."""
    abandoned_after: int = 10
    """A marker still held by an attempt older than this many pending leases had
    no retry come back for it and is purged."""
    purge_batch: int = 1000
    """Records one purge statement deletes at most; the sweep calls again for
    the rest."""


class IdempotencyManagerImpl(IdempotencyManagerInterface):
    def __init__(self, storage: IdempotencyStorageInterface, options: IdempotencyOptions) -> None:
        self._storage = storage
        self._options = options

    async def begin(
        self, ctx: TenantContext, key: str, request_digest: str, target_id: UUID
    ) -> IdempotencyRecord:
        ctx.require(Permission.WRITE)
        return await self._begin(ctx.org_id, ctx.user_id, key, request_digest, target_id)

    async def begin_for_operator(
        self, admin: OperatorContext, key: str, request_digest: str, target_id: UUID
    ) -> IdempotencyRecord:
        admin.require(OperatorPermission.WRITE)
        return await self._begin(EMPTY_UUID, admin.identity_id, key, request_digest, target_id)

    async def finish_for_operator(
        self, admin: OperatorContext, key: str, attempt_id: UUID, status: int, body: str
    ) -> IdempotencyRecord:
        admin.require(OperatorPermission.WRITE)
        return await self._finish(EMPTY_UUID, admin.identity_id, key, attempt_id, status, body)

    async def release_for_operator(
        self, admin: OperatorContext, key: str, attempt_id: UUID
    ) -> None:
        admin.require(OperatorPermission.WRITE)
        await self._release(EMPTY_UUID, admin.identity_id, key, attempt_id)

    async def _begin(
        self, org_id: UUID, user_id: UUID, key: str, request_digest: str, target_id: UUID
    ) -> IdempotencyRecord:
        pending = IdempotencyRecord(
            id=new_id(),
            user_id=user_id,
            key=key,
            request_digest=request_digest,
            target_id=target_id,
            attempt_id=new_id(),
            created_at=utcnow(),
        )
        try:
            # The unique index is the dedupe: the first writer wins, every other
            # writer reads what it wrote.
            await self._storage.write_record(org_id, pending)
        except DuplicateIdempotencyKey:
            stored = await self._storage.read_record(org_id, user_id, key)
            if stored is None:
                raise
            if stored.request_digest != request_digest:
                raise IdempotencyKeyReused(
                    f"idempotency key {key!r} was used for a different request"
                ) from None
            if stored.pending:
                # Either a failure released the marker (no attempt), which
                # is taken at once, or it is held: abandoned when the marker
                # was written and its effect never landed, or the effect landed
                # and the outcome did not, or the first attempt is still
                # running past its lease, which the bound below reads off its
                # token. The re-arm and the take-over are each one conditional
                # write that stamps a new attempt token, so of two retries
                # racing for the marker exactly one runs the request again, on
                # the target_id the first attempt minted, so a create that
                # already landed is found and not repeated; the other sees the
                # marker the new token holds, and the first attempt, if it is
                # still running, is refused at its finish or release.
                if stored.released:
                    armed = await self._storage.rearm_released(org_id, user_id, key, new_id())
                    if armed is not None:
                        return armed
                else:
                    taken = await self._storage.take_over_pending(
                        org_id,
                        user_id,
                        key,
                        lease_bound(utcnow() - self._options.pending_ttl),
                        new_id(),
                    )
                    if taken is not None:
                        return taken
                raise IdempotencyInProgress(
                    f"idempotency key {key!r} is still being processed"
                ) from None
            return stored
        return pending

    async def purge_across_tenants(self) -> int:
        now = utcnow()
        return await self._storage.purge_records(
            now - self._options.retention,
            lease_bound(now - self._options.pending_ttl * self._options.abandoned_after),
            self._options.purge_batch,
        )

    async def release(self, ctx: TenantContext, key: str, attempt_id: UUID) -> None:
        ctx.require(Permission.WRITE)
        await self._release(ctx.org_id, ctx.user_id, key, attempt_id)

    async def _release(self, org_id: UUID, user_id: UUID, key: str, attempt_id: UUID) -> None:
        if not await self._storage.release_pending(org_id, user_id, key, attempt_id):
            raise IdempotencyAttemptLost(
                f"idempotency key {key!r} is not held by attempt {attempt_id}"
            )

    async def finish(
        self, ctx: TenantContext, key: str, attempt_id: UUID, status: int, body: str
    ) -> IdempotencyRecord:
        ctx.require(Permission.WRITE)
        return await self._finish(ctx.org_id, ctx.user_id, key, attempt_id, status, body)

    async def _finish(
        self, org_id: UUID, user_id: UUID, key: str, attempt_id: UUID, status: int, body: str
    ) -> IdempotencyRecord:
        finished = await self._storage.finish_pending(
            org_id, user_id, key, attempt_id, status, body
        )
        if finished is not None:
            return finished
        # Refused: the statement matched nothing. Only now is a read worth it,
        # to say whether the key was never begun or the marker is another
        # attempt's (taken over, or finished already).
        if await self._storage.read_record(org_id, user_id, key) is None:
            raise NotFound(f"idempotency key {key!r} was never begun")
        raise IdempotencyAttemptLost(f"idempotency key {key!r} is not held by attempt {attempt_id}")
