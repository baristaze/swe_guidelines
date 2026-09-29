"""The realtime service: mints the ticket that opens the channel, forwards
the tenant's pushes on it as typed envelopes, ends the sockets a revocation
or a change of rights names, re-checks a socket's credential when asked, and
answers a ping with the tenant's head. The gateway redeems the ticket; the
socket handler moves frames, asks for the recheck on its interval, and
closes when told to."""

from abc import ABC, abstractmethod
from collections.abc import Callable

from acme.infra.topics import Topics
from acme.om.opcontext import ActorScope, OpContext
from acme.om.tenancy.types.socket_ticket import SocketPrincipal
from acme.services.api.realtime.envelopes import EventEnvelope, IssuedTicketView

CREDENTIAL_REVOKED = "credential_revoked"
"""The close reason when the session or the api key behind the socket was revoked."""

MEMBERSHIP_ENDED = "membership_ended"
"""The close reason when the user behind the socket was removed from the tenant."""

RIGHTS_CHANGED = "rights_changed"
"""The close reason when the role or the teams behind the socket changed. The
credential still holds, so this close asks the client to reconnect: its new
ticket is redeemed under the rights the member has now."""


class RealtimeServiceInterface(ABC):
    @abstractmethod
    async def issue_ticket(self, ctx: OpContext) -> IssuedTicketView: ...

    @abstractmethod
    async def head(self, ctx: OpContext) -> int:
        """The tenant's stream position when a socket opens, read from the
        database; the hello carries it so a client can replay from there
        before it has seen any push."""
        ...

    @abstractmethod
    async def pong_head(self, ctx: OpContext) -> int:
        """The head a pong carries: the highest `seq` this process has heard
        on the bus or read for the tenant, while it was confirmed within the
        bound the settings name, and else a fresh read, as `head`. Never
        above the truth, since a `seq` is published only once it committed.
        Below it only when the bus lost a hint after the last one heard, and
        then for no longer than the bound."""
        ...

    @abstractmethod
    async def recheck(self, principal: SocketPrincipal) -> str | None:
        """Asks again whether the socket's authority holds, through the same
        check the redemption made, without counting the question as a use of
        a session. Returns None when it holds as it did, `RIGHTS_CHANGED`
        when the credential holds and the role or the teams it grants
        changed, and the refusal's code when the credential no longer holds.
        A failure to ask (the database out of reach) raises."""
        ...

    @abstractmethod
    def subscribe(
        self, ctx: ActorScope, topic: Topics, deliver: Callable[[EventEnvelope], None]
    ) -> Callable[[], None]:
        """Hands `deliver` every push on `topic` for the caller's tenant, projected
        onto its view; returns the unsubscribe callable. Raises ValidationFailed
        for a topic the channel does not carry. Reads the tenant and the user
        and nothing else; `head` and `issue_ticket` keep `OpContext` because
        the managers behind them authorize."""
        ...

    @abstractmethod
    def attach(self, principal: SocketPrincipal, end: Callable[[str], None]) -> Callable[[], None]:
        """Registers an open socket under the principal its ticket produced.
        `end` is called, on the event loop, with the close reason when the
        session or the api key behind the socket is revoked, the user's
        membership ends, or the membership's role changes, in whichever
        process the change happened: the service hears every change on the
        bus. Returns the detach callable."""
        ...
