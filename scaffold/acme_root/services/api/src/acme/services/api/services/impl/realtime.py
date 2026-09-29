import itertools
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal
from uuid import UUID

from acme.infra.topics import EntityChangedPayload, TopicPayload, Topics, TopicsInterface
from acme.om.base import utcnow
from acme.om.events import EventsManagerInterface
from acme.om.exceptions import NotAuthenticated, ValidationFailed
from acme.om.opcontext import ActorScope, OpContext
from acme.om.tenancy import TenancyManagerInterface
from acme.om.tenancy.types.socket_ticket import SocketPrincipal
from acme.services.api.realtime.envelopes import EventEnvelope, IssuedTicketView
from acme.services.api.services.realtime import (
    CREDENTIAL_REVOKED,
    MEMBERSHIP_ENDED,
    RIGHTS_CHANGED,
    RealtimeServiceInterface,
)
from acme.services.api.types.events import EntityChangedView

PROJECTIONS: dict[Topics, Callable[[TopicPayload], EntityChangedView]] = {
    Topics.ENTITY_CHANGED: EntityChangedView.model_validate,
}
"""The topics the channel carries, each with the view its payload is projected
onto before a frame is offered. A topic outside this map never reaches a client."""

REVOCATIONS: dict[str, tuple[Literal["credential", "user", "membership", "org"], str]] = {
    "tenancy.session.revoked": ("credential", CREDENTIAL_REVOKED),
    "tenancy.api_key.deleted": ("credential", CREDENTIAL_REVOKED),
    "tenancy.user.deleted": ("user", MEMBERSHIP_ENDED),
    "tenancy.membership.updated": ("membership", RIGHTS_CHANGED),
    "tenancy.org.deleted": ("org", MEMBERSHIP_ENDED),
}
"""The change kinds that end a socket: which id of the socket the target
names (the credential behind its ticket, its user, the membership its
context was built from, or its org, whose deletion ends every membership in
it) and the close reason. A membership's change is a change of its role, so
the socket closes to be opened again under the role the member has now."""


@dataclass(frozen=True)
class AttachedSocket:
    org_id: UUID
    user_id: UUID
    credential_id: UUID
    membership_id: UUID
    end: Callable[[str], None]


@dataclass(frozen=True)
class KnownHead:
    """The highest `seq` this process knows for a tenant, and when it last
    learned it: from a hint on the bus, or from a read."""

    seq: int
    confirmed_at: float  # the process's monotonic clock


class RealtimeServiceImpl(RealtimeServiceInterface):
    """`head_max_age` bounds how long a head this process heard may answer a
    ping without a read; zero reads on every ping. `clock` is the monotonic
    clock that age is measured on, injected by tests."""

    def __init__(
        self,
        tenancy: TenancyManagerInterface,
        events: EventsManagerInterface,
        topics: TopicsInterface,
        head_max_age: timedelta = timedelta(seconds=60),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._tenancy = tenancy
        self._events = events
        self._topics = topics
        self._head_max_age = head_max_age.total_seconds()
        self._clock = clock
        self._ids = itertools.count()
        self._sockets: dict[int, AttachedSocket] = {}
        # The heads of the tenants this process holds a socket for, and no
        # other: an entry goes when the tenant's last socket here detaches.
        self._heads: dict[UUID, KnownHead] = {}
        self._tenants: Counter[UUID] = Counter()
        # One subscription per process: every replica hears every revocation
        # and ends the sockets it holds for it, as it does for every push, and
        # keeps the head of every tenant it holds a socket for.
        self._topics.subscribe(Topics.ENTITY_CHANGED, "socket-revocations", self._on_change)

    async def head(self, ctx: OpContext) -> int:
        seq = await self._events.get_head(ctx)
        self._learn(ctx.org_id, seq)
        return seq

    async def pong_head(self, ctx: OpContext) -> int:
        known = self._heads.get(ctx.org_id)
        if known is not None and self._clock() - known.confirmed_at < self._head_max_age:
            return known.seq
        return await self.head(ctx)

    def _learn(self, org_id: UUID, seq: int) -> None:
        """Keeps `seq` as the tenant's head when it is at least the one known,
        and restarts its age. A `seq` below the one known (a hint that
        arrived late, a read that started before a hint landed) changes
        nothing: it proves nothing about what came after the head."""
        if org_id not in self._tenants:
            return
        known = self._heads.get(org_id)
        if known is None or seq >= known.seq:
            self._heads[org_id] = KnownHead(seq=seq, confirmed_at=self._clock())

    async def recheck(self, principal: SocketPrincipal) -> str | None:
        ctx = principal.ctx
        # The socket is a request that stays open: its recheck runs under the
        # request stage of the handshake that opened it, which its context
        # carries.
        try:
            current = await self._tenancy.resume(
                ctx, ctx.org_id, principal.credential_kind, ctx.credential_id, record_use=False
            )
        except NotAuthenticated as refused:
            return refused.code
        if current.ctx.security != ctx.security:
            return RIGHTS_CHANGED
        return None

    async def issue_ticket(self, ctx: OpContext) -> IssuedTicketView:
        issued = await self._tenancy.issue_ticket(ctx)
        remaining = int((issued.expires_at - utcnow()).total_seconds())
        return IssuedTicketView(ticket=issued.ticket, expires_in_seconds=max(remaining, 0))

    def subscribe(
        self, ctx: ActorScope, topic: Topics, deliver: Callable[[EventEnvelope], None]
    ) -> Callable[[], None]:
        project = PROJECTIONS.get(topic)
        if project is None:
            raise ValidationFailed(f"topic {topic.value!r} is not carried on the channel")

        async def forward(payload: TopicPayload) -> None:
            if payload.org_id == ctx.org_id:
                deliver(EventEnvelope(topic=topic.value, payload=project(payload)))

        return self._topics.subscribe(topic, f"socket:{ctx.user_id}", forward)

    def attach(self, principal: SocketPrincipal, end: Callable[[str], None]) -> Callable[[], None]:
        ctx = principal.ctx
        socket_id = next(self._ids)
        self._sockets[socket_id] = AttachedSocket(
            org_id=ctx.org_id,
            user_id=ctx.user_id,
            credential_id=ctx.credential_id,
            membership_id=principal.membership_id,
            end=end,
        )
        self._tenants[ctx.org_id] += 1

        def detach() -> None:
            if self._sockets.pop(socket_id, None) is None:
                return
            self._tenants[ctx.org_id] -= 1
            if self._tenants[ctx.org_id] <= 0:
                del self._tenants[ctx.org_id]
                self._heads.pop(ctx.org_id, None)

        return detach

    async def _on_change(self, payload: TopicPayload) -> None:
        if not isinstance(payload, EntityChangedPayload):
            return
        self._learn(payload.org_id, payload.seq)
        revocation = REVOCATIONS.get(payload.kind)
        if revocation is None:
            return
        subject, reason = revocation
        for attached in list(self._sockets.values()):
            if attached.org_id != payload.org_id:
                continue
            named = {
                "credential": attached.credential_id,
                "user": attached.user_id,
                "membership": attached.membership_id,
                "org": attached.org_id,
            }[subject]
            if named == payload.target_id:
                attached.end(reason)
