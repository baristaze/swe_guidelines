"""The slack service: what the wire can do with the org's Slack installation,
and the end of an install, where Slack sends the browser back."""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from acme.om.opcontext import OpContext, RequestContext
from acme.services.api.types.slack import (
    SlackEventAnswerView,
    SlackInstallStartView,
    SlackStatusView,
)


@dataclass(frozen=True)
class SlackRequest:
    """A call in from Slack as it arrived: its body byte for byte, which is
    what the signature is over, the two headers the signature is made of, and
    which retry of Slack's it is."""

    payload: bytes
    timestamp: str | None
    signature: str | None
    retry_num: int


class SlackServiceInterface(ABC):
    @abstractmethod
    async def get_status(self, ctx: OpContext) -> SlackStatusView: ...

    @abstractmethod
    async def start_install(self, ctx: OpContext) -> SlackInstallStartView: ...

    @abstractmethod
    async def uninstall(self, ctx: OpContext) -> SlackStatusView: ...

    @abstractmethod
    async def finish_install(
        self, rctx: RequestContext, code: str | None, state: str | None, error: str | None
    ) -> str:
        """Where the browser goes once Slack sent it back: the portal's
        settings, saying how the install went. Never raises: a person reads
        the outcome there, not an error body."""
        ...

    @abstractmethod
    async def receive_command(self, rctx: RequestContext, request: SlackRequest) -> None:
        """Checks the signature over the body and its timestamp and queues the
        command; one that fails the check is refused before anything is
        queued. Answers within Slack's three seconds: the work is a worker's.
        The send to the queue ends by the request's deadline."""
        ...

    @abstractmethod
    async def receive_event(
        self, rctx: RequestContext, request: SlackRequest
    ) -> SlackEventAnswerView:
        """Checks the signature, then answers Slack's check of the URL with its
        challenge, or queues the event, by the request's deadline."""
        ...
