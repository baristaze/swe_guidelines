"""Slack routes: the org's installation, the start of an install, and the
uninstall. Each function is one call into the slack service."""

from fastapi import APIRouter, Response

from acme.services.api.gateway.auth import Ctx
from acme.services.api.gateway.idempotency import Idem
from acme.services.api.gateway.resolve import SlackService
from acme.services.api.types.slack import SlackInstallStartView, SlackStatusView

router = APIRouter(prefix="/slack", tags=["slack"])


@router.get("/installation", response_model=SlackStatusView)
async def get_installation(ctx: Ctx, slack: SlackService) -> SlackStatusView:
    return await slack.get_status(ctx)


# Slack's page for the org's workspace, with a one-time state in it, shown in
# the first response alone: a replay under the same Idempotency-Key answers
# with `url` null, and the caller asks for another.
@router.post("/installation", response_model=SlackInstallStartView, status_code=201)
async def start_install(ctx: Ctx, slack: SlackService, idem: Idem) -> Response:
    return await idem.run(201, lambda _: slack.start_install(ctx))


@router.delete("/installation", response_model=SlackStatusView)
async def uninstall(ctx: Ctx, slack: SlackService) -> SlackStatusView:
    return await slack.uninstall(ctx)
