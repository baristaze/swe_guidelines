"""Storage of the orchestrations swimlane. Every operation takes org_id first.
A record's every write after the create is a compare-and-set on its
version; a step's own write lands in the storage of the namespace whose rows
it changes, with the record beside it (`Step`)."""

from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from acme.om.orchestrations.types.orchestration import (
    Orchestration,
    OrchestrationKind,
    ParkReason,
    Step,
)
from acme.om.outbox.types.row import OutboxRow


class StepLandingInterface(ABC):
    """What another namespace's memory storage needs to land a step in the
    same step as its effect, the twin of the statement the Postgres impls
    share (`orchestrations.storage.impl.postgres.step_statement`)."""

    @abstractmethod
    def check_step(self, org_id: UUID, step: Step) -> None:
        """Raises `PreconditionFailed` when the stored record is gone or at
        another version than the step read, before anything is written."""
        ...

    @abstractmethod
    def land_step(self, org_id: UUID, step: Step, applied: int) -> None:
        """Writes the record as the step left it, its `applied` grown by the
        rows the effect wrote. Called after `check_step`, in the same step."""
        ...


class OrchestrationsStorageInterface(ABC):
    @abstractmethod
    async def create_orchestration(
        self, org_id: UUID, record: Orchestration, outbox_rows: tuple[OutboxRow, ...]
    ) -> bool:
        """The create, with the rows that announce it and ask for its first
        step in one commit; False, with nothing landed, when the id is
        written already, or when the org has a record of the kind for the
        period (the unique key a record kept per period opens under)."""
        ...

    @abstractmethod
    async def read_orchestration(self, org_id: UUID, record_id: UUID) -> Orchestration | None: ...

    @abstractmethod
    async def read_recent(
        self, org_id: UUID, kind: OrchestrationKind, limit: int
    ) -> list[Orchestration]:
        """The org's records of a kind, newest first by id. `limit` is the
        caller's: the manager asks for one row more than its page."""
        ...

    @abstractmethod
    async def read_parked(
        self, org_id: UUID, reason: ParkReason, limit: int
    ) -> list[Orchestration]:
        """The org's records parked for `reason`, oldest first."""
        ...

    @abstractmethod
    async def write_orchestration(
        self,
        org_id: UUID,
        record: Orchestration,
        expected_version: int,
        outbox_rows: tuple[OutboxRow, ...],
    ) -> None:
        """The compare-and-set: lands the record and its outbox rows together
        when the stored one is at `expected_version`, and raises
        `PreconditionFailed` otherwise, landing nothing."""
        ...

    @abstractmethod
    async def purge_settled(self, before: datetime, limit: int) -> int:
        """Cross-tenant, for the sweep, in the system scope, once a pass for
        every tenant: removes at most `limit` records that settled before
        `before`, whatever their tenant, skipping rows another transaction
        holds; returns how many. A parked or a running record is never
        purged."""
        ...

    @abstractmethod
    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        """At most `limit` records of a deleted tenant past its retention;
        returns how many."""
        ...
