from uuid import UUID

from pydantic import ValidationError

from acme.om.base import utcnow
from acme.om.context import TenantContext
from acme.om.exceptions import ValidationFailed
from acme.om.leases import LeasesManagerInterface
from acme.om.leases.types.lease import Lease
from acme.om.leases.types.request import LeaseEntry, LeaseRequest, Standing
from acme.om.leases.types.resource import Resource
from acme.services.api.services.leases import LeasesServiceInterface
from acme.services.api.types.common import clamp_limit
from acme.services.api.types.leases import (
    AskRequest,
    LeaseEntryView,
    LeaseRequestView,
    LeaseView,
    LineView,
    PlaceView,
    RenewRequest,
    ReorderRequest,
    ResourceView,
    StandingView,
)


def lease_view(lease: Lease) -> LeaseView:
    left = max(0.0, (lease.expires_at - utcnow()).total_seconds())
    return LeaseView.model_validate(
        {**lease.model_dump(), "fencing_token": lease.token, "expires_in_seconds": left}
    )


def resource_view(resource: Resource) -> ResourceView:
    return ResourceView.model_validate({**resource.model_dump(), "fencing_token": resource.token})


def request_view(request: LeaseRequest) -> LeaseRequestView:
    return LeaseRequestView.model_validate(request)


def entry_view(entry: LeaseEntry) -> LeaseEntryView:
    return LeaseEntryView(
        lease=lease_view(entry.lease),
        request=None if entry.request is None else request_view(entry.request),
    )


def standing_view(standing: Standing) -> StandingView:
    return StandingView(
        request=request_view(standing.request),
        lease=None if standing.lease is None else lease_view(standing.lease),
        place=standing.place,
        estimate_seconds=standing.estimate_seconds,
    )


class LeasesServiceImpl(LeasesServiceInterface):
    def __init__(self, leases: LeasesManagerInterface) -> None:
        self._leases = leases

    async def ask(self, ctx: TenantContext, body: AskRequest, request_id: UUID) -> StandingView:
        now = utcnow()
        try:
            request = LeaseRequest(
                id=request_id,
                created_at=now,
                updated_at=now,
                created_by=ctx.user_id,
                updated_by=ctx.user_id,
                idempotency_key=request_id,
                kind=body.kind,
                resource_id=body.resource_id,
                labels=None if body.labels is None else tuple(body.labels),
                payload=body.payload,
                term_seconds=body.term_seconds,
                start_seconds=body.start_seconds,
                wait_seconds=body.wait_seconds,
            )
        except ValidationError as error:
            raise ValidationFailed(f"an ask is not {error}") from None
        return standing_view(await self._leases.ask(ctx, request))

    async def get_request(self, ctx: TenantContext, request_id: UUID) -> StandingView:
        return standing_view(await self._leases.get_request(ctx, request_id))

    async def cancel(self, ctx: TenantContext, request_id: UUID) -> LeaseRequestView:
        return request_view(await self._leases.cancel(ctx, request_id))

    async def reorder(
        self, ctx: TenantContext, request_id: UUID, body: ReorderRequest
    ) -> LeaseRequestView:
        return request_view(await self._leases.reorder(ctx, request_id, body.before_id))

    async def line(self, ctx: TenantContext, resource_id: UUID) -> LineView:
        line = await self._leases.line(ctx, resource_id)
        return LineView(
            resource=resource_view(line.resource),
            requests=[request_view(r) for r in line.requests],
            places=[PlaceView.model_validate(p) for p in line.places],
        )

    async def get_lease(self, ctx: TenantContext, lease_id: UUID) -> LeaseView:
        return lease_view(await self._leases.get_lease(ctx, lease_id))

    async def list_leases(
        self, ctx: TenantContext, resource_id: UUID | None, limit: int
    ) -> list[LeaseEntryView]:
        entries = await self._leases.list_leases(ctx, resource_id, clamp_limit(limit))
        return [entry_view(e) for e in entries]

    async def renew(
        self, ctx: TenantContext, lease_id: UUID, body: RenewRequest | None
    ) -> LeaseView:
        seconds = None if body is None else body.seconds
        return lease_view(await self._leases.renew(ctx, lease_id, seconds))

    async def release(self, ctx: TenantContext, lease_id: UUID) -> LeaseView:
        return lease_view(await self._leases.release(ctx, lease_id))

    async def revoke(self, ctx: TenantContext, lease_id: UUID) -> LeaseView:
        return lease_view(await self._leases.revoke(ctx, lease_id))
