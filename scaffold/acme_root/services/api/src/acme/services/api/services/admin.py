"""The operator service: takes OperatorContext and never a TenantContext. A
read of one tenant names it by id; the views are the tenant's own."""

from abc import ABC, abstractmethod
from uuid import UUID

from acme.om.context import OperatorContext
from acme.om.idempotency.types.attempt import Attempt
from acme.services.api.types.admin import (
    AddMemberRequest,
    ConfirmTotpRequest,
    CreateOrgRequest,
    IssuedOperatorTokenView,
    IssuedTotpSecretView,
    MintOperatorTokenRequest,
    OperatorTokenPageView,
    OperatorTokenView,
    OperatorView,
    OperatorWorkItemView,
    PlatformSizeView,
    SetTenantCapRequest,
    TenantCapView,
    TotpConfirmedView,
)
from acme.services.api.types.events import OperatorEventView
from acme.services.api.types.tenancy import OrgPageView, OrgView, UserPageView, UserView


class AdminServiceInterface(ABC):
    @abstractmethod
    async def get_orgs(self, admin: OperatorContext, cursor: str | None, limit: int) -> OrgPageView:
        """One page of every org; `cursor` is the previous page's `next_cursor`."""
        ...

    @abstractmethod
    async def me(self, admin: OperatorContext) -> OperatorView: ...

    @abstractmethod
    async def enrol_totp(self, admin: OperatorContext) -> IssuedTotpSecretView: ...

    @abstractmethod
    async def confirm_totp(
        self, admin: OperatorContext, body: ConfirmTotpRequest
    ) -> TotpConfirmedView: ...

    @abstractmethod
    async def mint_token(
        self, admin: OperatorContext, body: MintOperatorTokenRequest
    ) -> IssuedOperatorTokenView: ...

    @abstractmethod
    async def list_tokens(
        self, admin: OperatorContext, cursor: str | None, limit: int
    ) -> OperatorTokenPageView:
        """One page of the caller's live tokens; `cursor` is the previous
        page's `next_cursor`."""
        ...

    @abstractmethod
    async def revoke_token(self, admin: OperatorContext, token_id: UUID) -> OperatorTokenView: ...

    @abstractmethod
    async def size(self, admin: OperatorContext) -> PlatformSizeView: ...

    @abstractmethod
    async def create_org(
        self, admin: OperatorContext, body: CreateOrgRequest, attempt: Attempt
    ) -> OrgView: ...

    @abstractmethod
    async def get_org(self, admin: OperatorContext, org_id: UUID) -> OrgView: ...

    @abstractmethod
    async def get_members(
        self, admin: OperatorContext, org_id: UUID, cursor: str | None, limit: int
    ) -> UserPageView: ...

    @abstractmethod
    async def add_member(
        self, admin: OperatorContext, org_id: UUID, body: AddMemberRequest, attempt: Attempt
    ) -> UserView: ...

    @abstractmethod
    async def get_events(
        self, admin: OperatorContext, org_id: UUID, after_seq: int, limit: int
    ) -> list[OperatorEventView]: ...

    @abstractmethod
    async def delete_org(self, admin: OperatorContext, org_id: UUID) -> OrgView: ...

    @abstractmethod
    async def requeue_work(
        self, admin: OperatorContext, org_id: UUID, item_id: UUID
    ) -> OperatorWorkItemView: ...

    @abstractmethod
    async def set_tenant_cap(
        self, admin: OperatorContext, org_id: UUID, lane: str, body: SetTenantCapRequest
    ) -> TenantCapView: ...

    @abstractmethod
    async def get_tenant_cap(
        self, admin: OperatorContext, org_id: UUID, lane: str
    ) -> TenantCapView: ...

    @abstractmethod
    async def clear_tenant_cap(
        self, admin: OperatorContext, org_id: UUID, lane: str
    ) -> TenantCapView: ...
