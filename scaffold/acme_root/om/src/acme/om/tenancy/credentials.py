"""The credentials duty of the tenancy manager: the sessions a person holds
in an org, and the org's API keys."""

from abc import ABC, abstractmethod
from datetime import timedelta
from uuid import UUID

from acme.om.context import Role, TenantContext
from acme.om.idempotency.types.attempt import Attempt
from acme.om.tenancy.types.api_key import ApiKey
from acme.om.tenancy.types.issued import IssuedApiKey
from acme.om.tenancy.types.page import ApiKeyPage
from acme.om.tenancy.types.session import Session


class TenancyCredentialsManagerInterface(ABC):
    """A delegate of `TenancyManagerInterface`, reached as
    `tenancy.credentials`. Every operation takes `TenantContext`."""

    @abstractmethod
    async def get_sessions(self, ctx: TenantContext, limit: int) -> list[Session]:
        """The caller's own live sessions in this org, newest first."""
        ...

    @abstractmethod
    async def revoke_session(self, ctx: TenantContext, session_id: UUID) -> Session: ...

    @abstractmethod
    async def get_api_keys(self, ctx: TenantContext, after: UUID | None, limit: int) -> ApiKeyPage:
        """The tenant's unrevoked keys for a member manager, the caller's own
        otherwise; newest first, a page at a time, as
        `TenancyMembersManagerInterface.get_users` pages."""
        ...

    @abstractmethod
    async def create_api_key(
        self,
        ctx: TenantContext,
        name: str,
        role: Role,
        ttl: timedelta | None = None,
        attempt: Attempt | None = None,
    ) -> IssuedApiKey:
        """Role-capped at the caller's role; the service role is refused by name.
        `attempt`, when given, is the attempt a retried request runs under: the
        id it creates on and the token of the idempotency marker holding it. A
        key that already exists under that id is the rerun of a create that
        issues a secret: the secret is re-minted on that row in the same write
        and a fresh `IssuedApiKey` with the same id comes back, since the first
        secret reached no one; the old secret stops authenticating. The re-mint
        lands only while the marker still holds the token, so an attempt that
        lost the marker to a retry cannot invalidate the key that retry already
        returned. Without an attempt the id is fresh and there is no rerun."""
        ...

    @abstractmethod
    async def revoke_api_key(self, ctx: TenantContext, api_key_id: UUID) -> ApiKey: ...
