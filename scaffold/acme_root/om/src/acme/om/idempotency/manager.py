"""The idempotency swimlane: the durable outcome of a request the caller
may retry. The gateway begins a record before it runs a creating request
and finishes it with the outcome; a retry gets the stored record back. The
record names the attempt that holds it, and finish and release are that
attempt's alone: an attempt that ran past the pending lease and lost the
marker to a retry is refused, like a worker whose lease has passed. A
failure releases the attempt and keeps the record, so the retry reruns on
the id the record carries and a row the failed attempt left behind is found,
not repeated."""

from abc import ABC, abstractmethod
from uuid import UUID

from acme.om.context import OperatorContext, TenantContext
from acme.om.idempotency.types.record import IdempotencyRecord


class IdempotencyManagerInterface(ABC):
    @abstractmethod
    async def begin(
        self, ctx: TenantContext, key: str, request_digest: str, target_id: UUID
    ) -> IdempotencyRecord:
        """Writes a pending record for (tenant, user, key) carrying `target_id`, the
        id the create will use, and a freshly minted `attempt_id`, and returns it:
        a pending record is the caller's to run, with the record's `target_id`,
        which a re-arm and a take-over keep from the first attempt, and the
        record's `attempt_id`, which they replace; a finished record is replayed.
        A released record (no attempt, no outcome) is re-armed in one conditional
        write; a record another attempt holds is taken over only past the pending
        lease. Raises IdempotencyInProgress while another attempt holds the
        marker within its lease, and IdempotencyKeyReused when the stored record
        has another digest."""
        ...

    @abstractmethod
    async def finish(
        self, ctx: TenantContext, key: str, attempt_id: UUID, status: int, body: str
    ) -> IdempotencyRecord:
        """Records the outcome on the record `begin` wrote, in one write conditional
        on `attempt_id` still holding it. Only an outcome a retry cannot change
        belongs here: a refusal, never a failure. Raises NotFound without a record
        under the key, and IdempotencyAttemptLost (a Conflict) when the record is
        finished already or a retry took it over: the caller's work, if it
        landed, is the row the retry found, and the retry's outcome is the one
        replayed."""
        ...

    # The operator plane's marker: the same three moves under the operator's
    # identity in the system scope (`EMPTY_UUID`), where no tenant is named, so
    # the sweep of that scope purges them with the login credentials.

    @abstractmethod
    async def begin_for_operator(
        self, admin: OperatorContext, key: str, request_digest: str, target_id: UUID
    ) -> IdempotencyRecord:
        """`begin` for a creating request of the operator plane: the record is
        per (system scope, operator identity, key), and everything else is as
        on `begin`. Requires `OperatorPermission.WRITE`, since only a write
        carries a key."""
        ...

    @abstractmethod
    async def finish_for_operator(
        self, admin: OperatorContext, key: str, attempt_id: UUID, status: int, body: str
    ) -> IdempotencyRecord:
        """`finish` for the operator plane, on the record `begin_for_operator` wrote."""
        ...

    @abstractmethod
    async def release_for_operator(
        self, admin: OperatorContext, key: str, attempt_id: UUID
    ) -> None:
        """`release` for the operator plane, on the record `begin_for_operator` wrote."""
        ...

    @abstractmethod
    async def purge_across_tenants(self) -> int:
        """Platform-internal: the sweep, across tenants, once a pass: deletes
        finished and released records past the retention and held pending
        ones past ten times the pending lease, a marker no retry came back
        for, a batch at most; returns how many. A key purged is a key free
        again. It takes no context, because it runs for no tenant and no
        principal."""
        ...

    @abstractmethod
    async def release(self, ctx: TenantContext, key: str, attempt_id: UUID) -> None:
        """Clears the attempt from the pending record `begin` wrote, because the
        attempt failed in a way a retry may change. The record stays, with its
        digest and its `target_id`, so the next attempt re-arms it and reruns on
        the same id: a row the failed attempt left behind is found, not repeated.
        One write conditional on `attempt_id` still holding the record; raises
        IdempotencyAttemptLost when it does not, so an attempt that lost the
        marker cannot release what a retry now holds."""
        ...
