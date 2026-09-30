import logging
from collections.abc import Mapping
from datetime import timedelta
from typing import Any
from uuid import UUID

from acme.om.base import new_id, utcnow
from acme.om.context import CredentialKind, Permission, Role, TenantContext
from acme.om.exceptions import NotAuthorized, NotFound, ValidationFailed
from acme.om.idempotency.types.attempt import Attempt
from acme.om.outbox import OutboxRelayInterface
from acme.om.outbox.types.row import OutboxRow, outbox_row
from acme.om.tenancy.credentials import TenancyCredentialsManagerInterface
from acme.om.tenancy.impl.manager import TenancyOptions
from acme.om.tenancy.impl.shared import clamp, mint_token, session_payload
from acme.om.tenancy.rules import hash_token, role_at_most
from acme.om.tenancy.storage import TenancyStorageInterface
from acme.om.tenancy.types.api_key import ApiKey
from acme.om.tenancy.types.issued import IssuedApiKey
from acme.om.tenancy.types.page import ApiKeyPage
from acme.om.tenancy.types.session import Session

log = logging.getLogger(__name__)


class TenancyCredentialsManagerImpl(TenancyCredentialsManagerInterface):
    def __init__(
        self,
        storage: TenancyStorageInterface,
        relay: OutboxRelayInterface,
        options: TenancyOptions,
    ) -> None:
        self._storage = storage
        self._relay = relay
        self._options = options

    async def get_sessions(self, ctx: TenantContext, limit: int) -> list[Session]:
        ctx.require(Permission.READ)
        # Live at the storage: a page of dead sessions cannot hide a live one.
        return await self._storage.read_sessions(
            ctx.org_id, ctx.user_id, utcnow(), clamp(limit, self._options.max_limit)
        )

    async def revoke_session(self, ctx: TenantContext, session_id: UUID) -> Session:
        ctx.require(Permission.READ)
        session = await self._storage.read_session(ctx.org_id, session_id)
        if session is None or session.revoked_at is not None:
            raise NotFound(f"session {session_id} not found")
        if session.user_id != ctx.user_id and not ctx.has(Permission.MANAGE_MEMBERS):
            raise NotAuthorized("only the owner of a session or a member manager may revoke it")
        now = utcnow()
        revoked = session.model_copy(
            update={"revoked_at": now, "updated_at": now, "updated_by": ctx.user_id}
        )
        # Announced like any change: the socket this session opened, in
        # whichever process holds it, closes on the row the relay publishes.
        await self._write_session(ctx, revoked, "revoked")
        return revoked

    async def get_api_keys(self, ctx: TenantContext, after: UUID | None, limit: int) -> ApiKeyPage:
        ctx.require(Permission.MANAGE_KEYS)
        # A member manager sees the tenant's keys; anyone else their own, filtered
        # at the storage so a page of other people's keys cannot hide theirs.
        own_only = None if ctx.has(Permission.MANAGE_MEMBERS) else ctx.user_id
        limit = clamp(limit, self._options.max_limit)
        # One row past the page, kept out of it: `has_more` is then a fact
        # about the rows, so no key is left unreachable behind a fixed limit.
        rows = await self._storage.read_api_keys(ctx.org_id, after, limit + 1, own_only)
        return ApiKeyPage(items=tuple(rows[:limit]), has_more=len(rows) > limit)

    async def create_api_key(
        self,
        ctx: TenantContext,
        name: str,
        role: Role,
        ttl: timedelta | None = None,
        attempt: Attempt | None = None,
    ) -> IssuedApiKey:
        ctx.require(Permission.MANAGE_KEYS)
        # A key never mints its successor. Revoking a leaked key has to end the
        # access it gave; a key that can issue another one outlives its own
        # revocation, and nothing ties the successor back to it. The sign-in
        # delegate's `logout` gates on the credential kind for the same reason.
        if ctx.security.credential_kind is CredentialKind.API_KEY:
            raise NotAuthorized("an api key cannot create another; sign in to create one")
        if role is Role.SERVICE:
            raise ValidationFailed("service is not an api key role")
        if not role_at_most(role, ctx.security.role):
            raise NotAuthorized(f"cannot issue role {role.value} above {ctx.security.role.value}")
        if ttl is not None and not (timedelta(0) < ttl <= self._options.api_key_ttl):
            raise ValidationFailed(
                f"an api key lives between one second and {self._options.api_key_ttl.days} days"
            )
        now = utcnow()
        key = mint_token(CredentialKind.API_KEY)
        api_key = ApiKey(
            id=attempt.target_id if attempt else new_id(),
            name=name,
            created_at=now,
            updated_at=now,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
            user_id=ctx.user_id,
            key_hash=hash_token(key),
            role=role,
            expires_at=now + (ttl or self._options.api_key_ttl),
        )
        # A create that issues a secret: the row as stored is not enough on a
        # rerun, because the secret is a digest there and was shown to no one
        # (the marker stored no outcome). The one storage method inserts the
        # key, or re-mints the secret on the row the id already names, and the
        # re-mint lands only while the marker still holds this attempt.
        row = outbox_row(ctx, "tenancy.api_key.created", api_key.id, self._key_payload(api_key))
        stored, created = await self._storage.issue_api_key(
            ctx.org_id, api_key, (row,), attempt.attempt_id if attempt else None
        )
        if created:
            await self._relay.relay(ctx.org_id, row)
        return IssuedApiKey(key=key, api_key=stored)

    async def revoke_api_key(self, ctx: TenantContext, api_key_id: UUID) -> ApiKey:
        ctx.require(Permission.MANAGE_KEYS)
        api_key = await self._storage.read_api_key(ctx.org_id, api_key_id)
        if api_key is None or api_key.deleted_at is not None:
            raise NotFound(f"api key {api_key_id} not found")
        if api_key.user_id != ctx.user_id and not ctx.has(Permission.MANAGE_MEMBERS):
            raise NotAuthorized("only the owner of a key or a member manager may revoke it")
        now = utcnow()
        revoked = api_key.model_copy(
            update={
                "deleted_at": now,
                "deleted_by": ctx.user_id,
                "updated_at": now,
                "updated_by": ctx.user_id,
            }
        )
        await self._write_api_key(ctx, revoked, "deleted")
        return revoked

    # The core row and its outbox row land in one storage call; the relay then
    # appends the event and pushes at once, and the sweep catches what a crash
    # left behind.

    def _session_row(self, ctx: TenantContext, session: Session, action: str) -> OutboxRow:
        return outbox_row(ctx, f"tenancy.session.{action}", session.id, session_payload(session))

    async def _write_session(self, ctx: TenantContext, session: Session, action: str) -> None:
        row = self._session_row(ctx, session, action)
        await self._storage.write_session(ctx.org_id, session, (row,))
        await self._relay.relay(ctx.org_id, row)

    @staticmethod
    def _key_payload(api_key: ApiKey) -> Mapping[str, Any]:
        # Ids only, and never the hash; the event is a record, not a credential.
        return {"user_id": str(api_key.user_id)}

    async def _write_api_key(self, ctx: TenantContext, api_key: ApiKey, action: str) -> None:
        row = outbox_row(ctx, f"tenancy.api_key.{action}", api_key.id, self._key_payload(api_key))
        await self._storage.write_api_key(ctx.org_id, api_key, (row,))
        await self._relay.relay(ctx.org_id, row)
