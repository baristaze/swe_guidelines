"""The orchestrations swimlane: long-running records, each a status and a
cursor advanced one step at a time by whichever worker holds its work item.

This namespace is the mechanism, the same for every kind: the record, its
start, its failure, and the two ways a parked record wakes (the event that
clears its reason, or a person). What one step of a kind does is the
namespace whose rows it changes: that namespace lands the record beside its
own rows (`Step`), a park among them. The one kind this namespace steps
itself is `noop`, which changes nothing else."""

from abc import ABC, abstractmethod
from uuid import UUID

from acme.om.context import TenantContext
from acme.om.orchestrations.types.orchestration import (
    FailReason,
    Orchestration,
    OrchestrationKind,
    OrchestrationPage,
    ParkReason,
)


class OrchestrationsManagerInterface(ABC):
    @abstractmethod
    async def start(self, ctx: TenantContext, record: Orchestration) -> Orchestration:
        """The create: the record lands running at its first cursor, with the
        hint that announces it and the work row that asks for its first step,
        in one commit. The input must be the shape its kind fixes
        (`ORCHESTRATION_INPUTS`). An id written already answers the record as
        stored; so does a record kept per period whose period is open."""
        ...

    @abstractmethod
    async def get(self, ctx: TenantContext, record_id: UUID) -> Orchestration: ...

    @abstractmethod
    async def step_noop(self, ctx: TenantContext, record: Orchestration) -> Orchestration:
        """One step of a `noop` record: the cursor moves by one, and the record
        succeeds once it reaches its input's count. The record is written
        alone, conditioned on the version of `record`, with the work row of
        the next step while it runs."""
        ...

    @abstractmethod
    async def get_recent(
        self, ctx: TenantContext, kind: OrchestrationKind, limit: int
    ) -> OrchestrationPage:
        """The org's newest records of a kind, newest first; `limit` is clamped."""
        ...

    @abstractmethod
    async def resume(self, ctx: TenantContext, record_id: UUID) -> Orchestration:
        """A person's wake: a parked record runs again from its cursor, whatever
        its reason. A running record is answered as it is; a settled one is
        refused (`ValidationFailed`), since nothing is left to run."""
        ...

    @abstractmethod
    async def wake(self, ctx: TenantContext, reason: ParkReason) -> int:
        """The event's wake: every record of the org parked for `reason`
        resumes, staggered; returns how many. What cleared the reason asks
        for it (a provider that answers again clears `provider_unavailable`)."""
        ...

    @abstractmethod
    async def fail(
        self,
        ctx: TenantContext,
        record: Orchestration,
        reason: FailReason,
        detail: str | None = None,
    ) -> Orchestration:
        """A bound ends the record: nothing more is done, and what it achieved
        stays. Conditioned on the version of `record`. A step that still
        fails on its work item's last attempt ends here too, as `defect`."""
        ...

    @abstractmethod
    async def purge_across_tenants(self) -> int:
        """Platform-internal: the sweep, across tenants, once a pass: the
        records settled past the retention, a batch at most, whatever their
        tenant; returns how many. It takes no context, because it runs for
        no tenant and no principal."""
        ...

    @abstractmethod
    async def purge_tenant(self, ctx: TenantContext) -> int:
        """The sweep, for one tenant past its own retention: every record, a
        batch at most a call. Any other tenant returns 0 and reads nothing:
        its settled records go across tenants."""
        ...
