"""The transactional outbox: a handoff that follows a core write (the event
row behind every push, the work item a write starts) is never a second
statement a manager remembers to make. The manager writes the core row and
the `OutboxRow`s that announce it in one named atomic storage method, then
relays each at once (in the API, once the request's answer is sent); the
maintenance sweep claims whatever a crash or a dropped message left behind,
one attempt at a time with a growing
delay, fails a row whose attempts are spent (a dead letter), and purges what
is settled. The relay is idempotent on the row's id, so relaying twice is
harmless."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import timedelta
from uuid import UUID

from acme.om.outbox.types.row import OutboxRow


class OutboxRelayInterface(ABC):
    @abstractmethod
    async def relay(self, org_id: UUID, row: OutboxRow) -> bool:
        """The row's kind is its destination. An entity change appends the `Event`
        the row describes (idempotent on the row's id) and publishes
        ENTITY_CHANGED with (kind, target_id, seq); a row of kind `work.<kind>`
        enqueues the work item it names, under the row's id as the item's
        idempotency key, and publishes WORK_AVAILABLE. The row is then marked
        done: an entity change once the bus took its message, a work row once
        it is enqueued, since the queue is its truth and the workers poll
        (ADR 0062). Returns False, and never raises, when a step failed or
        the bus dropped the message: the row is durable, stays pending, and
        the sweep relays it again."""
        ...

    @abstractmethod
    async def relay_all(self, org_id: UUID, rows: Sequence[OutboxRow]) -> bool:
        """`relay` for the rows one write landed together, such as an import
        step's hundred tasks: the entity changes among them are appended in
        one call, so they take one run of contiguous numbers under one hold of
        the tenant's cursor instead of one hold each, and are published in
        that order; the work rows are enqueued one by one. The rows delivered
        are then marked done in one statement. A row whose message the bus
        dropped is not among them: it stays pending, and the rows beside it
        are marked. Returns False, and never raises, when a step failed or a
        message was dropped: the rows are durable and the sweep relays what
        is pending."""
        ...

    @abstractmethod
    def hold(self, request_id: UUID) -> None:
        """Platform-internal, for the API's edge, which holds each request
        before it runs: until `release`, a row that names this request is
        held and not relayed, and `relay` and `relay_all` return True for it.
        The row names its request (`OutboxRow.request_id`), so the hold is
        found by that field, never by ambient state. A worker holds nothing
        and relays at once. Two holds of one id (a caller that sent the same
        request id twice) share the rows: each release relays what is held
        when it runs, and the hold ends with the last."""
        ...

    @abstractmethod
    def held(self, request_id: UUID) -> int:
        """Platform-internal, for the API's edge: how many rows the request's
        hold keeps now; zero with no hold."""
        ...

    @abstractmethod
    async def release(self, request_id: UUID) -> int:
        """Platform-internal, for the API's edge: ends a hold and relays the
        rows it kept, each org's rows in one `relay_all`, in the order they
        were handed over. The edge calls it once the answer is sent, so the
        caller never waits for the relay.
        Never raises for a failed relay: it is logged and counted, and the
        sweep relays the rows. A cancellation logs how many rows it leaves to
        the sweep. Returns how many rows it relayed or tried to."""
        ...

    @abstractmethod
    def abandon(self, request_id: UUID) -> int:
        """Platform-internal, for the API's edge: ends a hold without its
        relay, as a process that is stopping does. The rows are durable, and
        the sweep relays them past its grace. Logs and returns how many rows
        it leaves."""
        ...

    @abstractmethod
    async def relay_pending(self, limit: int) -> int:
        """Platform-internal, for the sweep: claims up to `limit` rows whose next
        attempt is due and that are older than the grace (a younger row is the
        request path's to relay), oldest first and never a row another sweep
        holds, and relays each tenant's rows together, as `relay_all` does.
        When a tenant's rows fail together, each is relayed alone. A row that
        fails, or whose message the bus dropped while the rows beside it were
        published, keeps its error and waits out a delay that doubles per attempt,
        and one whose attempts are spent is failed for good, logged, counted,
        and named by an audit event. Returns how many were relayed."""
        ...

    @abstractmethod
    async def oldest_pending_age(self) -> timedelta:
        """Platform-internal, for the sweep's relay gauge: how long ago the
        oldest row that is neither done nor failed landed, across tenants;
        zero when every row is settled. A row relays within the request that
        wrote it, so an old one is a relay that keeps failing."""
        ...

    @abstractmethod
    async def failed_within(self, window: timedelta) -> int:
        """Platform-internal, for the sweep's dead-letter gauge: how many rows
        failed for good in the last `window`, across tenants. A dead letter is
        no longer pending, so the relay gauge no longer sees it; this does."""
        ...

    @abstractmethod
    async def purge_done(self, retention: timedelta, limit: int) -> int:
        """Platform-internal, for the sweep: deletes rows done or failed longer
        ago than `retention`, at most `limit` of each; returns how many. The
        one hard delete of the namespace."""
        ...
