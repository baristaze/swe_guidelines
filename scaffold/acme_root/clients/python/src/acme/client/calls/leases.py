"""The leases namespace's calls: a leasable resource's wait line, and the
holder's lease. The holder's own side, its clock and its fence, is
`acme.client.leases`."""

from typing import Any
from uuid import UUID, uuid4

from acme.client.transport import LIMIT_MAX, Transport
from acme.client.types.leases import (
    LeasePageView,
    LeaseRequestView,
    LeaseView,
    StandingView,
    WaitLineView,
)


class LeasesCalls(Transport):
    """Asking for a leasable resource, its wait line, and the lease."""

    async def ask_lease(
        self,
        *,
        kind: str = "noop",
        leasable_resource_id: UUID | None = None,
        labels: list[str] | None = None,
        payload: dict[str, Any] | None = None,
        term_seconds: int = 60,
        start_seconds: int | None = None,
        wait_seconds: int = 3600,
        idempotency_key: str | None = None,
    ) -> StandingView:
        """Joins the line for one resource, or for any of `kind` with every
        label; granted at once only when no one waits in front. Always under
        an idempotency key, so a retry joins no line twice. `start_seconds`
        is the window a job the grant starts has to start in."""
        body: dict[str, Any] = {
            "kind": kind,
            "payload": payload or {},
            "term_seconds": term_seconds,
            "wait_seconds": wait_seconds,
        }
        if start_seconds is not None:
            body["start_seconds"] = start_seconds
        if leasable_resource_id is not None:
            body["leasable_resource_id"] = str(leasable_resource_id)
        if labels is not None:
            body["labels"] = labels
        asked = await self.request(
            "POST",
            "/v1/leases/requests",
            json=body,
            idempotency_key=idempotency_key or str(uuid4()),
        )
        return StandingView.model_validate(asked)

    async def lease_standing(self, request_id: UUID) -> StandingView:
        """Where a request stands now: its lease, or its place and estimate."""
        standing = await self.request("GET", f"/v1/leases/requests/{request_id}")
        return StandingView.model_validate(standing)

    async def cancel_lease_request(self, request_id: UUID) -> LeaseRequestView:
        cancelled = await self.request("POST", f"/v1/leases/requests/{request_id}/cancel")
        return LeaseRequestView.model_validate(cancelled)

    async def reorder_lease_request(
        self, request_id: UUID, before_id: UUID | None = None
    ) -> LeaseRequestView:
        """A manager's: the request moves in front of `before_id`, or to the end."""
        body = {"before_id": None if before_id is None else str(before_id)}
        moved = await self.request("POST", f"/v1/leases/requests/{request_id}/reorder", json=body)
        return LeaseRequestView.model_validate(moved)

    async def leasable_resource_wait_line(self, leasable_resource_id: UUID) -> WaitLineView:
        """A leasable resource's wait line, first first, with each place and
        estimate."""
        line = await self.request(
            "GET", f"/v1/leases/leasable-resources/{leasable_resource_id}/wait-line"
        )
        return WaitLineView.model_validate(line)

    async def lease_history(
        self,
        leasable_resource_id: UUID | None = None,
        *,
        cursor: str | None = None,
        limit: int = LIMIT_MAX,
    ) -> LeasePageView:
        """One page of the org's leases, or one resource's, newest first,
        ended ones too, each with the request it answered; `cursor` is the
        previous page's `next_cursor`."""
        params: dict[str, Any] = {"limit": limit}
        if leasable_resource_id is not None:
            params["leasable_resource_id"] = str(leasable_resource_id)
        if cursor:
            params["cursor"] = cursor
        page = await self.request("GET", "/v1/leases", params=params)
        return LeasePageView.model_validate(page)

    async def lease(self, lease_id: UUID) -> LeaseView:
        return LeaseView.model_validate(await self.request("GET", f"/v1/leases/{lease_id}"))

    async def renew_lease(self, lease_id: UUID, seconds: int | None = None) -> LeaseView:
        """The holder's: the lease runs `seconds` from now, or its term again,
        within the resource's bound. A `lease_ended` refusal means the lease
        is gone, and the holder stops."""
        body = None if seconds is None else {"seconds": seconds}
        renewed = await self.request("POST", f"/v1/leases/{lease_id}/renew", json=body)
        return LeaseView.model_validate(renewed)

    async def release_lease(self, lease_id: UUID) -> LeaseView:
        released = await self.request("POST", f"/v1/leases/{lease_id}/release")
        return LeaseView.model_validate(released)

    async def revoke_lease(self, lease_id: UUID) -> LeaseView:
        """A manager's: the lease ends and the resource goes to its line."""
        revoked = await self.request("POST", f"/v1/leases/{lease_id}/revoke")
        return LeaseView.model_validate(revoked)
