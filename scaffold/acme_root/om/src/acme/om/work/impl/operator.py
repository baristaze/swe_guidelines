import logging
from uuid import UUID

from pydantic import ValidationError

from acme.infra.observability import OUTCOMES
from acme.infra.topics import EntityChangedPayload, Topics, TopicsInterface, WorkAvailablePayload
from acme.om.base import new_id, utcnow
from acme.om.context import OperatorContext, OperatorPermission
from acme.om.events.storage import EventStorageInterface
from acme.om.events.types.event import Event
from acme.om.exceptions import NotFound, ValidationFailed, WorkNotFailed
from acme.om.tenancy.storage import TenancyStorageInterface
from acme.om.work.manager import WorkOperatorManagerInterface
from acme.om.work.storage import WorkStorageInterface
from acme.om.work.types.tenant_cap import TenantCap
from acme.om.work.types.work_item import WorkItem, WorkStatus

log = logging.getLogger(__name__)

REQUEUED_KIND = "work.item.requeued"
"""The audit event an operator's requeue leaves in the tenant's stream."""


class WorkOperatorManagerImpl(WorkOperatorManagerInterface):
    """Writes one named org's work item through the work storage, under the
    tenant the operator named, and reads the org through the tenancy
    storage, as the other operator planes do: no `TenantContext` exists on this
    plane, so no tenant manager is asked."""

    def __init__(
        self,
        storage: WorkStorageInterface,
        tenancy: TenancyStorageInterface,
        events: EventStorageInterface,
        topics: TopicsInterface,
    ) -> None:
        self._storage = storage
        self._tenancy = tenancy
        self._events = events
        self._topics = topics

    async def requeue(self, admin: OperatorContext, org_id: UUID, item_id: UUID) -> WorkItem:
        admin.require(OperatorPermission.WRITE)
        # A deleted org's item is failed at its claim, so it has no way
        # forward to send it back to.
        await self._live_org(org_id)
        stored = await self._storage.read_item(org_id, item_id)
        if stored is None:
            raise NotFound(f"work item {item_id} not found")
        if stored.status is not WorkStatus.FAILED:
            raise WorkNotFailed(f"work item {item_id} is {stored.status.value}, not failed")
        now = utcnow()
        requeued = stored.model_copy(
            update={
                "status": WorkStatus.QUEUED,
                "available_at": now,
                "attempts": 0,
                "claimed_by": None,
                "claim_token": None,
                "lease_expires_at": None,
                "last_error": None,
                "updated_at": now,
                # An operator has no user in the tenant: the identity is the actor.
                "updated_by": admin.identity_id,
            }
        )
        written = await self._storage.write_item_if_failed(org_id, requeued)
        if written is None:
            # Another requeue moved it, or the sweep purged it, since the read.
            raise WorkNotFailed(f"work item {item_id} is no longer failed")
        log.info(
            "operator %s requeued work item %s (%s) in org %s",
            admin.identity_id,
            item_id,
            stored.kind.value,
            org_id,
        )
        OUTCOMES.labels(subsystem="work", outcome="requeued").inc()
        await self._audit(admin, org_id, stored)
        await self._topics.publish(
            Topics.WORK_AVAILABLE,
            WorkAvailablePayload(
                idempotency_key=new_id(),
                produced_at=now,
                org_id=org_id,
                lane=written.lane,
                kind=written.kind.value,
            ),
        )
        return written

    async def set_tenant_cap(
        self, admin: OperatorContext, org_id: UUID, lane: str, cap: int
    ) -> TenantCap:
        admin.require(OperatorPermission.WRITE)
        # A deleted org's items are failed at their claim: no cap is its.
        await self._live_org(org_id)
        now = utcnow()
        try:
            # An operator has no user in the tenant: the identity is the actor.
            wanted = TenantCap(
                id=new_id(),
                lane=lane,
                cap=cap,
                created_at=now,
                updated_at=now,
                created_by=admin.identity_id,
                updated_by=admin.identity_id,
            )
        except ValidationError as error:
            raise ValidationFailed(str(error)) from None
        written = await self._storage.write_tenant_cap(org_id, wanted)
        log.info(
            "operator %s set the cap of org %s on lane %s to %d",
            admin.identity_id,
            org_id,
            lane,
            cap,
        )
        return written

    async def read_tenant_cap(self, admin: OperatorContext, org_id: UUID, lane: str) -> TenantCap:
        admin.require(OperatorPermission.READ)
        if await self._tenancy.read_org(org_id) is None:
            raise NotFound(f"org {org_id} not found")
        stored = await self._storage.read_tenant_cap(org_id, lane)
        # The support trail: the tenant and the operator, as every read of a
        # tenant's rows on this plane leaves.
        log.info("operator %s read the cap on lane %s of org %s", admin.identity_id, lane, org_id)
        if stored is None:
            raise NotFound(f"org {org_id} has no cap of its own on lane {lane}")
        return stored

    async def clear_tenant_cap(self, admin: OperatorContext, org_id: UUID, lane: str) -> TenantCap:
        admin.require(OperatorPermission.WRITE)
        if await self._tenancy.read_org(org_id) is None:
            raise NotFound(f"org {org_id} not found")
        removed = await self._storage.delete_tenant_cap(org_id, lane)
        if removed is None:
            raise NotFound(f"org {org_id} has no cap of its own on lane {lane}")
        log.info(
            "operator %s cleared the cap of org %s on lane %s", admin.identity_id, org_id, lane
        )
        return removed

    async def _live_org(self, org_id: UUID) -> None:
        org = await self._tenancy.read_org(org_id)
        if org is None or org.deleted_at is not None:
            raise NotFound(f"org {org_id} not found")

    async def _audit(self, admin: OperatorContext, org_id: UUID, failed: WorkItem) -> None:
        """The event that names the requeue and who made it, with what the
        item was when it failed. The queue row is in the `queue` role and the
        stream in `core`, so this follows the write, as the dead letter's
        event does: a crash between the two loses the entry, never the
        requeue."""
        (event,) = await self._events.append_events(
            org_id,
            [
                Event(
                    id=new_id(),
                    org_id=org_id,
                    kind=REQUEUED_KIND,
                    target_id=failed.id,
                    payload={
                        "kind": failed.kind.value,
                        "work_target_id": str(failed.target_id),
                        "attempts": failed.attempts,
                        "last_error": failed.last_error,
                    },
                    produced_at=utcnow(),
                    actor_id=admin.identity_id,
                    request_id=admin.request_id,
                    app=admin.app.type.value,
                ),
            ],
        )
        await self._topics.publish(
            Topics.ENTITY_CHANGED,
            EntityChangedPayload(
                idempotency_key=event.id,
                produced_at=event.produced_at,
                org_id=org_id,
                kind=event.kind,
                target_id=event.target_id,
                seq=event.seq,
                actor_id=event.actor_id,
            ),
        )
