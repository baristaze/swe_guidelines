"""Wire types of the work queue: what an operator reads and sets on an org's
work items and its own cap on a lane, under the tenant named in the path."""

from datetime import datetime
from uuid import UUID

from pydantic import Field

from acme.om.work.types.tenant_cap import MAX_CAP
from acme.om.work.types.work_item import KindName, WorkStatus
from acme.services.api.types.common import RequestBody, View


class OperatorWorkItemView(View):
    """One background job as an operator reads it: what it does and for
    which record, where it stands, and how many of its attempts are spent.
    The payload and the claim stay the worker's."""

    id: UUID
    kind: KindName
    target_id: UUID
    status: WorkStatus
    available_at: datetime
    attempts: int
    max_attempts: int
    last_error: str | None
    updated_at: datetime


class SetTenantCapRequest(RequestBody):
    """An org's own cap on a lane: the most items it holds claimed there at
    once, in place of the lane's cap."""

    cap: int = Field(ge=1, le=MAX_CAP)


class TenantCapView(View):
    """An org's own cap on a lane, with who last set it and when."""

    lane: str
    cap: int
    updated_at: datetime
    updated_by: UUID
