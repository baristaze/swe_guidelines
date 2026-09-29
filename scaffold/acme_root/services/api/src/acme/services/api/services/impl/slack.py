import logging
from urllib.parse import urlencode

from acme.infra.exceptions import InfraException, InfraUnavailable
from acme.infra.observability import OUTCOMES
from acme.infra.queues import Queues, QueuesInterface
from acme.integrations.slack import SlackError, SlackInterface
from acme.integrations.slack.requests import (
    command_of,
    event_of,
    inbound_command,
    inbound_event,
)
from acme.om.base import utcnow
from acme.om.exceptions import NotFound, SlackWorkspaceTaken
from acme.om.opcontext import OpContext, RequestContext
from acme.om.slack import SlackManagerInterface
from acme.services.api.services.slack import SlackRequest, SlackServiceInterface
from acme.services.api.types.slack import (
    SlackEventAnswerView,
    SlackInstallationView,
    SlackInstallStartView,
    SlackStatusView,
)

log = logging.getLogger(__name__)

SETTINGS_PATH = "/settings"


class SlackServiceImpl(SlackServiceInterface):
    """The org's installation over the slack manager, and the edge of Slack's
    calls in: the check, then the queue. What a call means is the worker's to
    do, under the org its workspace belongs to."""

    def __init__(
        self,
        slack: SlackManagerInterface,
        client: SlackInterface,
        queues: QueuesInterface,
        redirect_uri: str,
        portal_url: str,
    ) -> None:
        self._slack = slack
        self._client = client
        self._queues = queues
        self._redirect_uri = redirect_uri
        self._portal_url = portal_url.rstrip("/")

    async def get_status(self, ctx: OpContext) -> SlackStatusView:
        installation = await self._slack.get_installation(ctx)
        view = None if installation is None else SlackInstallationView.model_validate(installation)
        return SlackStatusView(installation=view)

    async def start_install(self, ctx: OpContext) -> SlackInstallStartView:
        start = await self._slack.start_install(ctx, self._redirect_uri)
        return SlackInstallStartView(url=start.url, expires_at=start.expires_at)

    async def uninstall(self, ctx: OpContext) -> SlackStatusView:
        await self._slack.uninstall(ctx)
        return SlackStatusView(installation=None)

    async def finish_install(
        self, rctx: RequestContext, code: str | None, state: str | None, error: str | None
    ) -> str:
        outcome = await self._finish(rctx, code, state, error)
        OUTCOMES.labels(subsystem="slack_install", outcome=outcome).inc()
        return f"{self._portal_url}{SETTINGS_PATH}?{urlencode({'slack': outcome})}"

    async def _finish(
        self, rctx: RequestContext, code: str | None, state: str | None, error: str | None
    ) -> str:
        if not state:
            return "expired"
        if error or not code:
            # The person clicked Cancel on Slack's page. The state stays until
            # it expires; without a code from Slack it installs nothing.
            return "cancelled"
        try:
            await self._slack.finish_install(rctx, state, code, self._redirect_uri)
        except SlackWorkspaceTaken:
            return "taken"
        except NotFound:
            return "expired"
        except SlackError as failed:
            log.warning("slack install failed at Slack: %s", failed.slack_code)
            return "failed"
        except InfraException as unanswered:
            # Translated by its code: the token's store did not answer by the
            # request's deadline, and the person reads that the install
            # failed, and installs again.
            if unanswered.code != InfraUnavailable.code:
                raise
            log.warning("slack install could not keep its token: %s", unanswered.message)
            return "failed"
        return "installed"

    async def receive_command(self, rctx: RequestContext, request: SlackRequest) -> None:
        self._client.verify_request(request.payload, request.timestamp, request.signature)
        delivery = inbound_command(command_of(request.payload), utcnow(), request.retry_num)
        await self._queues.send(
            Queues.SLACK, delivery.model_dump_json().encode(), deadline=rctx.deadline
        )
        OUTCOMES.labels(subsystem="slack_inbound", outcome="queued").inc()

    async def receive_event(
        self, rctx: RequestContext, request: SlackRequest
    ) -> SlackEventAnswerView:
        self._client.verify_request(request.payload, request.timestamp, request.signature)
        payload = event_of(request.payload)
        if payload["type"] == "url_verification":
            return SlackEventAnswerView(challenge=str(payload.get("challenge", "")))
        if payload["type"] != "event_callback":
            log.info("slack call of type %s acknowledged and ignored", payload["type"])
            return SlackEventAnswerView()
        delivery = inbound_event(payload, utcnow(), request.retry_num)
        await self._queues.send(
            Queues.SLACK, delivery.model_dump_json().encode(), deadline=rctx.deadline
        )
        OUTCOMES.labels(subsystem="slack_inbound", outcome="queued").inc()
        return SlackEventAnswerView()
