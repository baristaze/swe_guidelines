"""The change_events namespace's calls: the stream after a sequence number,
the live channel's ticket, and an org's stream on the operator plane."""

from typing import Any, cast
from uuid import UUID

from acme.client.transport import LIMIT_MAX, Transport
from acme.client.types.change_events import (
    ChangeEventView,
    IssuedTicketView,
    OperatorChangeEventView,
)


class ChangeEventsCalls(Transport):
    """The stream, the channel's ticket, and an org's stream for an operator."""

    async def change_events_after(
        self, after_seq: int, limit: int = LIMIT_MAX
    ) -> list[ChangeEventView]:
        body = await self.request(
            "GET", "/v1/change-events", params={"after_seq": after_seq, "limit": limit}
        )
        return [ChangeEventView.model_validate(e) for e in cast(list[Any], body)]

    async def ticket(self) -> IssuedTicketView:
        return IssuedTicketView.model_validate(await self.request("POST", "/v1/realtime/tickets"))

    async def admin_change_events(
        self, org_id: UUID, after_seq: int = 0, limit: int = LIMIT_MAX
    ) -> list[OperatorChangeEventView]:
        body = await self.request(
            "GET",
            f"/v1/admin/orgs/{org_id}/change-events",
            params={"after_seq": after_seq, "limit": limit},
        )
        return [OperatorChangeEventView.model_validate(e) for e in cast(list[Any], body)]
