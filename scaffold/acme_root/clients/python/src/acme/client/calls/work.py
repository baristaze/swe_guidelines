"""The work namespace's calls: an org's work items, on the operator plane,
whose bearer is an operator token."""

from uuid import UUID

from acme.client.transport import Transport
from acme.client.types.work import OperatorWorkItemView


class WorkCalls(Transport):
    """An org's failed work item sent back to the queue."""

    async def admin_requeue_work(self, org_id: UUID, item_id: UUID) -> OperatorWorkItemView:
        """One failed work item back to the queue, with its attempts reset; a
        write, refused with `work_not_failed` for an item that is not failed."""
        body = await self.request("POST", f"/v1/admin/orgs/{org_id}/work/{item_id}/requeue")
        return OperatorWorkItemView.model_validate(body)
