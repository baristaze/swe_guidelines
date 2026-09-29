"""Storage of idempotency records. The scope is user-bound within the
tenant: both keys are passed and both are in every WHERE clause, but the
sweep's purge, which reaches across tenants in the system scope. The writes
that move a pending record (take over, re-arm, finish, release) are
conditional in the statement itself and report what they matched: a
take-over, a re-arm, and a finish return the record as written or None, a
release returns whether the attempt went. The pending lease is passed as a
token bound (see `types/attempt.py`) and never as a clock, and no write here
touches the birth time. Nothing here raises for a refused attempt; the
manager names it."""

from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from acme.om.idempotency.types.record import IdempotencyRecord


class AttemptFenceInterface(ABC):
    """What another namespace's storage needs from the markers to fence the one
    write of a rerun that changes what is stored: whether the marker still
    holds the attempt making the write. The Postgres impls read the marker in
    their own WHERE, in the same statement; the memory impls ask here, which is
    the twin of that, the way the outbox has a landing of its own."""

    @abstractmethod
    def holds(self, org_id: UUID, target_id: UUID, attempt_id: UUID) -> bool:
        """Whether this tenant has a marker on `target_id` that is still
        pending and still held by `attempt_id`."""
        ...


class IdempotencyStorageInterface(ABC):
    @abstractmethod
    async def write_record(self, org_id: UUID, record: IdempotencyRecord) -> None:
        """Raises DuplicateIdempotencyKey when another record carries the same
        (org_id, user_id, key)."""
        ...

    @abstractmethod
    async def read_record(
        self, org_id: UUID, user_id: UUID, key: str
    ) -> IdempotencyRecord | None: ...

    @abstractmethod
    async def finish_pending(
        self, org_id: UUID, user_id: UUID, key: str, attempt_id: UUID, status: int, body: str
    ) -> IdempotencyRecord | None:
        """One conditional statement: stores the outcome on the record under
        (org_id, user_id, key) only while it is still pending and held by
        `attempt_id`. Returns the record as written, or None when nothing
        matched: no such record, already finished, or held by another attempt."""
        ...

    @abstractmethod
    async def release_pending(
        self, org_id: UUID, user_id: UUID, key: str, attempt_id: UUID
    ) -> bool:
        """One conditional statement: clears the attempt of the record under
        (org_id, user_id, key) only while it is still pending and held by
        `attempt_id`. The record stays, with its digest and its target id and no
        attempt, so the next retry re-arms it and reruns on the same id. Returns
        whether the attempt went; a finished record is untouched, and so is one
        another attempt holds."""
        ...

    @abstractmethod
    async def purge_records(
        self, finished_before: datetime, attempts_before: UUID, limit: int
    ) -> int:
        """Cross-tenant, for the sweep, in the system scope, once a pass for
        every tenant: deletes finished and released records that were born
        before `finished_before` (past the retention, a retry begins
        afresh) and held pending ones whose attempt token sorts below
        `attempts_before` (a marker no retry ever came back for, measured from
        the attempt like the lease is), at most `limit` of them, skipping rows
        another transaction holds; returns how many. The one hard delete of
        the namespace."""
        ...

    @abstractmethod
    async def take_over_pending(
        self,
        org_id: UUID,
        user_id: UUID,
        key: str,
        abandoned_before: UUID,
        attempt_id: UUID,
    ) -> IdempotencyRecord | None:
        """Stamps `attempt_id` as the holder of the record under (org_id,
        user_id, key), but only while it is still pending and held by an attempt
        whose token sorts below `abandoned_before`, the bound of the pending
        lease: one conditional write, so of two retries racing for an abandoned
        marker exactly one takes it over and runs the request again, and the
        attempt that lost it can no longer finish or release it. The new token
        starts the lease again, and the marker keeps the birth time it was
        written with. Returns the record as written, or None when nothing
        matched; a released marker is re-armed, never taken over."""
        ...

    @abstractmethod
    async def rearm_released(
        self, org_id: UUID, user_id: UUID, key: str, attempt_id: UUID
    ) -> IdempotencyRecord | None:
        """Stamps `attempt_id` on the record under (org_id, user_id, key), but
        only while it has no attempt and no outcome, a marker a failure
        released: one conditional write, so of two retries racing for a released
        marker exactly one re-arms it and reruns the request on the marker's
        target id, and the other finds it held. A released marker holds no
        attempt, so it is taken at once, whatever its age, and the new token
        starts the lease. Returns the record as written, or None when nothing
        matched."""
        ...
