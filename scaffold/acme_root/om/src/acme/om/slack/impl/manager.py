import json
import logging
from datetime import datetime, timedelta
from uuid import UUID

from pydantic import SecretStr

from acme.infra.exceptions import InfraException
from acme.infra.secrets import SecretNotFound, SecretsInterface
from acme.integrations.slack import (
    UNUSABLE_CHANNEL_ERRORS,
    SlackError,
    SlackFailed,
    SlackGrant,
    SlackInterface,
    SlackTokenRevoked,
    SlackTokens,
)
from acme.om.base import Platform, new_id, utcnow
from acme.om.exceptions import NotFound, SlackWorkspaceTaken, UniqueKeyTaken
from acme.om.opcontext import OpContext, Permission, RequestContext
from acme.om.outbox import OutboxRelayInterface
from acme.om.outbox.types.row import outbox_row
from acme.om.slack.manager import SlackManagerInterface
from acme.om.slack.rules import credential_ref_for, new_state, state_digest
from acme.om.slack.storage import SlackStorageInterface
from acme.om.slack.types.installation import (
    SlackInstallation,
    SlackInstallationStatus,
    SlackInstallStart,
    SlackInstallState,
    SlackPost,
)
from acme.om.tenancy import TenancyManagerInterface

log = logging.getLogger(__name__)


class SlackOptions(Platform):
    state_lifetime: timedelta = timedelta(minutes=10)
    # A token is renewed this long before it expires, so a caller that finds
    # a renewal already running still holds a token that works.
    refresh_margin: timedelta = timedelta(hours=1)
    # How long one renewal holds its claim; one that died is taken over after.
    refresh_claim: timedelta = timedelta(seconds=30)
    retention: timedelta = timedelta(days=30)
    purge_batch: int = 1000  # rows of each kind one purge deletes at most


def tokens_json(tokens: SlackTokens) -> str:
    """The secret's value: the bot token, what renews it, and when it expires."""
    return json.dumps(
        {
            "access_token": tokens.access_token.get_secret_value(),
            "refresh_token": None
            if tokens.refresh_token is None
            else tokens.refresh_token.get_secret_value(),
            "expires_at": None if tokens.expires_at is None else tokens.expires_at.isoformat(),
        }
    )


def tokens_from(value: str) -> SlackTokens:
    stored = json.loads(value)
    expires = stored.get("expires_at")
    refresh = stored.get("refresh_token")
    return SlackTokens(
        access_token=SecretStr(str(stored["access_token"])),
        refresh_token=SecretStr(str(refresh)) if refresh else None,
        expires_at=None if not expires else datetime.fromisoformat(expires),
    )


class SlackManagerImpl(SlackManagerInterface):
    def __init__(
        self,
        storage: SlackStorageInterface,
        tenancy: TenancyManagerInterface,
        relay: OutboxRelayInterface,
        slack: SlackInterface,
        secrets: SecretsInterface,
        options: SlackOptions,
    ) -> None:
        self._storage = storage
        self._tenancy = tenancy
        self._relay = relay
        self._slack = slack
        self._secrets = secrets
        self._options = options

    async def get_installation(self, ctx: OpContext) -> SlackInstallation | None:
        ctx.require(Permission.READ)
        return await self._storage.read_installation(ctx.org_id)

    async def start_install(self, ctx: OpContext, redirect_uri: str) -> SlackInstallStart:
        ctx.require(Permission.MANAGE_MEMBERS)
        now = utcnow()
        state = new_state()
        expires_at = now + self._options.state_lifetime
        # The page first: a process whose Slack app is not configured refuses
        # here, before a state is kept for an install that cannot finish.
        url = self._slack.authorize_url(state, redirect_uri)
        await self._storage.create_install_state(
            ctx.org_id,
            SlackInstallState(
                id=new_id(),
                created_at=now,
                user_id=ctx.user_id,
                state_hash=state_digest(state),
                expires_at=expires_at,
            ),
        )
        return SlackInstallStart(url=url, expires_at=expires_at)

    async def finish_install(
        self, rctx: RequestContext, state: str, code: str, redirect_uri: str
    ) -> SlackInstallation:
        redeemed = await self._storage.redeem_install_state(state_digest(state), utcnow())
        if redeemed is None:
            raise NotFound("that install is unknown, used, or expired")
        org_id, started = redeemed
        ctx = await self._tenancy.service_context(rctx, org_id, started.user_id)
        grant = await self._slack.exchange_code(code, redirect_uri, deadline=ctx.deadline)
        held = await self._storage.read_installation_by_team(grant.team_id)
        if held is not None and held[0] != org_id:
            await self._revoke_quietly(ctx, grant.tokens)
            raise SlackWorkspaceTaken("this Slack workspace is installed for another org")
        current = await self._storage.read_installation(org_id)
        if current is not None and current.team_id != grant.team_id:
            # Another workspace replaces the org's one: the app leaves it.
            await self._remove(ctx, current, uninstall=True)
            current = None
        installation = self._installed(ctx, grant, current)
        await self._secrets.put(
            org_id, installation.credential_ref, tokens_json(grant.tokens), deadline=ctx.deadline
        )
        try:
            await self._write(ctx, installation, "created" if current is None else "updated")
        except UniqueKeyTaken:
            # A race the read above did not see: another org took the workspace.
            if current is None:
                await self._secrets.delete(
                    org_id, installation.credential_ref, deadline=ctx.deadline
                )
            await self._revoke_quietly(ctx, grant.tokens)
            raise SlackWorkspaceTaken("this Slack workspace is installed for another org") from None
        return installation

    def _installed(
        self, ctx: OpContext, grant: SlackGrant, current: SlackInstallation | None
    ) -> SlackInstallation:
        """The installation a grant makes: a new one, or the org's own in the
        same workspace installed again (new scopes, a new token), which keeps
        its channel and is well again."""
        now = utcnow()
        fields = {
            "team_id": grant.team_id,
            "team_name": grant.team_name,
            "app_id": grant.app_id,
            "bot_user_id": grant.bot_user_id,
            "scopes": ",".join(grant.scopes),
            "installed_by_slack_user": grant.installer_user_id,
            "token_expires_at": grant.tokens.expires_at,
            "refreshing_until": None,
            "status": SlackInstallationStatus.OK,
            "broken_reason": None,
            "updated_at": now,
            "updated_by": ctx.user_id,
        }
        if current is not None:
            return current.model_copy(update=fields)
        installation_id = new_id()
        return SlackInstallation.model_validate(
            {
                **fields,
                "id": installation_id,
                "created_at": now,
                "created_by": ctx.user_id,
                "credential_ref": credential_ref_for(installation_id),
            }
        )

    async def uninstall(self, ctx: OpContext) -> SlackInstallation | None:
        ctx.require(Permission.MANAGE_MEMBERS)
        current = await self._storage.read_installation(ctx.org_id)
        if current is None:
            return None
        return await self._remove(ctx, current, uninstall=True)

    async def forget(self, ctx: OpContext, reason: str) -> SlackInstallation | None:
        ctx.require(Permission.WRITE)
        current = await self._storage.read_installation(ctx.org_id)
        if current is None:
            return None
        log.info("slack installation of org %s is gone at Slack: %s", ctx.org_id, reason)
        return await self._remove(ctx, current, uninstall=False)

    async def _remove(
        self, ctx: OpContext, current: SlackInstallation, *, uninstall: bool
    ) -> SlackInstallation:
        """The app leaves the workspace (when Slack does not know yet), the
        token goes from the org's secrets, and the row is deleted, announced.
        Slack's side is best effort: a Slack that cannot be reached leaves the
        app there, and a person removes it in Slack; Acme holds no token for
        it either way."""
        if uninstall:
            try:
                tokens = tokens_from(
                    await self._secrets.get(
                        ctx.org_id, current.credential_ref, deadline=ctx.deadline
                    )
                )
                await self._slack.uninstall(
                    tokens.access_token.get_secret_value(), deadline=ctx.deadline
                )
            except SlackError as error:
                log.warning("slack app of org %s was not removed at Slack: %s", ctx.org_id, error)
            except InfraException as error:
                if error.code != SecretNotFound.code:
                    raise
                # No token: nothing to remove the app with.
        await self._secrets.delete(ctx.org_id, current.credential_ref, deadline=ctx.deadline)
        now = utcnow()
        deleted = current.model_copy(
            update={
                "deleted_at": now,
                "deleted_by": ctx.user_id,
                "updated_at": now,
                "updated_by": ctx.user_id,
            }
        )
        await self._write(ctx, deleted, "deleted")
        return deleted

    async def installation_for_team(
        self, rctx: RequestContext, team_id: str
    ) -> tuple[OpContext, SlackInstallation] | None:
        held = await self._storage.read_installation_by_team(team_id)
        if held is None:
            return None
        org_id, installation = held
        ctx = await self._tenancy.service_context(rctx, org_id, installation.created_by)
        return ctx, installation

    async def bot_token(self, ctx: OpContext) -> str:
        ctx.require(Permission.READ)
        installation = await self._storage.read_installation(ctx.org_id)
        if installation is None:
            raise NotFound("the org has no Slack installation")
        try:
            tokens = tokens_from(
                await self._secrets.get(
                    ctx.org_id, installation.credential_ref, deadline=ctx.deadline
                )
            )
        except InfraException as error:
            # Translated by its code: a token that is gone breaks the install.
            if error.code != SecretNotFound.code:
                raise
            await self.mark_broken(ctx, "token_missing")
            raise SlackTokenRevoked("token_missing") from None
        now = utcnow()
        expires = tokens.expires_at
        if expires is None or tokens.refresh_token is None:
            return tokens.access_token.get_secret_value()  # a token that never expires
        if expires - self._options.refresh_margin > now:
            return tokens.access_token.get_secret_value()
        claimed = await self._storage.claim_refresh(
            ctx.org_id, installation.id, now, now + self._options.refresh_claim
        )
        if not claimed:
            # Another caller renews it. The margin keeps this token working
            # meanwhile; one already expired waits for the renewal.
            if expires > now:
                return tokens.access_token.get_secret_value()
            raise SlackFailed("token_renewing", "the Slack token is being renewed")
        # A renewal that settled between the read above and the claim left a
        # fresh pair: it is read again, and a refresh token already spent is
        # never presented.
        tokens = tokens_from(
            await self._secrets.get(ctx.org_id, installation.credential_ref, deadline=ctx.deadline)
        )
        if tokens.refresh_token is None or (
            tokens.expires_at is not None and tokens.expires_at - self._options.refresh_margin > now
        ):
            await self._storage.settle_refresh(
                ctx.org_id, installation.id, tokens.expires_at, utcnow()
            )
            return tokens.access_token.get_secret_value()
        try:
            fresh = await self._slack.refresh(tokens.refresh_token.get_secret_value())
        except SlackTokenRevoked as revoked:
            await self._storage.settle_refresh(ctx.org_id, installation.id, expires, utcnow())
            await self.mark_broken(ctx, revoked.slack_code)
            raise
        except SlackError:
            await self._storage.settle_refresh(ctx.org_id, installation.id, expires, utcnow())
            raise
        # The refresh token just used stops working after Slack's grace
        # period, so the new pair is kept before anything else happens.
        await self._secrets.put(
            ctx.org_id, installation.credential_ref, tokens_json(fresh), deadline=ctx.deadline
        )
        await self._storage.settle_refresh(ctx.org_id, installation.id, fresh.expires_at, utcnow())
        return fresh.access_token.get_secret_value()

    async def bind_channel(self, ctx: OpContext, channel_id: str) -> SlackInstallation:
        ctx.require(Permission.MANAGE_MEMBERS)
        current = await self._storage.read_installation(ctx.org_id)
        if current is None:
            raise NotFound("the org has no Slack installation")
        bound = current.model_copy(
            update={
                "channel_id": channel_id,
                "status": SlackInstallationStatus.OK,
                "broken_reason": None,
                "updated_at": utcnow(),
                "updated_by": ctx.user_id,
            }
        )
        await self._write(ctx, bound, "updated")
        return bound

    async def mark_broken(self, ctx: OpContext, reason: str) -> SlackInstallation | None:
        ctx.require(Permission.WRITE)
        current = await self._storage.read_installation(ctx.org_id)
        if current is None:
            return None
        if current.status is SlackInstallationStatus.BROKEN and current.broken_reason == reason:
            return current
        broken = current.model_copy(
            update={
                "status": SlackInstallationStatus.BROKEN,
                "broken_reason": reason,
                "updated_at": utcnow(),
                "updated_by": ctx.user_id,
            }
        )
        await self._write(ctx, broken, "updated")
        return broken

    async def bot_joined(self, ctx: OpContext, channel_id: str) -> SlackInstallation | None:
        ctx.require(Permission.WRITE)
        current = await self._storage.read_installation(ctx.org_id)
        if (
            current is None
            or current.channel_id != channel_id
            or current.status is not SlackInstallationStatus.BROKEN
            or current.broken_reason not in UNUSABLE_CHANNEL_ERRORS
        ):
            return None
        log.info("slack bot of org %s is back in its channel: %s", ctx.org_id, channel_id)
        mended = current.model_copy(
            update={
                "status": SlackInstallationStatus.OK,
                "broken_reason": None,
                "updated_at": utcnow(),
                "updated_by": ctx.user_id,
            }
        )
        await self._write(ctx, mended, "updated")
        return mended

    async def read_post(self, ctx: OpContext, key: UUID) -> SlackPost | None:
        ctx.require(Permission.READ)
        return await self._storage.read_post(ctx.org_id, key)

    async def record_post(self, ctx: OpContext, key: UUID, channel_id: str, ts: str) -> SlackPost:
        ctx.require(Permission.WRITE)
        post = SlackPost(id=new_id(), created_at=utcnow(), key=key, channel_id=channel_id, ts=ts)
        if await self._storage.create_post(ctx.org_id, post):
            return post
        existing = await self._storage.read_post(ctx.org_id, key)
        if existing is None:
            raise NotFound(f"slack post {key} vanished while it was recorded")
        return existing

    async def purge_across_tenants(self) -> int:
        return await self._storage.purge(
            utcnow() - self._options.retention, self._options.purge_batch
        )

    async def purge_tenant(self, ctx: OpContext) -> int:
        ctx.require(Permission.WRITE)
        if not await self._tenancy.tenant_expired(ctx):
            return 0
        return await self._storage.purge_tenant(ctx.org_id, self._options.purge_batch)

    async def _revoke_quietly(self, ctx: OpContext, tokens: SlackTokens) -> None:
        try:
            await self._slack.revoke(tokens.access_token.get_secret_value(), deadline=ctx.deadline)
        except SlackError as error:
            log.warning("a Slack token the platform does not keep was not revoked: %s", error)

    async def _write(self, ctx: OpContext, installation: SlackInstallation, action: str) -> None:
        """The installation and the row that announces it, in one commit; the
        portal's settings hear it and read the installation again. Ids only."""
        rows = (outbox_row(ctx, f"slack.installation.{action}", installation.id, {}),)
        await self._storage.write_installation(ctx.org_id, installation, rows)
        await self._relay.relay_all(ctx.org_id, rows)
